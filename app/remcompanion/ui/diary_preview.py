"""Full-document Markdown preview with a bounded native-window slice."""
from __future__ import annotations

import tkinter as tk
from .scroll_surface import ScrollCanvas
from tkinter import ttk
from . import theme
from .message_bubble import MessageBubble
from .theme_controller import set_color_roles


class _DiaryBubble(MessageBubble):
    OPACITY = 0

    def fit(self, viewport_width):
        self._expanded = True
        super().fit(viewport_width)
        changed = False
        if self._foldable:
            self._foldable = False
            self._height -= self._footer_height
            changed = True
        # A document preview is a steady reading column, not a short-message
        # bubble that narrows to the longest line of the current paragraph.
        width = round(max(64, min(viewport_width - 64, int(viewport_width * .80), self._font.measure("0") * 70)))
        if width != self._width:
            self._width = width
            super().configure(width=width)
            changed = True
        if changed:
            self._paint_key = None
            self.paint()

    def toggle_expanded(self):
        return "break"  # Journals are always full documents, never collapsed chat replies.

    def _draw_text(self, x, y, **options):
        # Unlike chat over arbitrary photographs, diary has a guaranteed
        # theme surface. Plain glyphs retain the established contrast without
        # adding four outlined halo glyphs to every reading fragment.
        return self.create_text(x, y - self._display_top, **options)


class DiaryPreview(tk.Frame):
    """Mount a bounded physical slice of a full shared Markdown document."""
    def __init__(self, parent):
        super().__init__(parent)
        set_color_roles(self, background="WHITE")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = ScrollCanvas(self, width=1, height=1, highlightthickness=0, bd=0)
        set_color_roles(self.canvas, background="APP_BG")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        # Shared renderer's background cache contract: this host has a fixed
        # theme surface rather than external images. Repeated Configure paints
        # therefore reuse geometry/paint until theme or visible slice changes.
        self.message_canvas = self.canvas
        self._background_pixels = None
        self._background_render_key = "diary-theme-surface"
        scroll = ttk.Scrollbar(self, command=self._scroll)
        scroll.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scroll.set)
        self.bubble = _DiaryBubble(self.canvas, role="assistant", font=(theme.BODY_FONT, 11),
                                    background=self._background, on_layout=self._layout,
                                    visible_region=self._visible_region,
                                    render_fenced_markdown=False, show_role=False)
        self.bubble._expanded = True
        self._window = self.canvas.create_window(0, 0, window=self.bubble, anchor="nw")
        self._layout_job = None
        self.canvas.bind("<Configure>", self._schedule)
        for target in (self.canvas, self.bubble):
            target.bind("<MouseWheel>", self._wheel, add=True)
            target.bind("<Button-4>", lambda _event: self._scroll("scroll", -3, "units"), add=True)
            target.bind("<Button-5>", lambda _event: self._scroll("scroll", 3, "units"), add=True)
        self.bind("<Map>", self._schedule, add=True)
        self.bind("<Destroy>", self._destroy, add=True)

    def _background(self, *_arguments):
        return None

    def set_text(self, text):
        self.bubble.insert("1.0", text)
        self.bubble._layout_key = None
        self.canvas.yview_moveto(0)
        self._schedule()

    def _schedule(self, _event=None):
        if self._layout_job is None:
            self._layout_job = self.after_idle(self._layout)

    def _layout(self):
        self._layout_job = None
        if not self.winfo_exists() or not self.winfo_ismapped():
            return
        # The bubble's 80% chat width is counterbalanced by a host viewport;
        # native geometry remains within the actual preview width.
        self.bubble.fit(max(120, self.canvas.winfo_width() / .80))
        height = self.bubble._height
        self.canvas.configure(scrollregion=(0, 0, max(1, self.canvas.winfo_width()), height))
        self._mount()

    def _visible_region(self, _bubble, height):
        top = max(0, int(self.canvas.canvasy(0)))
        bottom = min(height, top + max(1, self.canvas.winfo_height()))
        return (top, max(top + 1, bottom))

    def _mount(self):
        if not hasattr(self.bubble, "_height"):
            return
        top, bottom = self._visible_region(self.bubble, self.bubble._height)
        self.bubble.set_visible_slice(top, bottom)
        self.canvas.coords(self._window, 0, top)
        self.bubble.paint()

    def _scroll(self, *arguments):
        self.canvas.yview(*arguments)
        self._mount()
        return "break"

    def _wheel(self, event):
        return self._scroll("scroll", -int(event.delta / 120) * 3, "units")

    def refresh_theme(self):
        self.bubble._paint_key = None
        self._schedule()

    def _destroy(self, event):
        if event.widget is self and self._layout_job is not None:
            self.after_cancel(self._layout_job)
            self._layout_job = None
