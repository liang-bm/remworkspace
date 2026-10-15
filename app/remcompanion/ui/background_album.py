"""The background album extends RemChat's existing appearance settings.

THESIS: Choose pictures and a quiet change rhythm, not a slideshow.
OWN-WORLD: Blue-grey semantic surfaces, existing rounded SettingsCard chrome.
STORY: Import a copy, mark participation, choose the current image.
FIRST VIEWPORT: Change rhythm and import above a responsive thumbnail gallery.
FORM: A precisely requested local extension; no new visual identity.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import queue
import tkinter as tk
from tkinter import ttk
from .localized_dialogs import filedialog, messagebox

from ..background_album import BackgroundAlbum, MODES, prepare_import
from ..chat_background import render_faded_cover
from . import theme
from .scroll_surface import ScrollCanvas
from .dpi import pixels
from .selection_feedback import Tooltip
from .settings_card import SettingsCard
from .theme_controller import set_color_roles


class BackgroundAlbumWindow:
    """Non-modal management with worker-decoded, fixed-size thumbnails."""

    PAGE_SIZE = 24

    def __init__(self, owner, album: BackgroundAlbum, on_changed):
        self.owner, self.album, self.on_changed = owner, album, on_changed
        self.window = tk.Toplevel(owner.root)
        self.window.title("背景相册")
        self.window.geometry(f"{pixels(self.window, 820)}x{pixels(self.window, 660)}")
        self.window.minsize(pixels(self.window, 420), pixels(self.window, 460))
        self.window.transient(owner.root)
        set_color_roles(self.window, background="APP_BG")
        self._closed = False
        self._busy = False
        self._generation = 0
        self._page = 0
        self._columns = 0
        self._tiles = []
        self._photos = {}
        self._jobs = ThreadPoolExecutor(max_workers=2, thread_name_prefix="background-album")
        self._results = queue.Queue()
        self._poll_job = None
        self._layout_job = None
        self._controls = []
        self._build()
        self.refresh()
        self.window.bind("<Escape>", lambda _event: self.close())
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Destroy>", self._destroyed, add=True)
        self._poll_job = self.window.after(80, self._drain)

    def _frame(self, parent):
        result = tk.Frame(parent)
        set_color_roles(result, background="APP_BG")
        return result

    def _label(self, parent, text, *, size=10, bold=False, muted=False, surface=False):
        result = tk.Label(parent, text=text, anchor="w", justify="left",
                          font=(theme.BODY_FONT, size, "bold" if bold else "normal"))
        set_color_roles(result, background="WHITE" if surface else "APP_BG",
                        foreground="MUTED" if muted else "INK")
        return result

    def _button(self, parent, label, command, *, primary=False, surface=False):
        result = tk.Button(parent, text=label, command=command, relief="flat", bd=0,
                           padx=pixels(parent, 12), pady=pixels(parent, 7), cursor="hand2",
                           font=(theme.BODY_FONT, 10), takefocus=True, highlightthickness=1)
        set_color_roles(result, background="ACCENT_DEEP" if primary else "WHITE" if surface else "ACCENT_SOFT",
                        foreground="ON_ACCENT" if primary else "INK",
                        activebackground="ACCENT_HOVER" if primary else "ACCENT_SOFT",
                        activeforeground="ON_ACCENT" if primary else "INK", disabledforeground="MUTED",
                        highlightbackground="WHITE" if surface else "APP_BG", highlightcolor="ACCENT")
        result.bind("<FocusIn>", self._reveal, add=True)
        self._controls.append(result)
        return result

    def _build(self):
        top = self._frame(self.window)
        top.pack(fill="x", padx=pixels(self.window, 24), pady=(pixels(self.window, 20), pixels(self.window, 16)))
        self._label(top, "背景相册", size=18, bold=True).pack(anchor="w")
        self.hint = self._label(top, "导入只保存应用副本；背景居中等比裁切，不拉伸。", muted=True)
        self.hint.pack(fill="x", pady=(pixels(top, 6), pixels(top, 16)))
        row = self._frame(top)
        row.pack(fill="x")
        self._label(row, "更换方式").pack(side="left", padx=(0, pixels(row, 8)))
        self.mode = tk.StringVar(self.window, MODES[self.album.mode])
        self.mode_select = ttk.Combobox(row, textvariable=self.mode, values=tuple(MODES.values()),
                                        state="readonly", width=16, font=(theme.BODY_FONT, 10))
        self.mode_select.pack(side="left")
        self.mode_select.bind("<<ComboboxSelected>>", self._change_mode)
        self.mode_select.bind("<Return>", self._change_mode, add=True)
        self.import_button = self._button(row, "导入图片", self._import, primary=True)
        self.import_button.pack(side="right")
        self.rhythm = self._label(top, "", muted=True)
        self.rhythm.pack(fill="x", pady=(pixels(top, 10), 0))

        host = self._frame(self.window)
        host.pack(fill="both", expand=True, padx=pixels(self.window, 16))
        self.canvas = ScrollCanvas(host, highlightthickness=0, bd=0, takefocus=True)
        set_color_roles(self.canvas, background="APP_BG", highlightcolor="ACCENT")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll = ttk.Scrollbar(host, orient="vertical", command=self.canvas.yview)
        self.scroll.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.grid = self._frame(self.canvas)
        self._grid_window = self.canvas.create_window(0, 0, window=self.grid, anchor="nw")
        self.canvas.bind("<Configure>", self._configure, add=True)
        self.grid.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")), add=True)
        self._wheel_bind(self.canvas)
        self.canvas.bind("<Prior>", lambda _e: self._page_scroll(-1))
        self.canvas.bind("<Next>", lambda _e: self._page_scroll(1))
        self.canvas.bind("<Up>", lambda _e: self._unit_scroll(-1))
        self.canvas.bind("<Down>", lambda _e: self._unit_scroll(1))
        footer = self._frame(self.window)
        footer.pack(fill="x", padx=pixels(self.window, 24), pady=pixels(self.window, 16))
        self.status = self._label(footer, "", muted=True)
        self.status.pack(fill="x", pady=(0, pixels(footer, 10)))
        pages = self._frame(footer)
        pages.pack(fill="x")
        self.previous = self._button(pages, "上一页", lambda: self._set_page(-1))
        self.previous.pack(side="left")
        self.page_label = self._label(pages, "")
        self.page_label.pack(side="left", padx=pixels(pages, 12))
        self.next = self._button(pages, "下一页", lambda: self._set_page(1))
        self.next.pack(side="left")
        self._button(pages, "完成", self.close).pack(side="right")

    def _configure(self, event):
        self.canvas.itemconfigure(self._grid_window, width=max(1, event.width))
        self.hint.configure(wraplength=max(pixels(self.window, 250), event.width - pixels(self.window, 24)))
        self.rhythm.configure(wraplength=max(pixels(self.window, 250), event.width - pixels(self.window, 24)))
        self.status.configure(wraplength=max(pixels(self.window, 250), event.width - pixels(self.window, 24)))
        if self._layout_job is None:
            self._layout_job = self.window.after(90, self._layout)

    def _layout(self):
        self._layout_job = None
        columns = min(4, max(1, self.canvas.winfo_width() // pixels(self.window, 250)))
        if columns == self._columns:
            return
        self._columns = columns
        for column in range(4):
            self.grid.grid_columnconfigure(column, weight=1 if column < columns else 0, uniform="album" if column < columns else "")
        for index, tile in enumerate(self._tiles):
            tile.grid(row=index // columns, column=index % columns, sticky="nsew",
                      padx=pixels(self.window, 8), pady=pixels(self.window, 8))

    def _wheel_bind(self, widget):
        widget.bind("<MouseWheel>", self._wheel, add=True)
        widget.bind("<Button-4>", lambda _e: self._unit_scroll(-3), add=True)
        widget.bind("<Button-5>", lambda _e: self._unit_scroll(3), add=True)

    def _wheel(self, event):
        if event.delta:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def _unit_scroll(self, count):
        self.canvas.yview_scroll(count, "units")
        return "break"

    def _page_scroll(self, count):
        self.canvas.yview_scroll(count, "pages")
        return "break"

    def _reveal(self, event):
        widget = event.widget
        self.window.after_idle(lambda: self._reveal_ready(widget))

    def _reveal_ready(self, widget):
        if self._closed or not widget.winfo_exists():
            return
        ancestor = widget
        while ancestor is not self.grid and ancestor is not self.window:
            ancestor = ancestor.master
        if ancestor is not self.grid:
            return
        y = widget.winfo_rooty() - self.grid.winfo_rooty()
        top = self.canvas.canvasy(0)
        visible = self.canvas.winfo_height()
        total = max(1, self.grid.winfo_height())
        if y < top:
            self.canvas.yview_moveto(max(0, (y - pixels(widget, 8)) / total))
        elif y + widget.winfo_height() > top + visible:
            self.canvas.yview_moveto(max(0, (y + widget.winfo_height() - visible + pixels(widget, 8)) / total))

    def refresh(self, message=""):
        self._generation += 1
        self._photos.clear()
        self._controls = [widget for widget in self._controls if widget.winfo_exists() and widget not in self.grid.winfo_children()]
        for child in self.grid.winfo_children():
            child.destroy()
        self._tiles.clear()
        self._controls = [widget for widget in self._controls if widget.winfo_exists()]
        entries = self.album.entries()
        pages = max(1, (len(entries) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self._page = min(self._page, pages - 1)
        for entry in entries[self._page * self.PAGE_SIZE:(self._page + 1) * self.PAGE_SIZE]:
            self._tile(entry)
        if not entries:
            self._label(self.grid, "还没有背景图片\n点击『导入图片』，添加你自己的背景。", muted=True).pack(pady=pixels(self.window, 40))
        self._columns = 0
        self._layout()
        self.page_label.configure(text=f"{self._page + 1} / {pages}")
        self.previous.configure(state="normal" if self._page > 0 else "disabled")
        self.next.configure(state="normal" if self._page < pages - 1 else "disabled")
        selected = sum(item.enabled for item in entries)
        current = next((item.name for item in entries if item.key == self.album.current_key), "无")
        summary = f"{len(entries)} 张背景 · {selected} 张参与切换 · 当前：{current}"
        self.status.configure(text=f"{summary}\n{message}" if message else summary)
        self.rhythm.configure(text={"startup": "只在下次启动时更换；本次使用期间保持不动。",
                                    "daily": "本地日期改变时更换一次；同一天重启不会再换。",
                                    "fixed": "使用你设为当前的图片，不自动更换。"}[self.album.mode])
        self.mode.set(MODES[self.album.mode])

    def _tile(self, entry):
        tile = SettingsCard(self.grid, inset=12, radius=16)
        self._tiles.append(tile)
        body = tile.body
        image_host = tk.Frame(body, width=pixels(body, 210), height=pixels(body, 120))
        set_color_roles(image_host, background="SUBTLE")
        image_host.pack(fill="x")
        image_host.pack_propagate(False)
        image = self._label(image_host, "加载缩略图…", muted=True, surface=True)
        image.pack(fill="both", expand=True)
        title = self._label(body, entry.name, bold=True, surface=True)
        title.configure(wraplength=pixels(body, 205))
        title.pack(fill="x", pady=(pixels(body, 10), pixels(body, 4)))
        Tooltip(title, entry.name)
        current = entry.key == self.album.current_key
        badge = self._label(body, "✓ 当前背景" if current else "导入副本" if entry.imported else "预置 / 旧目录图片", muted=not current, surface=True)
        badge.pack(fill="x", pady=(0, pixels(body, 6)))
        enabled = tk.BooleanVar(self.window, entry.enabled)
        check = tk.Checkbutton(body, text="参与更换", variable=enabled, command=lambda: self._enable(entry.key, enabled.get()),
                               font=(theme.BODY_FONT, 10), anchor="w", takefocus=True, highlightthickness=1)
        set_color_roles(check, background="WHITE", foreground="INK", activebackground="ACCENT_SOFT",
                        activeforeground="INK", selectcolor="WHITE", highlightbackground="WHITE", highlightcolor="ACCENT")
        check.pack(fill="x", pady=(0, pixels(body, 8)))
        check.bind("<FocusIn>", self._reveal, add=True)
        self._controls.append(check)
        actions = tk.Frame(body)
        set_color_roles(actions, background="WHITE")
        actions.pack(fill="x")
        use = self._button(actions, "当前使用" if current else "设为当前", lambda: self._current(entry.key), surface=True)
        use.pack(side="left", padx=(0, pixels(body, 4)))
        if current:
            use.configure(state="disabled")
        remove = self._button(actions, "删除" if entry.imported else "移出相册", lambda: self._remove(entry), surface=True)
        remove.pack(side="right")
        Tooltip(remove, "仅将应用导入副本放进回收目录；外部原图不删除。" if entry.imported else "只从相册隐藏；磁盘上的原图保留。")
        for widget in (tile, body, image_host, image, title, badge, check, actions, use, remove):
            self._wheel_bind(widget)
        generation = self._generation
        future = self._jobs.submit(render_faded_cover, entry.path, (pixels(body, 210), pixels(body, 120)),
                                   base_color=theme.palette()["WHITE"], fade_percent=0)
        future.add_done_callback(lambda result: self._results.put(("thumbnail", generation, image, result)))

    def _set_page(self, step):
        if self._busy:
            return
        self._page = max(0, self._page + step)
        self.refresh()
        self.canvas.yview_moveto(0)

    def _change_mode(self, _event=None):
        selected = next((key for key, label in MODES.items() if label == self.mode.get()), self.album.mode)
        self._change(lambda: self.album.set_mode(selected), "更换方式已保存；当前背景保持不动。")

    def _current(self, key):
        self._change(lambda: self.album.set_current(key), "已设为当前背景；聊天页面即时生效。")

    def _enable(self, key, enabled):
        self._change(lambda: self.album.set_enabled(key, enabled), "参与更换的图片已保存。")

    def _remove(self, entry):
        if self._busy:
            return
        text = ("仅删除应用导入副本，外部原图不动。\n副本将移入 data/background-trash，可重新导入恢复。"
                if entry.imported else "仅从相册中隐藏这张图片，不删除磁盘上的原图。")
        if messagebox.askyesno("删除背景" if entry.imported else "移出相册", text, parent=self.window):
            self._change(lambda: self.album.remove(entry.key), "已移出相册；外部原图没有删除。")

    def _change(self, operation, message):
        if self._busy:
            return
        yview = self.canvas.yview()
        try:
            operation()
            self.on_changed()
            self.refresh(message)
        except (OSError, RuntimeError, ValueError) as error:
            self.status.configure(text=f"未完成：{error}。请检查文件权限后重试。")
            self.mode.set(MODES[self.album.mode])
        self.window.after_idle(lambda: self.canvas.yview_moveto(yview[0]) if not self._closed else None)

    def _import(self):
        if self._busy:
            return
        paths = filedialog.askopenfilenames(parent=self.window, title="导入聊天背景",
                                           filetypes=(("图片", "*.png *.jpg *.jpeg *.webp *.avif *.bmp *.gif *.tif *.tiff"), ("所有文件", "*.*")))
        if not paths:
            return
        self._busy = True
        self.import_button.configure(state="disabled", text="正在导入…")
        self.status.configure(text=f"正在处理 {len(paths)} 张图片；外部原图不会更改。")
        self.mode_select.configure(state="disabled")
        for widget in self._controls:
            if widget.winfo_exists():
                widget.configure(state="disabled")
        def prepare():
            results = []
            for path in paths:
                try:
                    results.append((Path(path).name, prepare_import(Path(path)), None))
                except Exception as error:
                    results.append((Path(path).name, None, str(error)))
            return results
        future = self._jobs.submit(prepare)
        future.add_done_callback(lambda result: self._results.put(("import", 0, None, result)))

    def _drain(self):
        self._poll_job = None
        if self._closed:
            return
        from PIL import ImageTk
        for _ in range(12):
            try:
                kind, generation, widget, future = self._results.get_nowait()
            except queue.Empty:
                break
            if kind == "thumbnail":
                if generation != self._generation or not widget.winfo_exists():
                    continue
                try:
                    photo = ImageTk.PhotoImage(future.result(), master=self.window)
                    self._photos[str(widget)] = photo
                    widget.configure(image=photo, text="")
                except Exception:
                    widget.configure(text="图片无法读取\n可移出相册后重新导入")
            else:
                successes, errors = 0, []
                try:
                    for name, prepared, error in future.result():
                        try:
                            if error:
                                raise ValueError(error)
                            self.album.import_prepared(*prepared)
                            successes += 1
                        except (OSError, RuntimeError, ValueError) as failure:
                            errors.append(f"{name}：{failure}")
                    self.album.activate()
                    self.on_changed()
                except Exception as failure:
                    errors.append(str(failure))
                self._busy = False
                self.import_button.configure(state="normal", text="导入图片")
                self.mode_select.configure(state="readonly")
                for control in self._controls:
                    if control.winfo_exists():
                        control.configure(state="normal")
                self.refresh(f"已导入 {successes} 张；{len(errors)} 张未完成。外部原图保持原样。" if errors else f"已导入 {successes} 张图片，已勾选参与更换。")
                if errors:
                    messagebox.showwarning("部分背景未导入", "\n".join(errors[:8]), parent=self.window)
        self._poll_job = self.window.after(80, self._drain)

    def _destroyed(self, event):
        if event.widget is self.window:
            self.close(destroy=False)

    def close(self, destroy=True):
        if self._closed:
            return
        self._closed = True
        for job in (self._poll_job, self._layout_job):
            if job is not None:
                try:
                    self.window.after_cancel(job)
                except tk.TclError:
                    pass
        self._jobs.shutdown(wait=False, cancel_futures=True)
        if destroy and self.window.winfo_exists():
            self.window.destroy()
