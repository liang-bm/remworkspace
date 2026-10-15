"""不依赖 Codex 的轻量雷姆宠物渲染窗口。"""

from __future__ import annotations

from collections.abc import Callable
from collections import OrderedDict
import json
import math
from pathlib import Path
import random
import time
import tkinter as tk
from .ui.localized_dialogs import messagebox

from .pet_tracker import WindowRect
from .pet_actions import PetActionLibrary
from .pet_motion import FrameTimeline, MOTION_HZ, RoamPlan, clamp_pet_position
from .platforms import native_module


TRANSPARENT = "#010203"
BASE_PET_WIDTH = 192
BASE_PET_HEIGHT = 208
SCALE_RATIOS = {
    0.75: (3, 4),
    1.0: (1, 1),
    1.25: (5, 4),
    1.5: (3, 2),
}
SCALE_RENDER_DIRS = {
    0.75: "075",
    1.0: "100",
    1.25: "125",
    1.5: "150",
}
ACTION_LABELS = {
    "waving": "挥手",
    "jumping": "跳跃",
    "waiting": "输出",
    "running": "工作中",
    "review": "思考",
    "failed": "难过",
    "happy": "开心",
    "little-angry": "假装生气",
}
DERIVED_ACTION_LABELS = {"dozing": "打盹", "waking": "唤醒",
                         "strolling-left": "慢慢向左走", "strolling-right": "慢慢向右走"}
DOZING_DURATIONS = (550, 600, 800, 650, 1500, 1400, 650, 800)
ACTION_DURATIONS = {
    "idle": (280, 110, 110, 140, 140, 320),
    "running-right": (120, 120, 120, 120, 120, 120, 120, 220),
    "running-left": (120, 120, 120, 120, 120, 120, 120, 220),
    # One hand lifts, waves at the wrist, then returns. No mirrored arm swap.
    "waving": (180, 120, 150, 150, 150, 120, 220),
    "jumping": (140, 140, 140, 140, 280),
    "failed": (140, 140, 140, 140, 140, 140, 140, 240),
    "waiting": (150, 150, 150, 150, 150, 260),
    "running": (120, 120, 120, 120, 120, 220),
    "review": (150, 150, 150, 150, 150, 280),
    # 第 4 帧是左右摇摆的换向点，稍作停顿能避免动作显得机械；
    # 第 9 帧回到中心后停留，再无缝衔接下一次 idle。
    "happy": (125, 125, 125, 125, 185, 125, 125, 125, 125, 250),
    "little-angry": (145, 145, 145, 145, 145, 145, 145, 145, 145, 270),
    "dozing": DOZING_DURATIONS,
    "waking": (180, 180, 360, 220, 380, 420, 280, 320),
    "strolling-left": (220, 180, 200, 220, 220, 180, 200, 220),
    "strolling-right": (220, 180, 200, 220, 220, 180, 200, 220),
}


def bundled_asset_path(*parts: str) -> Path:
    """定位源码目录或 PyInstaller 包内的静态资源。"""

    return Path(__file__).resolve().parent.joinpath("assets", *parts)


def reply_emotion_action(emotion: str) -> str | None:
    """把模型情绪映射成一次性动作；普通回答只播放“输出”动作。"""

    normalized = emotion.strip().lower().replace("-", "_")
    if normalized == "happy":
        return "happy"
    if normalized in {"little_angry", "angry"}:
        return "little-angry"
    if normalized in {"sad", "concern"}:
        return "failed"
    return None


class PetWindow:
    """播放标准宠物动作，并提供拖动、缩放、菜单和气泡锚点。"""

    def __init__(
        self,
        root: tk.Tk,
        *,
        on_exit: Callable[[], None],
        on_toggle_chat: Callable[[], None],
        initial_scale: float = 1.0,
        project_root: Path | None = None,
        interaction_active: Callable[[], bool] | None = None,
    ) -> None:
        self.root = root
        self.on_exit = on_exit
        self.on_toggle_chat = on_toggle_chat
        self._interaction_active = interaction_active
        self._drag_origin: tuple[int, int] | None = None
        self._press_origin = None
        self._press_was_sleeping = False
        self._frame_index = 0
        self._action = "idle"
        self._completion_callback: Callable[[], None] | None = None
        self._exiting = False
        self._focus_active = False
        self._thinking_active = False
        self._pointer_over_sprite = False
        self._deferred_reply: str | None = None
        self._animation_timer: str | None = None
        self._action_generation = 0
        self._presented_frame: tk.PhotoImage | None = None
        self._frame_cache: OrderedDict[float, dict[str, list[tk.PhotoImage]]] = OrderedDict()
        self._roam_plan: RoamPlan | None = None
        self._roam_direction: str | None = None
        self._idle_manual = False
        self._random = random.Random()
        self._idle_settings_path = None if project_root is None else Path(project_root) / "data" / "pet-idle-settings.json"
        self._idle_settings_error = ""
        self._idle_preferences = self._load_idle_preferences()
        self._idle_paused = tk.BooleanVar(master=root, value=self._idle_preferences["paused"])
        self._dozing_enabled = tk.BooleanVar(master=root, value=self._idle_preferences["dozing"])
        self._roaming_enabled = tk.BooleanVar(master=root, value=self._idle_preferences["roaming"])
        self._next_idle_at = time.perf_counter() + self._random.uniform(45, 90)
        self._motion_due = time.perf_counter()
        self.action_library = (
            PetActionLibrary(project_root, bundled_asset_path("pet"))
            if project_root is not None else None
        )
        self._action_timings: dict[str, tuple[int, ...]] = {}
        self.scale = (
            initial_scale if initial_scale in SCALE_RATIOS else 1.0
        )
        self._frames = self._load_frames(self.scale)
        self._timeline = self._new_timeline("idle")
        self.pet_width = round(BASE_PET_WIDTH * self.scale)
        self.pet_height = round(BASE_PET_HEIGHT * self.scale)

        root.title("RemChatPet")
        # Native window closes must use the same owned-resource cleanup as Exit.
        root.protocol("WM_DELETE_WINDOW", self.on_exit)
        root.geometry(f"{self.pet_width}x{self.pet_height}")
        root.resizable(False, False)
        root.overrideredirect(True)
        root.configure(bg=TRANSPARENT)
        root.attributes("-topmost", True)
        native_module("windowing").configure_pet_transparency(root, TRANSPARENT)

        self.canvas = tk.Canvas(
            root,
            width=self.pet_width,
            height=self.pet_height,
            bg=TRANSPARENT,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self.sprite = self.canvas.create_image(
            0,
            0,
            image=self._frames["idle"][0],
            anchor="nw",
        )
        self._bind_interactions(self.canvas)
        self._build_context_menu()
        self._place_initial()
        self._remove_windows_frame()
        self._silhouette = native_module("windowing").create_pet_silhouette(root)
        if self._silhouette is not None:
            self._silhouette.present(self._frames["idle"][0])
        self.root.bind("<Destroy>", self._on_destroy, add="+")
        self._schedule_animation(ACTION_DURATIONS["idle"][0])

    def _present_frame(self, frame: tk.PhotoImage) -> None:
        """Render and native silhouette use the exact same frame, never a guessed box."""
        if frame is self._presented_frame:
            return
        self.canvas.itemconfigure(self.sprite, image=frame)
        if self._silhouette is not None:
            self._silhouette.present(frame)
        self._presented_frame = frame

    def _remove_windows_frame(self) -> None:
        """Legacy method name; native styling lives in its platform directory."""
        native_module("windowing").remove_pet_frame(self.root)

    def _load_source_frames(self) -> dict[str, list[tk.PhotoImage]]:
        frame_sets: dict[str, list[tk.PhotoImage]] = {}
        idle_dir = bundled_asset_path("pet", "idle")
        idle_paths = sorted(idle_dir.glob("idle_*.png"))
        if not idle_paths:
            raise FileNotFoundError(f"独立宠物帧不存在: {idle_dir}")
        frame_sets["idle"] = [
            tk.PhotoImage(master=self.root, file=str(path))
            for path in idle_paths
        ]
        action_root = bundled_asset_path("pet", "actions")
        for action in ACTION_DURATIONS:
            if action == "idle":
                continue
            paths = sorted((action_root / action).glob(f"{action}_*.png"))
            if paths:
                frame_sets[action] = [
                    tk.PhotoImage(master=self.root, file=str(path))
                    for path in paths
                ]
        return frame_sets

    def _load_frames(self, scale: float) -> dict[str, list[tk.PhotoImage]]:
        """优先载入离线高质量缩放帧，缺失时才退回 Tk 兼容缩放。"""

        if scale in self._frame_cache:
            self._frame_cache.move_to_end(scale)
            return self._frame_cache[scale]

        rendered_root = bundled_asset_path(
            "pet",
            "rendered",
            SCALE_RENDER_DIRS[scale],
        )
        if rendered_root.is_dir():
            frame_sets: dict[str, list[tk.PhotoImage]] = {}
            for action in ACTION_DURATIONS:
                paths = sorted(
                    (rendered_root / action).glob(f"{action}_*.png")
                )
                if paths:
                    frame_sets[action] = [
                        tk.PhotoImage(master=self.root, file=str(path))
                        for path in paths
                    ]
            if "idle" in frame_sets:
                return self._cache_frames(self._apply_action_library(frame_sets, scale), scale)

        return self._cache_frames(self._apply_action_library(self._scaled_frames(self._load_source_frames(), scale), scale), scale)

    def _cache_frames(self, frames: dict[str, list[tk.PhotoImage]], scale: float) -> dict[str, list[tk.PhotoImage]]:
        # Missing authored art means no sleep action. Never manufacture it
        # from an idle blink: sleep and waking have their own drawn poses.
        self._frame_cache[scale] = frames
        # Two complete sizes bound retained Tk image memory. Other sizes reload
        # once; no disk decoding, scaling or image allocation on animation ticks.
        while len(self._frame_cache) > 2:
            self._frame_cache.popitem(last=False)
        return frames

    def _apply_action_library(self, frames: dict[str, list[tk.PhotoImage]], scale: float) -> dict[str, list[tk.PhotoImage]]:
        if self.action_library is None:
            return frames
        self._action_timings = {}
        for entry in self.action_library.entries():
            if entry.source == "custom":
                custom_frames = [tk.PhotoImage(master=self.root, file=str(path)) for path in entry.frames]
                frames[entry.action] = self._scaled_frames({entry.action: custom_frames}, scale)[entry.action]
            defaults = ACTION_DURATIONS.get(entry.action, DOZING_DURATIONS if entry.action == "dozing" else entry.durations_ms or (140,) * entry.frame_count)
            self._action_timings[entry.action] = self.action_library.durations(entry.action, defaults)
        return frames

    def reload_actions(self) -> None:
        """重读用户设置与导入动作，不修改内置帧，也不重新创建窗口。"""
        if self.action_library is None:
            return
        self.action_library = PetActionLibrary(self.action_library.root, bundled_asset_path("pet"))
        self._frame_cache.clear()
        self._stop_idle_motion()
        if self._silhouette is not None:
            self._silhouette.clear_cache()
        self._frames = self._load_frames(self.scale)
        if self._action not in self._frames:
            self._action = self._base_action()
        self._timeline = self._new_timeline(self._action)
        self._frame_index = 0
        self._present_frame(self._frames[self._action][self._frame_index])
        self._build_context_menu()
        self._schedule_animation(1)

    def _scaled_frames(
        self,
        frame_sets: dict[str, list[tk.PhotoImage]],
        scale: float,
    ) -> dict[str, list[tk.PhotoImage]]:
        numerator, denominator = SCALE_RATIOS[scale]
        if numerator == denominator:
            return {name: list(frames) for name, frames in frame_sets.items()}
        return {
            name: [
                frame.zoom(numerator, numerator).subsample(
                    denominator,
                    denominator,
                )
                for frame in frames
            ]
            for name, frames in frame_sets.items()
        }

    def available_actions(self) -> tuple[str, ...]:
        return tuple(
            action
            for action in self._frames
            if action != "idle"
        )

    def trigger_action(
        self,
        action: str,
        on_complete: Callable[[], None] | None = None,
        *, force: bool = False,
    ) -> None:
        """播放标准动作，并在一次性动作结束后回到当前基础状态。

        专注计时期间的基础状态是 ``running``，其余时间始终是 ``idle``。
        拖拽使用的左右移动状态不受一次性动作干扰。
        """

        if self._exiting:
            return
        if self._drag_origin is not None and action not in {"running-left", "running-right"}:
            return
        if action not in self._frames or (
            not force and self.action_library is not None and not self.action_library.is_enabled(action)
        ):
            if on_complete is not None:
                self.root.after_idle(on_complete)
            return
        if self._action == action and action in {"running-left", "running-right"}:
            return
        self._stop_idle_motion()
        self._defer_idle()
        self._begin_action(action, on_complete, manual_idle=force and action == "dozing")

    def _begin_action(self, action: str, on_complete: Callable[[], None] | None = None,
                      *, manual_idle: bool = False) -> None:
        """Begin at a fresh clock, independent of the prior action's timer."""
        self._action_generation += 1
        self._idle_manual = manual_idle
        self._action = action
        self._frame_index = 0
        self._completion_callback = on_complete
        self._timeline = self._new_timeline(action)
        self._present_frame(self._frames[action][0])
        self._schedule_animation(1)

    def play_action(self, action: str) -> None:
        """统一动作入口；跳跃按产品约定连续播放两个完整周期。"""

        if action == "jumping":
            self._play_repeated_action(action, repetitions=2)
        elif action == "dozing":
            self.preview_action(action)
        else:
            self.trigger_action(action)

    def preview_action(self, action: str) -> None:
        """用户手动预览可播放停用动作；自动情绪/悬停仍遵循启用设置。"""
        if action == "jumping":
            self.trigger_action(action, on_complete=lambda: self.trigger_action(action, force=True), force=True)
        else:
            self.trigger_action(action, force=True)

    def show_reply(self, emotion: str) -> None:
        """模型输出时先播放 waiting，再按回复情绪补一次表情动作。"""

        self._thinking_active = False
        if self._drag_origin is not None:
            self._deferred_reply = emotion
            return
        emotion_action = reply_emotion_action(emotion)
        callback = (
            None
            if emotion_action is None
            else lambda: self.trigger_action(emotion_action)
        )
        self.trigger_action("waiting", on_complete=callback)

    def set_thinking_mode(self, active: bool) -> None:
        """模型请求期间持续循环思考动作，直到回复或失败明确结束。"""

        self._thinking_active = active
        if self._drag_origin is not None:
            return
        if active:
            self.trigger_action("review")
        elif self._action == "review":
            self.trigger_action(self._base_action())

    def _base_action(self) -> str:
        """按优先级选择拖拽或一次性动作结束后的持续状态。"""

        if self._thinking_active:
            return "review"
        return "running" if self._focus_active else "idle"

    def set_focus_mode(self, active: bool) -> None:
        """让工作动作在番茄钟运行期间持续循环。"""

        self._focus_active = active
        if self._drag_origin is not None:
            return
        self.trigger_action(self._base_action())

    def play_exit(self, on_complete: Callable[[], None]) -> None:
        """退出前完整播放一次挥手动作，再交还销毁窗口的控制权。"""
        if self._exiting:
            return
        self._drag_origin = None
        self.trigger_action("waving", on_complete=on_complete, force=True)
        # 迟到的模型/专注提醒不能覆盖挥手的销毁回调。
        self._exiting = True

    def set_scale(self, scale: float) -> None:
        if scale not in SCALE_RATIOS or scale == self.scale:
            return
        old_x = self.root.winfo_x()
        old_y = self.root.winfo_y()
        old_width = self.pet_width
        old_height = self.pet_height
        self.scale = scale
        self._stop_idle_motion()
        if self._silhouette is not None:
            self._silhouette.clear_cache()
        self._frames = self._load_frames(scale)
        self.pet_width = round(BASE_PET_WIDTH * scale)
        self.pet_height = round(BASE_PET_HEIGHT * scale)
        # 以底部中心为锚点调整尺寸，避免雷姆突然从桌面位置跳走。
        x = old_x + (old_width - self.pet_width) // 2
        y = old_y + old_height - self.pet_height
        x, y = self._bounded_position(x, y)
        self.root.geometry(f"{self.pet_width}x{self.pet_height}+{x}+{y}")
        self.canvas.configure(
            width=self.pet_width,
            height=self.pet_height,
        )
        frames = self._frames.get(self._action, self._frames["idle"])
        self._frame_index %= len(frames)
        self._present_frame(frames[self._frame_index])
        self._timeline = self._new_timeline(self._action)
        self._frame_index = 0
        self._schedule_animation(1)

    def _bind_interactions(self, widget: tk.Widget) -> None:
        widget.bind("<ButtonPress-1>", self._drag_start)
        widget.bind("<B1-Motion>", self._drag_move)
        widget.bind("<ButtonRelease-1>", self._drag_end)
        widget.bind("<Double-Button-1>", lambda _event: self.on_toggle_chat())
        widget.bind("<Button-3>", self._show_menu)
        widget.bind("<Motion>", self._hover_motion)
        widget.bind("<Leave>", self._hover_leave)

    def _build_context_menu(self) -> None:
        if hasattr(self, "menu"):
            self.menu.destroy()
        self.menu = tk.Menu(self.root, tearoff=False)
        self.menu.add_command(
            label="显示 / 隐藏主聊天窗口",
            command=self.on_toggle_chat,
        )
        action_menu = tk.Menu(self.menu, tearoff=False)
        for action in self.available_actions():
            action_menu.add_command(
                label=ACTION_LABELS.get(action, DERIVED_ACTION_LABELS.get(action, action)),
                command=lambda value=action: self.preview_action(value),
            )
        self.menu.add_cascade(label="播放动作", menu=action_menu)
        idle_menu = tk.Menu(self.menu, tearoff=False)
        for label, variable in (("偶尔散步", self._roaming_enabled),
                                ("待机打盹", self._dozing_enabled),
                                ("暂停待机小动作", self._idle_paused)):
            idle_menu.add_checkbutton(label=label, variable=variable, command=self._save_idle_preferences)
        idle_menu.add_separator()
        idle_menu.add_command(label="散步一下", command=lambda: self._start_roaming(manual=True))
        self.menu.add_cascade(label="待机小动作", menu=idle_menu)
        self.menu.add_separator()
        self.menu.add_command(label="退出 RemChat", command=self.on_exit)

    def _show_menu(self, event) -> None:
        self._stop_idle_motion()
        self._defer_idle()
        self._build_context_menu()
        from .ui.locale_controller import translator_for
        tr = translator_for(self.root)
        def translate(menu):
            end = menu.index('end')
            for index in range((end + 1) if end is not None else 0):
                if menu.type(index) in {'separator', 'tearoff'}:
                    continue
                menu.entryconfigure(index, label=tr.display(menu.entrycget(index, 'label')))
                if menu.type(index) == 'cascade':
                    translate(menu.nametowidget(menu.entrycget(index, 'menu')))
        translate(self.menu)
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _place_initial(self) -> None:
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = max(16, screen_width - self.pet_width - 72)
        y = max(16, screen_height - self.pet_height - 72)
        x, y = self._bounded_position(x, y)
        self.root.geometry(f"+{x}+{y}")

    def _new_timeline(self, action: str) -> FrameTimeline:
        frames = self._frames[action]
        durations = self._action_timings.get(action) or ACTION_DURATIONS.get(action, DOZING_DURATIONS if action == "dozing" else (140,))
        if action == "dozing" and action not in self._action_timings and self.action_library is not None:
            durations = self.action_library.durations(action, durations)
        timings = tuple(durations[min(i, len(durations)-1)] for i in range(len(frames)))
        # Python 3.12 Windows monotonic() uses 15.625 ms GetTickCount64;
        # perf_counter() is monotonic high-resolution QueryPerformanceCounter.
        return FrameTimeline(timings, time.perf_counter())

    def _schedule_animation(self, delay_ms: int) -> None:
        if self._animation_timer is not None:
            try:
                self.root.after_cancel(self._animation_timer)
            except tk.TclError:
                pass
        try:
            self._animation_timer = self.root.after(max(1, delay_ms), self._animate)
        except tk.TclError:
            self._animation_timer = None

    def _on_destroy(self, event) -> None:
        if event.widget is not self.root:
            return
        self._action_generation += 1
        if self._animation_timer is not None:
            try:
                self.root.after_cancel(self._animation_timer)
            except tk.TclError:
                pass
        self._animation_timer = None
        self._roam_plan = None
        self._frame_cache.clear()

    def _looping(self) -> bool:
        return ((self._action == "idle" and not self._focus_active and not self._thinking_active)
                or self._action == "dozing"
                or (self._action == "running" and self._focus_active)
                or (self._action == "review" and self._thinking_active)
                or (self._action in {"running-left", "running-right", "strolling-left", "strolling-right"}
                    and (self._drag_origin is not None or self._roam_plan is not None)))

    def _animate(self) -> None:
        self._animation_timer = None
        try:
            if not self.root.winfo_exists():
                return
        except tk.TclError:
            return
        now = time.perf_counter()
        if not self._exiting:
            if self._idle_blocked():
                self._stop_idle_motion()
                self._defer_idle()
            elif self._action == "idle" and now >= self._next_idle_at:
                self._choose_idle_behaviour()
            self._step_roaming(now)

        index, complete, until_boundary = self._timeline.sample(now, looping=self._looping())
        if complete:
            callback = self._completion_callback
            self._completion_callback = None
            # An exit callback is final; never enqueue another normal action.
            if self._exiting:
                self._present_frame(self._frames[self._action][-1])
                if callback is not None:
                    callback()
                return
            self._begin_action(self._base_action())
            self._defer_idle()
            if callback is not None:
                generation = self._action_generation
                self.root.after_idle(lambda: callback() if generation == self._action_generation else None)
            return

        self._frame_index = index
        self._present_frame(self._frames[self._action][index])
        delay_seconds = until_boundary
        if self._roam_plan is not None or self._action == "dozing":
            # Moving positions and interruptible idle poses check at 30 Hz.
            # Keep a deadline instead of adding work time to every interval.
            gap = 1 / MOTION_HZ
            if now >= self._motion_due:
                self._motion_due = self._motion_due + gap if now <= self._motion_due + gap else now + gap
            delay_seconds = min(delay_seconds, max(0.0, self._motion_due - now))
        self._schedule_animation(max(1, math.ceil(delay_seconds * 1000)))

    def _load_idle_preferences(self) -> dict[str, bool]:
        defaults = {"paused": False, "dozing": True, "roaming": True}
        path = self._idle_settings_path
        if path is None or not path.exists():
            return defaults
        try:
            if path.stat().st_size > 4096:
                raise ValueError("待机设置文件过大")
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("待机设置格式无效")
            for name in defaults:
                if name in data:
                    if not isinstance(data[name], bool):
                        raise ValueError("待机选项需为开关值")
                    defaults[name] = data[name]
            return defaults
        except (OSError, ValueError, UnicodeError) as error:
            self._idle_settings_error = str(error)
            return {**defaults, "paused": True}

    def _save_idle_preferences(self) -> None:
        self._stop_idle_motion()
        self._defer_idle()
        if self._idle_settings_path is None:
            return
        try:
            if self._idle_settings_error:
                raise ValueError("原待机设置无法读取，未覆盖原文件。请修复后重新启动。")
            PetActionLibrary._atomic_json(self._idle_settings_path,
                {"version": 1, "paused": self._idle_paused.get(),
                 "dozing": self._dozing_enabled.get(), "roaming": self._roaming_enabled.get()})
        except (OSError, ValueError) as error:
            messagebox.showerror("待机设置未保存", str(error), parent=self.root)

    def _defer_idle(self) -> None:
        self._next_idle_at = time.perf_counter() + self._random.uniform(45, 90)

    def _idle_blocked(self) -> bool:
        if (self._exiting or self._drag_origin is not None or self._focus_active
                or self._thinking_active or (self._pointer_over_sprite and self._action not in {"dozing", "waking"})
                or (self._idle_paused.get() and not self._idle_manual)):
            return True
        if not self.root.winfo_viewable():
            return True
        if self._interaction_active is not None and not self._idle_manual:
            try:
                return bool(self._interaction_active())
            except Exception:
                # An unavailable host state may pause wandering, never the
                # regular pet animations or the rest of the application.
                return True
        return False

    def _choose_idle_behaviour(self) -> None:
        choices = []
        if self._dozing_enabled.get() and "dozing" in self._frames:
            if self.action_library is None or self.action_library.is_enabled("dozing"):
                choices.append("dozing")
        if self._roaming_enabled.get() and all(a in self._frames for a in ("strolling-left", "strolling-right")):
            choices.append("roaming")
        self._defer_idle()
        if not choices:
            return
        choice = self._random.choice(choices)
        if choice == "roaming":
            self._start_roaming()
        else:
            self._begin_action("dozing")

    def _work_area(self) -> WindowRect:
        fallback = WindowRect(0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        current = self.window_rect() or fallback
        return native_module("windowing").work_area_at(current, fallback)

    def _bounded_position(self, x: float, y: float) -> tuple[int, int]:
        proposed = WindowRect(round(x), round(y), round(x)+self.pet_width, round(y)+self.pet_height)
        fallback = WindowRect(0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        work = native_module("windowing").work_area_at(proposed, fallback)
        return clamp_pet_position(x, y, self.pet_width, self.pet_height, work)

    def _start_roaming(self, *, manual: bool = False) -> None:
        if self._exiting or self._drag_origin is not None or self._thinking_active or self._focus_active:
            return
        if not manual and self._idle_blocked():
            return
        work = self._work_area()
        x, y = clamp_pet_position(self.root.winfo_x(), self.root.winfo_y(),
                                  self.pet_width, self.pet_height, work)
        room = max(0, work.width-self.pet_width)
        if room < 24:
            return
        distance = self._random.uniform(120, 240) * self.scale
        candidates = (max(work.left, x-distance), min(work.right-self.pet_width, x+distance))
        candidates = tuple(target for target in candidates if abs(target-x) >= 24)
        if not candidates:
            return
        target = round(self._random.choice(candidates))
        action = "strolling-right" if target > x else "strolling-left"
        if action not in self._frames or (self.action_library is not None and not self.action_library.is_enabled(action)):
            return
        self._stop_idle_motion()
        self._begin_action(action)
        self._idle_manual = manual
        self._roam_plan = RoamPlan(x, y, target, time.perf_counter(),
                                   max(5.0, abs(target-x)/(28*self.scale)), work)
        self._roam_direction = action
        self._motion_due = time.perf_counter()

    def _stop_idle_motion(self) -> None:
        was_idle_motion = self._roam_plan is not None or self._action == "dozing"
        self._roam_plan = None
        self._roam_direction = None
        self._idle_manual = False
        if was_idle_motion and not self._exiting and hasattr(self, "_timeline"):
            self._begin_action(self._base_action())

    def _step_roaming(self, now: float) -> None:
        plan = self._roam_plan
        if plan is None:
            return
        work = self._work_area()
        if work != plan.work_area:
            # Display/taskbar geometry may change mid-stroll. Stop and return
            # to a visible position on the current nearest monitor.
            x, y = clamp_pet_position(self.root.winfo_x(), self.root.winfo_y(),
                                      self.pet_width, self.pet_height, work)
            self._stop_idle_motion()
            self.root.geometry(f"+{x}+{y}")
            self._defer_idle()
            return
        x, y, arrived = plan.sample(now, self.pet_width, self.pet_height)
        if (x, y) != (self.root.winfo_x(), self.root.winfo_y()):
            # Tk's '-20' means an offset from the right edge. '+-20' is an
            # absolute negative virtual-screen coordinate, as required here.
            self.root.geometry(f"+{x}+{y}")
        if arrived:
            self._stop_idle_motion()
            self._defer_idle()

    def _drag_start(self, event) -> None:
        if self._exiting:
            return
        # A press is not a drag. A sleeping click has its own wake sequence.
        self._press_origin = (event.x_root, event.y_root,
                              self.root.winfo_x(), self.root.winfo_y())
        self._press_was_sleeping = self._action == "dozing"

    def _drag_move(self, event) -> None:
        if self._drag_origin is None and self._press_origin is not None:
            px, py, wx, wy = self._press_origin
            if max(abs(event.x_root-px), abs(event.y_root-py)) < 5:
                return
            self._stop_idle_motion()
            self._drag_origin = (px-wx, py-wy)
        if self._drag_origin is None:
            return
        old_x = self.root.winfo_x()
        x, y = self._bounded_position(event.x_root - self._drag_origin[0], event.y_root - self._drag_origin[1])
        self.trigger_action("running-right" if x >= old_x else "running-left")
        self.root.geometry(f"+{x}+{y}")

    def _drag_end(self, _event) -> None:
        dragged = self._drag_origin is not None
        wake = self._press_was_sleeping and not dragged and self._action == "dozing"
        self._drag_origin = None
        self._press_origin = None
        self._press_was_sleeping = False
        self._defer_idle()
        if wake:
            self.trigger_action("waking", force=True)
            self._idle_manual = True
            self._pointer_over_sprite = True
            return
        if not dragged:
            return
        reply = self._deferred_reply
        self._deferred_reply = None
        if reply is not None:
            self.show_reply(reply)
        else:
            self.trigger_action(self._base_action())

    def _hover_motion(self, event) -> None:
        """只有鼠标真正覆盖非透明像素，且雷姆静待时，才触发跳跃。"""

        if (self._drag_origin is not None or self._focus_active or self._thinking_active
                or self._exiting or self._action in {"dozing", "waking"}):
            return
        frames = self._frames.get(self._action, self._frames["idle"])
        frame = frames[min(self._frame_index, len(frames) - 1)]
        opaque = False
        if 0 <= event.x < self.pet_width and 0 <= event.y < self.pet_height:
            try:
                opaque = not frame.transparency_get(event.x, event.y)
            except tk.TclError:
                opaque = True
        entered = opaque and not self._pointer_over_sprite
        self._pointer_over_sprite = opaque
        if opaque and self._roam_plan is not None:
            self._stop_idle_motion()
            self._defer_idle()
        elif entered and self._action == "idle":
            self._play_repeated_action("jumping", repetitions=2)

    def _hover_leave(self, _event) -> None:
        self._pointer_over_sprite = False

    def _play_repeated_action(self, action: str, *, repetitions: int) -> None:
        """连续完整播放动作，避免仅复制末帧造成假重复。"""

        if repetitions <= 0:
            return
        callback = (
            None
            if repetitions == 1
            else lambda: self._play_repeated_action(
                action,
                repetitions=repetitions - 1,
            )
        )
        self.trigger_action(action, on_complete=callback)

    def window_rect(self) -> WindowRect | None:
        if not self.root.winfo_exists():
            return None
        return WindowRect(
            self.root.winfo_x(),
            self.root.winfo_y(),
            self.root.winfo_x() + self.pet_width,
            self.root.winfo_y() + self.pet_height,
        )
