"""ChatBackgroundViewMixin：ChatWindow 的单一界面职责；状态由宿主窗口持有。"""

from __future__ import annotations

import tkinter as tk
import queue

from ..chat_background import (
    BACKGROUND_ROTATION_MS, discover_background_files, pillow_available, render_faded_cover,
)
from ..background_album import BackgroundAlbum
from .theme import APP_BG
from .message_rows import position_visible_message_rows

try:
    from PIL import Image, ImageTk
except ImportError:
    ImageTk = None


class ChatBackgroundViewMixin:
    """仅供 ChatWindow 组合使用；不持有独立模型、数据库或请求线程。"""

    def _initialize_background_album(self) -> None:
        """Choose once per launch, before the first chat background paint."""
        self._background_album = None
        self._background_album_error = ""
        self._background_album_window = None
        try:
            self._background_album = BackgroundAlbum(self.service.config.root)
            selected = self._background_album.activate(startup=True)
            self._background_paths = [selected] if selected else []
        except (OSError, RuntimeError, ValueError) as error:
            self._background_album_error = str(error)
            # Chat remains available, but do not overwrite a broken manifest.
            self._background_paths = discover_background_files(self.service.config.root)[:1]
        self._background_index = 0

    def _refresh_background_album(self) -> None:
        album = getattr(self, "_background_album", None)
        if album is None:
            return
        selected = album.activate()
        paths = [selected] if selected else []
        changed = paths != self._background_paths
        self._background_paths = paths
        self._background_index = 0
        if changed:
            self._background_generation=getattr(self,'_background_generation',0)+1
            self._background_pending_generation=None
            self._background_request_key=None
            self._background_render_key = None
            self._schedule_background_render()
        label = getattr(self, "background_album_status", None)
        if label is not None and label.winfo_exists():
            entries = album.entries()
            current = next((item.name for item in entries if item.key == album.current_key), "无")
            from ..background_album import MODES
            label.configure(text=f"{len(entries)} 张 · {sum(item.enabled for item in entries)} 张参与切换 · {MODES[album.mode]} · 当前：{current}")

    def _open_background_album(self) -> None:
        from tkinter import messagebox
        from .background_album import BackgroundAlbumWindow
        current = getattr(self, "_background_album_window", None)
        if current is not None and not current._closed and current.window.winfo_exists():
            current.window.deiconify()
            current.window.lift()
            return
        try:
            if getattr(self, "_background_album", None) is None:
                self._background_album = BackgroundAlbum(self.service.config.root)
            self._background_album_window = BackgroundAlbumWindow(
                self, self._background_album, self._refresh_background_album,
            )
        except (OSError, RuntimeError, ValueError) as error:
            messagebox.showerror("背景相册暂时无法打开", f"{error}\n请检查 data/background-album.json 的格式和写入权限。\n原图片和配置未覆盖。", parent=self.root)

    def _message_background_crop(self, widget, width, height):
        """采样固定在视口的真实背景，不重读磁盘或重新缩放整张图片。"""
        image = getattr(self, "_background_pixels", None)
        if image is None:
            return None
        x = widget._message_world_x
        y = widget._message_world_y - int(self.message_canvas.canvasy(0))
        y += getattr(widget, "_paint_top", 0)
        return image.crop((x, y, x + width, y + height))

    def _message_visible_region(self, widget, height):
        """Return a bubble-local viewport slice, never a full long-answer image."""
        if not hasattr(widget, "_message_world_y"):
            return None
        y = widget._message_world_y - int(self.message_canvas.canvasy(0))
        top = max(0, -y)
        bottom = min(height, self.message_canvas.winfo_height() - y)
        return (top, bottom) if bottom > top else None

    def _repaint_visible_bubbles(self):
        self._bubble_paint_pending = None
        if getattr(self, "_closing", False) or not self.message_canvas.winfo_ismapped():
            return
        for widget in getattr(self, "_message_labels", ()):
            if widget.winfo_exists() and hasattr(widget, "_height") and hasattr(widget, "paint"):
                if (self._message_visible_region(widget, widget._height) is not None
                        or getattr(widget, "_photo", None) is not None):
                    widget.paint()

    def _schedule_bubble_paint(self):
        if (not getattr(self, "_closing", False) and self.message_canvas.winfo_ismapped()
                and getattr(self, "_bubble_paint_pending", None) is None):
            self._bubble_paint_pending = self.root.after_idle(self._repaint_visible_bubbles)

    def _position_chat_background(self) -> None:
        """Position before Tk's Canvas paint pass, never one pass behind it."""
        self._commit_chat_viewport()

    def _commit_chat_viewport(self) -> None:
        """Mount and paint one consistent final slice after scroll input settles."""

        if not hasattr(self, "chat_background_label"):
            return
        try:
            if (not self.chat_background_label.winfo_exists()
                    or not self.message_canvas.winfo_ismapped()):
                return
        except tk.TclError:
            return
        width = max(1, self.message_canvas.winfo_width())
        height = max(1, self.message_canvas.winfo_height())
        visible_top = int(self.message_canvas.canvasy(0))
        position_visible_message_rows(self)
        self._repaint_visible_bubbles()
        if self.message_canvas.coords(self._background_window) != [0.0, float(visible_top)]:
            self.message_canvas.coords(self._background_window, 0, visible_top)

    def _background_widget_exists(self) -> bool:
        try:
            return bool(
                self.root.winfo_exists()
                and self.chat_background_label.winfo_exists()
            )
        except tk.TclError:
            return False

    def _schedule_background_render(self) -> None:
        """窗口连续缩放时做短暂防抖，只渲染最终尺寸。"""

        if self._background_resize_after_id is not None:
            try:
                self.root.after_cancel(self._background_resize_after_id)
            except tk.TclError:
                pass
            self._background_resize_after_id = None
        if not self._background_widget_exists() or not self.message_canvas.winfo_ismapped():
            return  # A Map callback will render the latest album/fade/size.
        self._background_resize_after_id = self.root.after(
            33,
            self._render_chat_background,
        )

    def _render_chat_background(self, *, force: bool = False) -> None:
        self._background_resize_after_id = None
        if not self._background_widget_exists() or not self.message_canvas.winfo_ismapped():
            return
        if (
            not self._background_paths
            or not pillow_available()
            or ImageTk is None
        ):
            print(
                "[背景] 未启用："
                f"图片={len(self._background_paths)}, "
                f"Pillow={pillow_available()}, ImageTk={ImageTk is not None}",
                flush=True,
            )
            self.chat_background_label.configure(image="", bg=APP_BG)
            self._background_generation=getattr(self,'_background_generation',0)+1
            self._background_pending_generation=None
            self._background_request_key=None
            self._background_photo = None
            self._background_pixels = None
            self._schedule_bubble_paint()
            return
        width = max(1, self.message_canvas.winfo_width())
        height = max(1, self.message_canvas.winfo_height())
        if width < 8 or height < 8:
            return
        fade_percent = min(
            100,
            max(0, round(self.background_fade.get())),
        )
        if fade_percent >= 100:
            self._background_request_key=None
            self._background_pending_generation=None
            self._background_generation=getattr(self,'_background_generation',0)+1
            self.chat_background_label.configure(image="", bg=APP_BG)
            self._background_photo = None
            self._background_pixels = None
            self._background_render_key = ("fully-faded", width, height)
            self._position_chat_background()
            return

        # 某张文件损坏时继续尝试下一张，不能让背景影响聊天主功能。
        attempts = len(self._background_paths)
        for _ in range(attempts):
            path = self._background_paths[
                self._background_index % len(self._background_paths)
            ]
            try:
                render_key = (path, path.stat().st_mtime_ns, width, height, fade_percent)
                if not force and render_key == self._background_render_key:
                    self._position_chat_background()
                    return
                request_key=(*render_key,APP_BG)
                if not force and request_key==getattr(self,'_background_request_key',None):
                    return
                from .background_render import BackgroundRenderQueue
                if getattr(self,'_background_worker',None) is None:
                    self._background_worker=BackgroundRenderQueue()
                self._background_generation=getattr(self,'_background_generation',0)+1
                self._background_pending_generation=self._background_generation
                self._background_request_key=request_key
                self._background_worker.submit(self._background_generation,request_key,path,
                                               (width,height),APP_BG,fade_percent)
                if getattr(self,'_background_poll_after_id',None) is None:
                    self._background_poll_after_id=self.root.after(16,self._accept_background_pixels)
                return
            except (OSError, RuntimeError, ValueError) as error:
                print(
                    f"[背景] 无法载入 {path}: "
                    f"{type(error).__name__}: {error}",
                    flush=True,
                )
                self._background_index = (
                    self._background_index + 1
                ) % len(self._background_paths)
                continue

        self.chat_background_label.configure(image="", bg=APP_BG)
        self._background_photo = None
        self._background_pixels = None
        self._schedule_bubble_paint()

    def _accept_background_pixels(self):
        self._background_poll_after_id=None
        if getattr(self,'_closing',False) or not self._background_widget_exists():
            return
        worker=getattr(self,'_background_worker',None)
        if worker is None:return
        if getattr(self,'_background_pending_generation',None) is None:return
        found=None
        while True:
            try:item=worker.results.get_nowait()
            except queue.Empty:break
            if item[0]==getattr(self,'_background_generation',0):found=item
        if found is None:
            self._background_poll_after_id=self.root.after(16,self._accept_background_pixels)
            return
        _,key,rendered,error,milliseconds=found
        self._background_pending_generation=None
        self._background_last_decode_ms=milliseconds
        # Hidden chat/changed fade/resize can complete out of order. Do not
        # present an obsolete bitmap or revive a hidden page's expensive paint.
        path,stamp,width,height,fade,color=key
        current=(self.message_canvas.winfo_width(),self.message_canvas.winfo_height(),round(self.background_fade.get()))
        selected=(self._background_paths[self._background_index%len(self._background_paths)]
                  if self._background_paths else None)
        try:current_stamp=path.stat().st_mtime_ns
        except OSError:current_stamp=None
        if (not self.message_canvas.winfo_ismapped() or current!=(width,height,fade)
                or path!=selected or stamp!=current_stamp or color!=APP_BG):
            self._background_request_key=None
            return
        if error:
            self._background_request_key=None
            self._background_index=(self._background_index+1)%max(1,len(self._background_paths))
            print('[背景] 后台解码失败：'+error,flush=True)
            failures=getattr(self,'_background_failed_paths',set())
            failures.add(path)
            self._background_failed_paths=failures
            if len(failures)<len(self._background_paths):
                self._render_chat_background(force=True)
            else:
                self.chat_background_label.configure(image='',bg=APP_BG)
                self._background_photo=self._background_pixels=None
                self._schedule_bubble_paint()
            return
        self._background_failed_paths=set()
        self._background_photo=ImageTk.PhotoImage(rendered,master=self.root)
        self._background_pixels=rendered
        self._background_render_key=key[:5]
        self.chat_background_label.configure(image=self._background_photo,bg=APP_BG)
        self._position_chat_background()

    def _schedule_background_rotation(self) -> None:
        if not self._background_widget_exists():
            return
        self._background_rotation_after_id = self.root.after(
            BACKGROUND_ROTATION_MS,
            self._rotate_chat_background,
        )

    def _rotate_chat_background(self) -> None:
        """Only advance at a date boundary in daily mode; no slideshow."""

        if not self._background_widget_exists():
            return
        self._background_rotation_after_id = None
        try:
            self._refresh_background_album()
        except (OSError, RuntimeError, ValueError) as error:
            self._background_album_error = str(error)
        self._schedule_background_rotation()
