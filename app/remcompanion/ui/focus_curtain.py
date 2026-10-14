"""App-owned wallpaper curtain; one session, no desktop capture or key hooks.

THESIS: Starting focus quiets the desktop without locking the user into it.
OWN-WORLD: Current Rem wallpaper, blue timer, readable dark-blue mask.
STORY: Stay with the remaining time and Rem; pause or leave with one action.
FIRST VIEWPORT: Wallpaper fills each monitor, a countdown sits at its center.
FORM: The explicitly requested immersive focus state, not a new app shell.
"""
from __future__ import annotations

from collections.abc import Callable
import math
from pathlib import Path
import queue
import threading
import tkinter as tk

from PIL import Image, ImageOps, ImageTk

from ..platforms import native_module
from .dpi import pixels
from .font_source import native_font


CURTAIN_BG = "#142235"
CURTAIN_INK = "#F5F8FC"
CURTAIN_MUTED = "#B9CDDF"
CURTAIN_BLUE = "#91C9EF"


class FocusCurtain:
    def __init__(self, parent: tk.Misc, *, snapshot: Callable[[], tuple[float, float, bool]],
                 on_pause: Callable[[], None], on_end: Callable[[], None],
                 keep_pet_visible: Callable[[], None] = lambda: None) -> None:
        self.parent = parent
        self.snapshot = snapshot
        self.on_pause = on_pause
        self.on_end = on_end
        self.keep_pet_visible = keep_pet_visible
        self.windows: list[tk.Toplevel] = []
        self.views = []
        self._photos = {}
        self._results = queue.Queue()
        self._stop = threading.Event()
        self._animation = None
        self._poll = None
        self._generation = 0
        self._closed = True

    def _t(self, source, **values):
        translator = getattr(self.parent, "_remchat_translator", None)
        return translator(source, **values) if translator else source.format(**values)

    def show(self, wallpaper: Path | None = None) -> None:
        if not self._closed:
            self.raise_pet()
            return
        self._closed = False
        self._generation += 1
        generation = self._generation
        self._stop = threading.Event()
        # The callback always refers to this generation's stop event, never a
        # future session's replacement; late decoded images cannot reappear.
        stop = self._stop
        try:
            rects = native_module("windowing").monitor_rects(self.parent)
            if not rects:
                raise OSError("没有可显示专注幕布的屏幕")
            for rect in rects:
                self._add_window(rect)
            self.windows[0].focus_force()  # once on the user's Start action
            self.raise_pet()
            self._animate()
            if wallpaper is not None:
                sizes = {(rect.width, rect.height) for rect in rects}
                threading.Thread(target=self._render_wallpaper,
                                 args=(generation, stop, Path(wallpaper), sizes),
                                 name="rem-focus-wallpaper", daemon=True).start()
                self._poll = self.parent.after(40, self._poll_wallpaper)
        except Exception:
            self.close()
            raise

    def _add_window(self, rect):
        window = tk.Toplevel(self.parent)
        window.withdraw()
        # Intentionally excluded from normal app recolouring/rounded child
        # masks: a curtain is a complete opaque rectangular monitor surface.
        window._remchat_theme_exclude = True
        window._remchat_locale_exclude = True
        self.windows.append(window)
        window.title("RemChat · 专注")
        window.overrideredirect(True)
        window.configure(bg=CURTAIN_BG)
        window.attributes("-topmost", True)
        window.protocol("WM_DELETE_WINDOW", self._end)
        window.bind("<Escape>", self._end)
        window.bind("<space>", self._pause)
        window.bind("<FocusIn>", lambda _event: self.raise_pet(), add=True)
        window.bind("<Destroy>", self._destroyed, add=True)
        canvas = tk.Canvas(window, bd=0, highlightthickness=0, bg=CURTAIN_BG)
        canvas.pack(fill="both", expand=True)
        cx, cy = rect.width / 2, rect.height * .44
        radius = min(pixels(window, 165), rect.height * .27, rect.width * .30)
        stroke = max(3, pixels(window, 4))
        wallpaper_id = canvas.create_image(0, 0, anchor="nw")
        canvas.create_oval(cx-radius, cy-radius, cx+radius, cy+radius,
                           outline="#4D647A", width=stroke)
        arc = canvas.create_arc(cx-radius, cy-radius, cx+radius, cy+radius,
                                start=90, extent=0, style="arc", outline=CURTAIN_BLUE, width=stroke)
        dot = canvas.create_oval(0, 0, 0, 0, fill=CURTAIN_BLUE, outline="")
        heading_font = native_font(window, family="Microsoft YaHei UI", size=13)
        time_font = native_font(window, family="Microsoft YaHei UI",
                                size=-round(radius * .38), weight="bold")
        caption_font = native_font(window, family="Microsoft YaHei UI", size=10)
        heading = canvas.create_text(cx, cy-radius*.46, text=self._t("专注进行中"), font=heading_font, fill=CURTAIN_MUTED)
        time_text = canvas.create_text(cx, cy, text="", font=time_font, fill=CURTAIN_INK)
        caption = canvas.create_text(cx, cy+radius*.42, text="", font=caption_font, fill=CURTAIN_MUTED)
        controls = tk.Frame(canvas, bg=CURTAIN_BG, bd=1, relief="flat",
                            padx=pixels(window, 8), pady=pixels(window, 6))
        from .rounded_controls import RoundedChrome
        controls._curtain_rounding = RoundedChrome(controls)
        pause = self._button(controls, self._t("暂停"), self._pause, primary=True)
        pause.pack(side="left", padx=(0, pixels(window, 12)))
        self._button(controls, self._t("结束专注"), self._end).pack(side="left")
        gap = min(pixels(window, 42), max(pixels(window, 18), (rect.height-cy-radius)/3))
        control_id = canvas.create_window(cx, cy+radius+gap, window=controls)
        escape = canvas.create_text(cx, cy+radius+2*gap, text=self._t("Esc 退出"), font=caption_font,
                                    fill=CURTAIN_MUTED)
        self.views.append({"window": window, "canvas": canvas, "rect": rect,
                           "wallpaper": wallpaper_id, "arc": arc, "dot": dot,
                           "heading": heading, "time": time_text, "caption": caption,
                           "pause": pause, "center": (cx, cy), "radius": radius,
                           "fonts": (heading_font, time_font, caption_font), "shown": None,
                           "controls": control_id, "escape": escape})
        window.deiconify()
        native_module("windowing").place_focus_window(window, rect)
        window.lift()

    @staticmethod
    def _button(parent, text, command, *, primary=False):
        return tk.Button(parent, text=text, command=command, bg="#276C9D" if primary else "#273D53",
                         fg=CURTAIN_INK, activebackground="#347FAF" if primary else "#36546E",
                         activeforeground=CURTAIN_INK, relief="flat", bd=0,
                         highlightthickness=1, highlightbackground=CURTAIN_BG, highlightcolor=CURTAIN_BLUE,
                         padx=22, pady=11, cursor="hand2", takefocus=True,
                         font=("Microsoft YaHei UI", 11))

    def _render_wallpaper(self, generation, stop, path, sizes):
        try:
            with Image.open(path) as source:
                original = ImageOps.exif_transpose(source).convert("RGB")
            for size in sizes:
                if stop.is_set():
                    break
                # Render only once per distinct monitor size. No screenshot,
                # screen capture, repeatedly resized blur or per-frame bitmap.
                cover = ImageOps.fit(original, size, method=Image.Resampling.LANCZOS)
                masked = Image.blend(cover, Image.new("RGB", size, CURTAIN_BG), .78)
                if not stop.is_set():
                    self._results.put((generation, size, masked))
        except (OSError, ValueError, Image.DecompressionBombError):
            pass  # opaque readable curtain remains usable with a bad picture
        finally:
            if not stop.is_set():
                self._results.put((generation, None, None))

    def _poll_wallpaper(self):
        self._poll = None
        if self._closed:
            return
        done = False
        try:
            while True:
                generation, size, image = self._results.get_nowait()
                if generation != self._generation:
                    continue
                if size is None:
                    done = True
                    continue
                photo = ImageTk.PhotoImage(image, master=self.parent)
                self._photos[size] = photo
                for view in self.views:
                    if (view["rect"].width, view["rect"].height) == size:
                        view["canvas"].itemconfigure(view["wallpaper"], image=photo)
        except queue.Empty:
            pass
        if not done:
            self._poll = self.parent.after(40, self._poll_wallpaper)

    def _animate(self):
        self._animation = None
        if self._closed:
            return
        remaining, total, paused = self.snapshot()
        progress = min(1.0, max(0.0, 1 - remaining / max(1, total)))
        seconds = math.ceil(max(0.0, remaining))
        minutes, seconds = divmod(seconds, 60)
        shown = (minutes, seconds, paused, total)
        for view in self.views:
            canvas, radius = view["canvas"], view["radius"]
            cx, cy = view["center"]
            canvas.itemconfigure(view["arc"], extent=-min(359.99, progress * 360))
            angle = math.pi / 2 - progress * 2 * math.pi
            x, y = cx+radius*math.cos(angle), cy-radius*math.sin(angle)
            dot_radius = pixels(canvas, 5)
            canvas.coords(view["dot"], x-dot_radius, y-dot_radius, x+dot_radius, y+dot_radius)
            if view["shown"] != shown:
                canvas.itemconfigure(view["time"], text=f"{minutes:02d}:{seconds:02d}")
                canvas.itemconfigure(view["heading"], text=self._t("专注已暂停" if paused else "专注进行中"))
                canvas.itemconfigure(view["caption"], text=self._t("{minutes} 分钟", minutes=round(total / 60)))
                view["pause"].configure(text=self._t("继续" if paused else "暂停"))
                view["shown"] = shown
        self._animation = self.parent.after(100 if paused else 33, self._animate)

    def raise_pet(self):
        if not self._closed:
            self.keep_pet_visible()

    def _pause(self, _event=None):
        # A native Button's class binding already invokes its command on
        # Space. Its subsequent Toplevel bindtag must not toggle a second time.
        if _event is not None and isinstance(_event.widget, tk.Button):
            return None
        if not self._closed:
            self.on_pause()
        return "break"

    def _end(self, _event=None):
        if not self._closed:
            self.on_end()
        return "break"

    def _destroyed(self, event):
        if not self._closed and event.widget in self.windows:
            # Losing any curtain must not leave the other monitors covered.
            self._end()

    def close(self):
        self._closed = True
        self._stop.set()
        for name in ("_animation", "_poll"):
            job = getattr(self, name)
            if job is not None:
                try:
                    self.parent.after_cancel(job)
                except tk.TclError:
                    pass
                setattr(self, name, None)
        for window in self.windows:
            try:
                if window.winfo_exists():
                    window.destroy()
            except tk.TclError:
                pass
        self.windows.clear()
        self.views.clear()
        # Images are always detached/dropped on this UI thread, not worker GC.
        self._photos.clear()
        while True:
            try:
                self._results.get_nowait()
            except queue.Empty:
                break
