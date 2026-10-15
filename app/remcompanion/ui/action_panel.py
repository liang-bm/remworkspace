"""RemChat 动作管理：选择、预览、节奏与自定义透明帧导入。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import queue
import threading
import tkinter as tk
from .font_source import native_font
from .scroll_surface import ScrollCanvas
import tkinter.font as tkfont
from .localized_dialogs import filedialog, messagebox

from ..companion_preferences import open_in_vscode
from ..pet_actions import PetActionEntry, PetActionLibrary
from .locale_controller import translator_for
from .theme import (
    APP_BG, WHITE, INK, MUTED, LINE, ACCENT_DEEP, ACCENT_SOFT, ON_ACCENT,
    SUCCESS, ERROR, BODY_FONT,
)


class ActionPanel(tk.Frame):
    """on_preview(action) 播放桌面动作，on_changed() 通知状态机重载动作库。"""

    def __init__(
        self, parent: tk.Misc, root_path: Path | str,
        on_preview: Callable[[str], None], on_changed: Callable[[], None],
    ) -> None:
        super().__init__(parent, bg=APP_BG)
        self.project_root = Path(root_path)
        self.library = PetActionLibrary(self.project_root, Path(__file__).resolve().parents[1] / "assets" / "pet")
        self.on_preview = on_preview
        self.on_changed = on_changed
        self._entries: tuple[PetActionEntry, ...] = ()
        self._current: PetActionEntry | None = None
        self._preview_frames: list[tk.PhotoImage] = []
        self._preview_index = 0
        self._preview_timer: str | None = None
        self._poll_timer: str | None = None
        self._closed = False
        self._busy = False
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._scale = max(1.0,native_font(self,font=(BODY_FONT,10)).metrics("linespace")/18)
        self.enabled = tk.BooleanVar(self, True)
        self.speed = tk.DoubleVar(self, 1.0)
        self.speed_label = tk.StringVar(self, "1.00 倍")
        self.detail = tk.StringVar(self)
        self.status = tk.StringVar(self, "内置素材保留原样，自定义动作只保存在 data/pet-actions。")
        self._build()
        self.refresh()
        self._poll_timer = self.after(100, self._drain_events)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.bind("<Map>", self._on_map, add="+")
        self.bind("<Unmap>", self._on_unmap, add="+")

    def _button(self, parent: tk.Misc, text: str, command: Callable, primary: bool = False) -> tk.Button:
        button = tk.Button(
            parent, text=text, command=command,
            bg=ACCENT_DEEP if primary else ACCENT_SOFT,
            fg=ON_ACCENT if primary else ACCENT_DEEP, activebackground=ACCENT_DEEP,
            activeforeground=ON_ACCENT, relief="flat", bd=0, cursor="hand2",
            font=(BODY_FONT, 10, "bold"), padx=16, pady=10,
            highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT_DEEP,
        )
        button.bind("<Configure>", lambda event: button.configure(wraplength=max(80, event.width - 36)))
        return button

    def _build(self) -> None:
        heading = tk.Frame(self, bg=APP_BG)
        heading.pack(fill="x", padx=26, pady=(20, 14))
        tk.Label(heading, text="动作工作台", font=(BODY_FONT, 22, "bold"),
                 bg=APP_BG, fg=INK).pack(anchor="w")
        subtitle = tk.Label(heading, text="让雷姆的节奏，刚好合你心意。", bg=APP_BG,
                            fg=MUTED, font=(BODY_FONT, 10), anchor="w", justify="left")
        subtitle.pack(fill="x", pady=(4, 0))
        subtitle.bind("<Configure>", lambda event: subtitle.configure(wraplength=max(100, event.width)))
        body = tk.Frame(self, bg=WHITE)
        body.pack(fill="both", expand=True, padx=26, pady=(0, 14))
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        rail = tk.Frame(body, bg=WHITE)
        rail.grid(row=0, column=0, sticky="nsew", padx=14, pady=16)
        tk.Label(rail, text="动作库", bg=WHITE, fg=MUTED,
                 font=(BODY_FONT, 10)).pack(anchor="w", pady=(0, 10))
        list_frame = tk.Frame(rail, bg=WHITE)
        list_frame.pack(fill="both", expand=True)
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")
        self.action_list = tk.Listbox(
            list_frame, width=17, font=(BODY_FONT, 11), bg=APP_BG, fg=INK,
            selectbackground=ACCENT_DEEP, selectforeground=ON_ACCENT, bd=0,
            highlightthickness=1, highlightbackground=LINE, exportselection=False,
            activestyle="none", yscrollcommand=scrollbar.set,
        )
        self.action_list.pack(fill="both", expand=True)
        scrollbar.configure(command=self.action_list.yview)
        self.action_list.bind("<<ListboxSelect>>", self._select)
        self.import_button = self._button(rail, "导入动作帧包", self._import_action, True)
        self.import_button.pack(fill="x", pady=(12, 4))
        self.refresh_button = self._button(rail, "刷新动作库", self.refresh)
        self.refresh_button.pack(fill="x", pady=4)
        details_rail = tk.Frame(body, bg=WHITE)
        details_rail.grid(row=0, column=1, sticky="nsew", padx=(4, 14), pady=16)
        viewport = ScrollCanvas(details_rail, bg=WHITE, highlightthickness=0)
        self.details_viewport = viewport
        scroll = tk.Scrollbar(details_rail, command=viewport.yview)
        scroll.pack(side="right", fill="y")
        viewport.pack(fill="both", expand=True)
        viewport.configure(yscrollcommand=scroll.set)
        self.details_frame = tk.Frame(viewport, bg=WHITE)
        item = viewport.create_window(0, 0, window=self.details_frame, anchor="nw")
        self.details_frame.bind("<Configure>", lambda _: viewport.configure(scrollregion=viewport.bbox("all")))
        viewport.bind("<Configure>", lambda event: viewport.itemconfigure(item, width=event.width))
        self.title = tk.Label(self.details_frame, bg=WHITE, fg=INK, text="选择一个动作",
                              font=(BODY_FONT, 18, "bold"), anchor="w", justify="left")
        self.title.pack(fill="x", pady=(0, 10))
        self.title._remchat_locale_exclude = True
        self.title.bind("<Configure>", lambda event: self.title.configure(wraplength=max(100, event.width)))
        self.detail_label = tk.Label(self.details_frame, textvariable=self.detail, bg=WHITE, fg=MUTED,
                                    font=(BODY_FONT, 10), anchor="w", justify="left")
        self.detail_label.pack(fill="x", pady=(0, 14))
        self.detail_label._remchat_locale_exclude = True
        self.detail_label.bind("<Configure>", lambda event: self.detail_label.configure(wraplength=max(120, event.width)))
        preview_width = round(192 * min(self._scale, 1.5))
        preview_height = round(208 * min(self._scale, 1.5))
        self.preview_canvas = tk.Canvas(self.details_frame, bg=ACCENT_SOFT, highlightthickness=0,
                                        width=preview_width, height=preview_height)
        self.preview_canvas.pack(fill="x", pady=(0, 14))
        self.enabled_control = tk.Checkbutton(
            self.details_frame, text="允许自动触发这个动作", variable=self.enabled,
            bg=WHITE, activebackground=WHITE, fg=INK, selectcolor=ACCENT_SOFT,
            font=(BODY_FONT, 11), anchor="w", justify="left",
        )
        self.enabled_control.pack(fill="x", pady=6)
        self.enabled_control.bind("<Configure>", lambda event: self.enabled_control.configure(wraplength=max(100, event.width - 30)))
        speed_row = tk.Frame(self.details_frame, bg=WHITE)
        speed_row.pack(fill="x", pady=8)
        tk.Label(speed_row, text="播放速度", bg=WHITE, fg=INK,
                 font=(BODY_FONT, 11)).pack(side="left", padx=(0, 16))
        tk.Label(speed_row, textvariable=self.speed_label, bg=WHITE, fg=ACCENT_DEEP,
                 font=(BODY_FONT, 11, "bold")).pack(side="left")
        self.speed_control = tk.Scale(
            self.details_frame, from_=.5, to=2, resolution=.05, variable=self.speed,
            orient="horizontal", showvalue=False, bg=WHITE, troughcolor=ACCENT_SOFT,
            activebackground=ACCENT_DEEP, highlightthickness=0,
            command=self._speed_changed, length=round(280 * self._scale),
        )
        self.speed_control.pack(fill="x", pady=(0, 10))
        speed_help = tk.Label(self.details_frame, text="0.5 倍更慢 · 1 倍原速 · 2 倍更快\n"
                 "速度只改变逐帧时长；动作缺少中间帧时，请补帧改善轨迹。",
                 bg=WHITE, fg=MUTED, font=(BODY_FONT, 9), justify="left", anchor="w")
        speed_help.pack(fill="x", pady=8)
        speed_help.bind("<Configure>", lambda event: speed_help.configure(wraplength=max(100, event.width)))
        controls = tk.Frame(self.details_frame, bg=WHITE)
        controls.pack(fill="x", pady=(10, 18))
        self.save_button = self._button(controls, "保存动作设置", self._save, True)
        self.save_button.pack(fill="x", pady=3)
        self.reload_button = self._button(controls, "重新读取动作设置", self._reload_selected)
        self.reload_button.pack(fill="x", pady=3)
        self.restore_button = self._button(controls, "恢复内置帧", self._restore_bundled)
        self.restore_button.pack(fill="x", pady=3)
        self.preview_button = self._button(controls, "桌面上预览", self._play_desktop)
        self.preview_button.pack(fill="x", pady=3)
        import_help = tk.Label(self.details_frame, text="静待、思考、输出、工作中是应用状态，保持启用。\n"
                 "导入仅接纳 action.json + 编号透明 PNG，不执行素材包中的脚本。",
                 bg=WHITE, fg=MUTED, font=(BODY_FONT, 9), justify="left", anchor="w")
        import_help.pack(fill="x", pady=8)
        import_help.bind("<Configure>", lambda event: import_help.configure(wraplength=max(100, event.width)))
        directory_controls = tk.Frame(self.details_frame, bg=WHITE)
        directory_controls.pack(fill="x", pady=(10, 4))
        self._button(directory_controls, "VS Code 打开动作目录", self._open_actions).pack(fill="x", pady=4)
        self._button(directory_controls, "查看动作生成 Skill", self._open_skill).pack(fill="x", pady=4)
        footer = tk.Frame(self, bg=APP_BG)
        footer.pack(fill="x", padx=26, pady=(0, 12))
        status_label = tk.Label(footer, textvariable=self.status, bg=APP_BG, fg=MUTED,
                               font=(BODY_FONT, 9), anchor="w", justify="left")
        status_label.pack(fill="x")
        status_label.bind("<Configure>", lambda event: status_label.configure(wraplength=max(100, event.width)))
        def install_wheel() -> None:
            if self._closed:
                return
            def scroll_content(event: tk.Event) -> str:
                viewport.yview_scroll(-1 if event.num == 4 or event.delta > 0 else 1, "units")
                return "break"
            def visit(widget: tk.Misc) -> None:
                if widget is not self.speed_control:
                    widget.bind("<MouseWheel>", scroll_content, add="+")
                    widget.bind("<Button-4>", scroll_content, add="+")
                    widget.bind("<Button-5>", scroll_content, add="+")
                for child in widget.winfo_children():
                    visit(child)
            visit(self.details_frame)
            viewport.bind("<MouseWheel>", scroll_content)
            viewport.bind("<Button-4>", scroll_content)
            viewport.bind("<Button-5>", scroll_content)
        self.after_idle(install_wheel)

    def refresh(self, *, preserve_draft: bool = True) -> None:
        if self._busy:
            return
        selected = self._current.action if self._current else None
        dirty = preserve_draft and self._is_dirty()
        self._entries = self.library.entries()
        self.action_list.delete(0, "end")
        index = None
        for row, entry in enumerate(self._entries):
            state = "" if entry.enabled else " · 已停用"
            custom = " · 自定" if entry.source == "custom" else ""
            self.action_list.insert("end", entry.label + custom + state)
            if entry.action == selected:
                index = row
        if self._entries:
            if index is not None:
                self._restore_selection(index)
                if not dirty:
                    self._load_entry(self._entries[index])
            elif not dirty:
                self._restore_selection(0)
                self._load_entry(self._entries[0])
        else:
            if not dirty:
                self._current = None
                self._stop_preview()
                self._preview_frames = []
                self.preview_canvas.delete("all")
                self.title.configure(text="动作目录尚未加载")
                self.detail.set("请检查项目的 assets/pet 或导入自定义帧包。")
        self._set_busy(False)
        if dirty:
            self.status.set("动作库已刷新；当前未保存设置已保留。")
        if self.library.errors:
            self.status.set("；".join(self.library.errors))
        self.retranslate()

    def _is_dirty(self) -> bool:
        entry = self._current
        return entry is not None and (self.enabled.get() != entry.enabled or abs(self.speed.get() - entry.speed) > .001)

    def _restore_selection(self, index: int | None = None) -> None:
        self.action_list.selection_clear(0, "end")
        if index is None and self._current:
            index = next((i for i, row in enumerate(self._entries) if row.action == self._current.action), None)
        if index is not None:
            self.action_list.selection_set(index)
            self.action_list.activate(index)

    def _select(self, event: tk.Event | None = None) -> None:
        if self._busy:
            self._restore_selection()
            return
        selection = self.action_list.curselection()
        if not selection:
            return
        entry = self._entries[selection[0]]
        if self._current and entry.action == self._current.action:
            return
        if self._is_dirty():
            tr = translator_for(self)
            if not messagebox.askyesno(tr("切换动作"), tr("当前有未保存修改。切换会放弃这份草稿，是否继续？"), parent=self):
                self._restore_selection()
                return
        self._load_entry(entry)

    def _load_entry(self, entry: PetActionEntry) -> None:
        self._current = entry
        self.retranslate()
        self.enabled.set(entry.enabled)
        self.enabled_control.configure(state="disabled" if entry.locked else "normal")
        self.speed.set(entry.speed)
        self._speed_changed()
        self._load_preview()
        self._set_busy(False)

    def _reload_selected(self) -> None:
        if self._busy:
            return
        tr = translator_for(self)
        if self._is_dirty() and not messagebox.askyesno(tr("重新读取动作设置"), tr("重新读取会放弃未保存修改，是否继续？"), parent=self):
            return
        try:
            self.library.reload_settings()
            self.refresh(preserve_draft=False)
            if not self.library.errors:
                self.status.set("已重新读取动作设置，未修改文件。")
        except (ValueError, OSError) as exc:
            self.status.set(str(exc))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for button in (self.import_button, self.refresh_button, self.reload_button):
            button.configure(state="disabled" if busy else "normal")
        self.action_list.configure(state="disabled" if busy else "normal")
        available = not busy and self._current is not None
        for button in (self.save_button, self.preview_button, self.speed_control):
            button.configure(state="normal" if available else "disabled")
        self.enabled_control.configure(state="normal" if available and not self._current.locked else "disabled")
        restorable = (available and self._current.source == "custom"
                      and self.library.bundled_entry(self._current.action) is not None)
        self.restore_button.configure(state="normal" if restorable else "disabled")

    def retranslate(self) -> None:
        """Update labels only, preserving speed edits and the current animation phase."""
        tr = translator_for(self)
        entry = self._current
        if entry is not None:
            self.title.configure(text=entry.label if entry.source == "custom" else tr.display(entry.label))
            duration = sum(self._preview_durations(entry)) / 1000
            text = tr("{action} · {count} 帧 · {asset_kind}", action=entry.action,
                      count=entry.frame_count, asset_kind=tr("自定义" if entry.source == "custom" else "内置"))
            if duration:
                text += tr(" · 原速一轮 {seconds} 秒", seconds=f"{duration:.2f}")
            if entry.locked:
                text += "\n" + tr("自动状态动作，不能禁用。")
            self.detail.set(text)
        selection = self.action_list.curselection()
        self.action_list.delete(0, "end")
        for item in self._entries:
            label = item.label if item.source == "custom" else tr.display(item.label)
            self.action_list.insert("end", label + (tr(" · 自定") if item.source == "custom" else "")
                                    + (tr(" · 已停用") if not item.enabled else ""))
        if selection:
            self.action_list.selection_set(selection[0])

    def _speed_changed(self, _: str = "") -> None:
        self.speed_label.set(f"{self.speed.get():.2f} 倍")

    def _load_preview(self) -> None:
        self._stop_preview()
        self._preview_frames = []
        self.preview_canvas.delete("all")
        if self._current is None:
            return
        try:
            # 当前预览只载入选中动作，切换后释放引用，不把所有动作倍增驻留。
            for path in self._current.frames:
                self._preview_frames.append(tk.PhotoImage(master=self, file=str(path)))
        except tk.TclError as exc:
            self.status.set(f"预览图片无法读取：{exc}")
            self._preview_frames = []
            return
        self._preview_index = 0
        self._animate_preview()

    def _animate_preview(self) -> None:
        self._preview_timer = None
        if self._closed or not self._preview_frames or self._current is None or not self.winfo_viewable():
            return
        image = self._preview_frames[self._preview_index]
        width = self.preview_canvas.winfo_width()
        height = self.preview_canvas.winfo_height()
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(width / 2, height / 2, image=image)
        durations = self._preview_durations(self._current)
        duration = durations[min(self._preview_index, len(durations) - 1)]
        duration = max(10, round(duration / max(.5, self.speed.get())))
        self._preview_index = (self._preview_index + 1) % len(self._preview_frames)
        self._preview_timer = self.after(duration, self._animate_preview)

    @staticmethod
    def _preview_durations(entry: PetActionEntry) -> tuple[int, ...]:
        if entry.durations_ms:
            return entry.durations_ms
        # 沿用现有渲染器的节奏，不在预览页重新定义动作停顿。
        from ..pet_window import ACTION_DURATIONS
        return ACTION_DURATIONS.get(entry.action, (140,) * entry.frame_count)

    def _stop_preview(self) -> None:
        if self._preview_timer is not None:
            self.after_cancel(self._preview_timer)
            self._preview_timer = None

    def _on_map(self, event: tk.Event) -> None:
        if event.widget is self and self._preview_timer is None:
            self._animate_preview()

    def _on_unmap(self, event: tk.Event) -> None:
        if event.widget is self:
            self._stop_preview()

    def _save(self) -> None:
        if self._current is None or self._busy:
            return
        try:
            self.library.set_options(self._current.action, enabled=self.enabled.get(), speed=self.speed.get())
        except (ValueError, OSError) as exc:
            self.status.set(str(exc))
            return
        self.refresh(preserve_draft=False)
        self._sync_desktop("动作设置已保存，桌面雷姆已同步。", "动作设置已保存，但桌面同步失败；可再次保存重试：{error}")

    def _sync_desktop(self, success: str, failure: str) -> None:
        try:
            self.on_changed()
        except Exception as exc:
            # Disk commit already succeeded; report the callback boundary, never claim rollback.
            self.status.set(failure.format(error=exc))
        else:
            self.status.set(success)

    def _play_desktop(self) -> None:
        if self._current is not None and not self._busy:
            try:
                self.on_preview(self._current.action)
            except Exception as exc:
                self.status.set(translator_for(self)("桌面预览失败，可重试：{error}", error=exc))

    def _import_action(self) -> None:
        if self._busy:
            return
        path = filedialog.askopenfilename(parent=self, title="选择 Skill 生成的 action.json",
                                         filetypes=[("动作清单", "action.json"), ("JSON", "*.json")])
        if not path:
            return
        self._set_busy(True)
        self.status.set("正在检查画布、透明背景、帧顺序与 timing…")
        def worker() -> None:
            try:
                entry = self.library.import_action(path)
                self._events.put(("imported", entry))
            except Exception as exc:
                self._events.put(("error", str(exc)))
        threading.Thread(target=worker, name="remchat-action-import", daemon=True).start()

    def _restore_bundled(self) -> None:
        entry = self._current
        if self._busy or entry is None or entry.source != "custom":
            return
        if self.library.bundled_entry(entry.action) is None:
            self.status.set("这个动作没有可恢复的内置帧。")
            return
        tr = translator_for(self)
        if not messagebox.askyesno(tr("恢复内置帧"), tr(
            "将 {label} 的自定义帧移到备份目录，恢复内置帧。\n速度、启用选项和未保存设置保留。备份可重新导入。\n备份目录：{path}",
            label=entry.label, path=self.library.backup_root), parent=self):
            return
        self._set_busy(True)
        self.status.set("正在备份自定义帧并恢复内置帧…")

        def worker() -> None:
            try:
                backup = self.library.restore_bundled(entry.action)
                self._events.put(("restored", (entry.action, backup)))
            except Exception as exc:
                self._events.put(("restore-error", str(exc)))
        threading.Thread(target=worker, name="remchat-action-restore", daemon=True).start()

    def _drain_events(self) -> None:
        self._poll_timer = None
        if self._closed:
            return
        try:
            while True:
                try:
                    kind, value = self._events.get_nowait()
                except queue.Empty:
                    break
                self._set_busy(False)
                if kind == "error":
                    self.status.set(f"导入未完成，现有动作保持原样：{value}")
                elif kind == "restore-error":
                    self.status.set(f"恢复未完成，原帧保留；请刷新后重试：{value}")
                elif kind == "restored":
                    action, backup = value
                    dirty = self._is_dirty()
                    enabled, speed = self.enabled.get(), self.speed.get()
                    self.refresh(preserve_draft=False)
                    # Rebind preview to the original frames, but keep edits that
                    # were never submitted as playback preferences.
                    if dirty and self._current is not None and self._current.action == action:
                        self.enabled.set(enabled)
                        self.speed.set(speed)
                        self._speed_changed()
                    message = f"已恢复内置帧。自定义帧备份：{backup}"
                    if dirty:
                        message += "\n当前未保存设置已保留。"
                    self._sync_desktop(message, "内置帧已恢复，但桌面同步失败；可再次保存重试：{error}")
                else:
                    entry = value
                    self.refresh()
                    backup = self.library.last_backup
                    tr = translator_for(self)
                    message = tr("已导入 {label}，{count} 帧。", label=entry.label, count=entry.frame_count)
                    if backup:
                        message += tr("旧版已备份到 {path}。", path=backup.name)
                    if self._is_dirty():
                        message += tr("当前未保存设置已保留。")
                    self._sync_desktop(message, "动作帧包已导入，但桌面同步失败；可再次保存重试：{error}")
        except (ValueError, OSError, tk.TclError) as exc:
            self.status.set(translator_for(self)("动作列表刷新失败，可重试；导入结果请检查动作目录：{error}", error=exc))
        finally:
            if not self._closed:
                self._poll_timer = self.after(100, self._drain_events)

    def _open_actions(self) -> None:
        try:
            self.library.custom_root.mkdir(parents=True, exist_ok=True)
            open_in_vscode(self.library.custom_root)
        except (OSError, ValueError) as exc:
            messagebox.showerror("无法打开动作目录", str(exc), parent=self)

    def _open_skill(self) -> None:
        skill = self.project_root / "skills" / "build-pet-action" / "SKILL.md"
        if not skill.is_file():
            self.status.set("当前项目未包含 build-pet-action Skill，请检查 skills 目录。")
            return
        try:
            open_in_vscode(skill)
        except (OSError, ValueError) as exc:
            messagebox.showerror("无法打开 Skill", str(exc), parent=self)

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            self._closed = True
            self._stop_preview()
            if self._poll_timer is not None:
                self.after_cancel(self._poll_timer)
                self._poll_timer = None

    def destroy(self) -> None:
        self._closed = True
        self._stop_preview()
        if self._poll_timer is not None:
            self.after_cancel(self._poll_timer)
            self._poll_timer = None
        super().destroy()
