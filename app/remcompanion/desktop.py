"""RemChat 桌面程序：宠物、主聊天窗与悬浮气泡的统一入口。"""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass, replace
from pathlib import Path
import os
import sys
import threading
import queue
import tkinter as tk

from .activation import ActivationListener, signal_existing_instance
from .audio_player import EphemeralAudioPlayer, cleanup_stale_dialogue_audio
from .chat_window import ChatWindow
from .backend_processes import read_process_identity, stop_owned_process
from .companion_preferences import (
    load_desktop_preferences,
    save_desktop_preferences,
)
from .config import load_config, AppConfig
from .downloader import close_active_downloads
from .dialogue import DialogueResult, DialogueService
from .overlay import CompanionOverlay, acquire_single_instance, enable_dpi_awareness
from .pet_window import PetWindow
from .platforms import native_module
from .gptsovits import _tts_api_python
from .linux_desktop import configure_fonts
from .ui import theme
from .ui.activity_tracker import AppActivityTracker
from .voice_setup_dialog import VoiceEnableGate


_LOG_STREAM = None


@dataclass(frozen=True)
class _NotificationAudio:
    path: Path
    passive: bool
    activity_serial: int


def _kill_backend_servers(config: AppConfig) -> None:
    """只终止 PID、实际 exe、出生时间都匹配的本项目后端。"""

    pid_files = {
        "llama-server": (config.root / "data" / "llm-server.pid", config.llama_server, "llama-server"),
        "TTS API": (config.root / "data" / "tts-api.pid", _tts_api_python(config), "tts-api"),
    }
    for label, (pid_file, executable, owner_key) in pid_files.items():
        if not pid_file.is_file():
            continue
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            continue
        try:
            identity = read_process_identity(config.root, owner_key, pid, executable)
            if stop_owned_process(pid, executable, expected_identity=identity):
                print(f"[退出] 已关闭 {label} (PID {pid})")
                for path in (pid_file, config.root / "data" / "backend-ownership" / f"{owner_key}.json"):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
            else:
                print(f"[退出] {label} (PID {pid}) 无法确认完整退出（不存在、身份不匹配或部分回收）；保留记录")
        except (OSError, ProcessLookupError):
            print(f"[退出] {label} (PID {pid}) 身份无法确认，保留进程与记录")


def _configure_desktop_log(root: Path) -> None:
    """把 windowed EXE 的标准输出写入工作目录，便于定位后台启动故障。"""

    global _LOG_STREAM
    log_path = root / "logs" / "desktop-app.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _LOG_STREAM = log_path.open("a", encoding="utf-8", buffering=1)
    sys.stdout = _LOG_STREAM
    sys.stderr = _LOG_STREAM
    print(f"\n===== desktop start {datetime.now().isoformat(timespec='seconds')} =====")


def main() -> None:
    mutex = acquire_single_instance()
    if mutex is None:
        # 旧版在此处静默退出，所以用户会感觉“双击没反应”。新版通知首个
        # 实例把三个窗口重新显示出来，然后再结束本次短进程。
        signal_existing_instance()
        return

    enable_dpi_awareness()
    config = load_config()
    os.environ.setdefault("REM_HOME", str(config.root))
    _configure_desktop_log(config.root)
    try:
        certificate_file = native_module("network").configure_tls()
        if certificate_file:
            print(f"[网络] 使用系统 CA 证书：{certificate_file}")
    except OSError as error:
        print(f"[网络] 证书初始化失败，离线功能仍可用：{error}")
    try:
        if configure_fonts(config.root):
            print("[字体] 使用项目本地 Noto CJK 字体配置")
    except OSError as error:
        print(f"[字体] 本地字体配置暂不可用，使用系统字体: {error}")
    preferences = load_desktop_preferences(config.root)
    theme.set_mode(preferences.theme_mode)

    removed = cleanup_stale_dialogue_audio(config.root)
    print(f"[语音] 启动时清理 {removed} 个遗留对话音频")

    root = tk.Tk()
    if sys.platform=='linux':native_module('windowing').configure_tk_scaling(root)
    root.withdraw()           # 配置完成后由 PetWindow 显示透明宠物根窗口
    main_root = tk.Toplevel(root)
    main_root.withdraw()      # 配置完成前隐藏，避免启动时闪出空白框
    state: dict[str, object] = {}

    # 三个窗口共享对话服务、切换按钮和播放器。这样既不会重复占用模型内存，
    # 也不会让同一段语音同时播放两次。
    service = DialogueService(config, log=print)
    speech_mode = tk.StringVar(
        master=root,
        value=config.companion.default_speech_mode,
    )
    audio_enabled = tk.BooleanVar(
        master=root,
        value=False,  # Playback is user-started; first use prepares voice files.
    )
    overlay_visible = tk.BooleanVar(master=root, value=preferences.overlay_visible)
    task_rings_visible = tk.BooleanVar(master=root, value=preferences.task_rings_visible)
    pet_scale = tk.DoubleVar(master=root, value=preferences.pet_scale)
    background_fade = tk.DoubleVar(
        master=root,
        value=preferences.background_fade,
    )
    chat_font_size = tk.IntVar(
        master=root,
        value=preferences.chat_font_size,
    )
    audio_player = EphemeralAudioPlayer(log=print)
    notification_events: queue.Queue[tuple[str, object]] = queue.Queue()
    notification_lock = threading.Lock()

    def on_main_request(active: bool, source: str) -> None:
        """请求锁与宠物动作阶段分开；语音准备时仍占用会话，但不再思考。"""
        overlay = state.get("overlay")
        if overlay is None:
            return
        try:
            overlay.set_request_state(active, source=source)  # type: ignore[union-attr]
        except tk.TclError as error:
            print(f"[窗口同步] 气泡状态更新失败: {error}")

    def on_overlay_request(active: bool, source: str) -> None:
        chat_window = state.get("chat")
        if isinstance(chat_window, ChatWindow):
            chat_window.set_request_state(active, source)

    def show_main_progress(text: str) -> None:
        overlay_window = state.get("overlay")
        if isinstance(overlay_window, CompanionOverlay):
            overlay_window.show_partial_reply(text)

    def show_main_error(text: str) -> None:
        overlay_window = state.get("overlay")
        if isinstance(overlay_window, CompanionOverlay):
            overlay_window.show_error(text)

    def clear_overlay_conversation() -> None:
        overlay_window = state.get("overlay")
        if isinstance(overlay_window, CompanionOverlay):
            overlay_window.clear_reply()

    def notify_companion(text: str, *, passive: bool = False) -> None:
        if state.get("closing"):
            return
        tracker = state.get("activity")
        activity_serial = tracker.serial if isinstance(tracker, AppActivityTracker) else -1
        chat_window = state.get("chat")
        ui_busy = isinstance(chat_window, ChatWindow) and chat_window.busy
        if passive and (ui_busy or service.is_busy or audio_player.is_playing):
            return
        overlay_window = state.get("overlay")
        if isinstance(overlay_window, CompanionOverlay) and not overlay_window.busy:
            overlay_window.show_partial_reply(text)
        if not service.is_busy:
            pet.show_reply("gentle")
        if not audio_enabled.get():
            return
        mode = speech_mode.get()
        speech_text = text
        if mode == "ja":
            speech_text = (
                "作業が失敗したようです。ログを確認してみてください。" if "失败" in text
                else "作業が終わりました。結果を確認してみてくださいね。" if "完成" in text
                else "少し休んで、気分転換しましょう。そばにいますね。"
            )
        def worker() -> None:
            try:
                path = service.synthesize_notification(speech_text, speech_mode=mode)
            except Exception as error:
                with notification_lock:
                    if not state.get("closing"):
                        notification_events.put(("error", str(error)))
            else:
                with notification_lock:
                    if state.get("closing"):
                        try:
                            path.unlink(missing_ok=True)
                        except OSError as error:
                            print(f"[退出] 提醒语音暂未删除: {error}")
                    else:
                        notification_events.put(("audio", _NotificationAudio(path, passive, activity_serial)))
        threading.Thread(target=worker, name="remchat-notification", daemon=True).start()

    def drain_notifications() -> None:
        if state.get("closing"):
            return
        try:
            while True:
                kind, payload = notification_events.get_nowait()
                if kind == "audio":
                    tracker = state.get("activity")
                    serial = tracker.serial if isinstance(tracker, AppActivityTracker) else -1
                    chat_window = state.get("chat")
                    ui_busy = isinstance(chat_window, ChatWindow) and chat_window.busy
                    passive_disabled = False
                    if isinstance(chat_window, ChatWindow):
                        settings = chat_window.plaza_body.schedule.settings
                        passive_disabled = (
                            not settings.enabled or settings.is_quiet()
                            or chat_window._focus_is_active()
                        )
                    if payload.passive and (
                        payload.activity_serial != serial or ui_busy
                        or service.is_busy or audio_player.is_playing or passive_disabled
                    ):
                        try:
                            payload.path.unlink(missing_ok=True)
                        except OSError as error:
                            print(f"[提醒] 过期轻提醒语音暂未删除: {error}")
                    else:
                        audio_player.play(payload.path)
                else:
                    print(f"[提醒] 语音跳过: {payload}")
        except queue.Empty:
            pass
        root.after(120, drain_notifications)

    def save_preferences(**changes: object) -> bool:
        nonlocal preferences
        updated = replace(preferences, **changes)
        try:
            save_desktop_preferences(config.root, updated, changed_fields=tuple(changes))
        except (OSError, ValueError, UnicodeError) as error:
            print(f"[设置] 保存失败: {error}")
            return False
        preferences = updated
        return True

    def close_all() -> None:
        if state.get("closing"):
            return
        chat=state.get('chat')
        if isinstance(chat,ChatWindow) and not chat.plaza_body.flush_diary():
            chat.activate()
            return  # Keep the editor available to recover a failed save.
        state["closing"] = True
        with notification_lock:
            try:
                while True:
                    kind, payload = notification_events.get_nowait()
                    if kind == "audio":
                        try:
                            payload.path.unlink(missing_ok=True)
                        except OSError as error:
                            print(f"[退出] 提醒语音暂未删除: {error}")
            except queue.Empty:
                pass
        chat_window = state.get("chat")
        overlay_window = state.get("overlay")
        cleanups = [("组件安装", close_active_downloads),
                    ("对话服务", service.close), ("语音播放", audio_player.stop)]
        voice_gate = state.get("voice_gate")
        if isinstance(voice_gate, VoiceEnableGate):
            cleanups.insert(0, ("语音准备窗口", voice_gate.close))
        tracker = state.get("activity")
        if isinstance(tracker, AppActivityTracker):
            cleanups.insert(0, ("应用活动监听", tracker.close))
        if isinstance(chat_window, ChatWindow):
            cleanups.append(("主窗口", chat_window.close))
        task_rings = state.get("task_rings")
        if task_rings is not None:
            cleanups.insert(0, ("任务跟踪环", task_rings.close))
        if isinstance(overlay_window, CompanionOverlay):
            cleanups.append(("悬浮框", overlay_window.close))
        cleanups.append(("本地后端", lambda: _kill_backend_servers(config)))
        for label, cleanup in cleanups:
            try:
                cleanup()
            except Exception as error:
                print(f"[退出] {label}清理异常，继续关闭其他组件: {error}")

        def finalize_close() -> None:
            listener = state.get("activation")
            if isinstance(listener, ActivationListener):
                listener.close()
            try:
                if root.winfo_exists():
                    root.destroy()
            except tk.TclError:
                pass

        pet_window = state.get("pet")
        if isinstance(pet_window, PetWindow) and root.winfo_exists():
            pet_window.play_exit(finalize_close)
            # 即便动画素材异常，也不能让退出依赖单个动画回调。
            root.after(1500, finalize_close)
        else:
            finalize_close()

    def toggle_main_chat() -> None:
        chat = state.get("chat")
        if not isinstance(chat, ChatWindow) or not main_root.winfo_exists():
            return
        if main_root.state() == "withdrawn":
            chat.activate()
        else:
            chat.hide()

    def toggle_overlay() -> None:
        if not bubble_root.winfo_exists():
            return
        overlay_window = state.get("overlay")
        if not isinstance(overlay_window, CompanionOverlay):
            return
        if bubble_root.state() == "withdrawn":
            overlay_window.show()
            overlay_visible.set(True)
        else:
            overlay_window.hide()
            overlay_visible.set(False)
        save_preferences(overlay_visible=overlay_visible.get())

    def publish_exchange(
        source: str,
        user_text: str,
        result: DialogueResult,
    ) -> None:
        """把任一输入端产生的结果同步给另一个窗口。"""

        chat = state.get("chat")
        overlay = state.get("overlay")
        if source != "main" and isinstance(chat, ChatWindow):
            chat.show_external_exchange(user_text, result)
        if source != "overlay" and isinstance(overlay, CompanionOverlay):
            overlay.show_external_exchange(user_text, result)

    pet = PetWindow(
        root,
        on_exit=close_all,
        on_toggle_chat=toggle_main_chat,
        initial_scale=preferences.pet_scale,
        project_root=config.root,
        interaction_active=lambda: main_root.winfo_exists() and main_root.state() != 'withdrawn',
    )
    state["pet"] = pet
    print("[窗口] pet ready")

    def set_pet_scale(scale: float) -> None:
        pet.set_scale(scale)
        save_preferences(pet_scale=scale)

    def set_background_fade(value: int) -> None:
        """保存背景淡化量；聊天窗已经在保存前即时完成预览。"""

        normalized = min(100, max(0, round(value)))
        background_fade.set(normalized)
        save_preferences(background_fade=normalized)

    def set_chat_font_size(value: int) -> None:
        """保存主聊天字号；悬浮框不共享该变量，因此不会随之变化。"""

        from .companion_preferences import (
            MAX_CHAT_FONT_SIZE,
            MIN_CHAT_FONT_SIZE,
        )

        normalized = min(
            MAX_CHAT_FONT_SIZE,
            max(MIN_CHAT_FONT_SIZE, round(value)),
        )
        chat_font_size.set(normalized)
        save_preferences(chat_font_size=normalized)

    def persist_ring_display(visible: bool, hidden_groups: tuple[str, ...]) -> bool:
        saved = save_preferences(task_rings_visible=visible, hidden_task_ring_groups=hidden_groups)
        task_rings_visible.set(visible if saved else preferences.task_rings_visible)
        return saved

    def set_ring_display(visible: bool) -> None:
        rings = state.get("task_rings")
        if rings is not None:
            (rings.restore_all if visible else rings.hide_all)()

    def restore_task_rings() -> None:
        set_ring_display(True)

    def inspect_task_rings() -> None:
        rings = state.get("task_rings")
        if rings is not None:
            rings.show_tasks()

    def focus_curtain_visibility(active: bool) -> None:
        overlay = state.get("overlay")
        if not isinstance(overlay, CompanionOverlay):
            return
        if active:
            state["focus_overlay_visible"] = bool(overlay.root.winfo_viewable())
            overlay.hide()
        elif state.pop("focus_overlay_visible", False) and overlay_visible.get() and not state.get("closing"):
            overlay.show()

    def keep_focus_pet_visible() -> None:
        if not state.get("closing") and root.winfo_exists():
            root.deiconify()
            root.lift()  # raise the existing pet, never focus_force it every frame
        rings = state.get('task_rings')
        if rings is not None:
            rings.raise_for_focus()

    def change_interface_language(language: str) -> None:
        save_preferences(ui_language=language)
        gate = state.get('voice_gate')
        if gate is not None:
            gate.retranslate()

    chat = ChatWindow(
        main_root,
        service,
        speech_mode=speech_mode,
        audio_enabled=audio_enabled,
        overlay_visible=overlay_visible,
        pet_scale=pet_scale,
        background_fade=background_fade,
        chat_font_size=chat_font_size,
        audio_player=audio_player,
        on_exchange=publish_exchange,
        on_toggle_overlay=toggle_overlay,
        on_set_pet_scale=set_pet_scale,
        on_set_background_fade=set_background_fade,
        on_set_chat_font_size=set_chat_font_size,
        on_set_focus_mode=pet.set_focus_mode,
        on_pet_action=pet.play_action,
        on_pet_reply=pet.show_reply,
        on_pet_thinking=pet.set_thinking_mode,
        on_exit=close_all,
        on_reply_progress=show_main_progress,
        on_request_error=show_main_error,
        on_conversation_changed=clear_overlay_conversation,
        on_notification=notify_companion,
        on_passive_notification=lambda text: notify_companion(text, passive=True),
        on_request_state=on_main_request,
        on_reload_actions=pet.reload_actions,
        on_pet_preview=pet.preview_action,
        theme_mode=preferences.theme_mode,
        on_set_theme=lambda mode: save_preferences(theme_mode=mode),
        ui_language=preferences.ui_language,
        on_set_ui_language=change_interface_language,
        task_rings_visible=task_rings_visible,
        on_set_task_rings_visible=set_ring_display,
        on_restore_task_rings=restore_task_rings,
        on_inspect_task_rings=inspect_task_rings,
        on_focus_curtain=focus_curtain_visibility,
        on_keep_focus_pet_visible=keep_focus_pet_visible,
    )
    state["chat"] = chat
    root._remchat_translator = chat.translator
    print("[窗口] main chat ready")

    # Windows first: separate Toplevels keep pet and bubble sizes/interactions intact.
    if os.name == "nt":
        from .ui.task_rings import TaskRings

        def manage_tracked_tasks() -> None:
            chat.activate()
            chat._show_project_management()

        state["task_rings"] = TaskRings(
            root, chat.integration_manager, anchor_provider=pet.window_rect,
            language=lambda: chat.ui_language, on_manage=manage_tracked_tasks,
            visible=preferences.task_rings_visible, hidden_groups=preferences.hidden_task_ring_groups,
            on_display_change=persist_ring_display,
        )
        print("[窗口] task rings ready")

    # 主窗布局会刷新 idle 事件。气泡需在此后创建并立即配置，避免 Linux
    # 窗口系统先处理一个尚未配置的 1×1 Toplevel，留下错误的原生尺寸。
    bubble_root = tk.Toplevel(root)
    bubble_root.withdraw()
    overlay = CompanionOverlay(
        bubble_root,
        config,
        anchor_provider=pet.window_rect,
        tight_anchor=True,
        on_close=toggle_overlay,
        service=service,
        usage_ledger=chat.integration_manager if chat.integration_manager.usage_ledger is not None else None,
        speech_mode=speech_mode,
        audio_enabled=audio_enabled,
        audio_player=audio_player,
        on_exchange=publish_exchange,
        on_pet_action=pet.play_action,
        on_pet_reply=pet.show_reply,
        on_pet_thinking=pet.set_thinking_mode,
        on_request_state=on_overlay_request,
        on_collapsed=lambda collapsed: save_preferences(overlay_collapsed=collapsed),
        on_request_error=chat.show_external_error,
        on_request_started=chat.begin_external_exchange,
        on_reply_progress=chat.show_external_reply_progress,
    )
    state["overlay"] = overlay
    state["voice_gate"] = VoiceEnableGate(
        root, audio_enabled, config,
        parent=lambda: main_root if main_root.winfo_viewable() else bubble_root,
        language=lambda: preferences.ui_language,
        on_status=chat._set_status,
        on_change=lambda: (chat._on_shared_option_changed(), overlay._refresh_audio_button()),
    )
    state["activity"] = AppActivityTracker(root, chat.plaza_body.record_activity)
    overlay.set_collapsed(preferences.overlay_collapsed)
    print("[窗口] bubble ready")

    def activate_all(*, force_overlay: bool = True) -> None:
        """响应第二次双击：把宠物、主聊天和气泡全部带到前台。"""

        if not root.winfo_exists():
            return
        root.deiconify()
        root.lift()
        chat.activate()
        if force_overlay or preferences.overlay_visible:
            overlay.show()
            overlay_visible.set(True)
        else:
            overlay.hide()
            overlay_visible.set(False)
        print("[激活] 已响应重复启动并显示全部窗口")

    listener = ActivationListener(root, activate_all)
    state["activation"] = listener

    # 首次启动按用户预期直接显示三者，而不是只留一个后台宠物进程。
    activate_all(force_overlay=False)
    pet.trigger_action("waving")
    root.after(120, drain_notifications)
    root.mainloop()
    print("===== desktop exit =====")
    del mutex


if __name__ == "__main__":
    main()
