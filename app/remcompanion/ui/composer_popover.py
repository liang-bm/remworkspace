"""Grouped composer tools: no model calls and no permission escalation."""
from __future__ import annotations

import tkinter as tk
from . import theme
from .scroll_surface import ScrollCanvas


class ComposerPopover(tk.Toplevel):
    def __init__(self, owner, anchor, groups, *, tr=lambda text: text):
        super().__init__(owner)
        self.withdraw()
        self.overrideredirect(True)
        self.transient(owner)
        self._anchor = anchor
        self._prior_focus = owner.focus_get()
        self._closed = False
        self._groups = groups
        self._tr = tr
        self._remchat_locale_exclude = True
        colors = theme.palette()
        from .dpi import pixels
        self.configure(bg=colors["LINE"])
        shell = tk.Frame(self, bg=colors["WHITE"])
        shell.pack(fill="both", expand=True, padx=1, pady=1)
        self._viewport = ScrollCanvas(shell, bg=colors["WHITE"], bd=0, highlightthickness=0)
        self._vertical = tk.Scrollbar(shell, command=self._viewport.yview)
        self._vertical.pack(side="right", fill="y")
        self._horizontal = tk.Scrollbar(shell, orient="horizontal", command=self._viewport.xview)
        self._horizontal.pack(side="bottom", fill="x")
        self._viewport.configure(yscrollcommand=self._vertical.set, xscrollcommand=self._horizontal.set)
        self._viewport.pack(fill="both", expand=True)
        body = self._body = tk.Frame(self._viewport, bg=colors["WHITE"], padx=12, pady=12)
        self._viewport.create_window(0, 0, window=body, anchor="nw")
        body.bind("<Configure>", lambda _: self._viewport.configure(scrollregion=self._viewport.bbox("all")))
        self.bind("<MouseWheel>", lambda event: self._viewport.yview_scroll(-int(event.delta / 120), "units"), add="+")
        nav = tk.Frame(body, bg=colors["WHITE"])
        nav.pack(side="left", fill="y", padx=(0, 12))
        self.detail = tk.Frame(body, bg=colors["WHITE"], width=pixels(self, 280))
        self.detail.pack(side="left", fill="both", expand=True)
        self._nav = []
        for index, (name, _items) in enumerate(groups):
            button = tk.Button(nav, text=tr(name), anchor="w", bd=0,
                font=(theme.BODY_FONT, 10), padx=14, pady=12, wraplength=pixels(self, 180), cursor="hand2",
                command=lambda n=index: self._show(n), takefocus=True)
            button.pack(fill="x", pady=(0, 5))
            self._nav.append(button)
            button.bind("<FocusIn>", lambda event: self._reveal(event.widget))
        self._show(0)
        self.bind("<Escape>", lambda _: self.close())
        self.bind("<Button-1>", self._outside, add="+")
        self.bind("<FocusOut>", lambda _: self.after_idle(self._lost_focus), add="+")
        self.update_idletasks()
        self._place_near_anchor()
        self.deiconify()
        self.grab_set()
        self._nav[0].focus_set()

    def _show(self, index):
        colors = theme.palette()
        from .dpi import pixels
        for n, button in enumerate(self._nav):
            button.configure(bg=colors["ACCENT_SOFT"] if n == index else colors["WHITE"],
                fg=colors["INK"], activebackground=colors["ACCENT_SOFT"], activeforeground=colors["INK"])
        for child in self.detail.winfo_children():
            child.destroy()
        name, items = self._groups[index]
        tk.Label(self.detail, text=self._tr(name), anchor="w", bg=colors["WHITE"],
                 fg=colors["MUTED"], font=(theme.BODY_FONT, 9)).pack(fill="x", pady=(0, 8))
        for item in items:
            title, description, action = item[:3]
            # Existing three-part tool entries remain enabled. Mode entries can
            # expose their reason while honestly disabling unavailable choices.
            enabled = item[3] if len(item) > 3 else True
            button = tk.Button(self.detail, text=self._tr(title) + "\n" + self._tr(description),
                justify="left", anchor="w", wraplength=pixels(self, 265), bg=colors["APP_BG"], fg=colors["INK"],
                activebackground=colors["ACCENT_SOFT"], activeforeground=colors["INK"],
                bd=0, highlightthickness=1, highlightbackground=colors["LINE"],
                highlightcolor=colors["ACCENT"], font=(theme.BODY_FONT, 10),
                padx=12, pady=10, cursor="hand2" if enabled else "arrow", takefocus=enabled,
                state="normal" if enabled else "disabled", disabledforeground=colors["MUTED"],
                command=lambda callback=action: self._run(callback))
            button.pack(fill="x", pady=(0, 6))
            button.bind("<FocusIn>", lambda event: self._reveal(event.widget))
        self._viewport.yview_moveto(0)
        self._viewport.xview_moveto(0)
        # Switching sections keeps the panel anchored without truncating rows.
        if self.winfo_viewable():
            self.update_idletasks()
            self._place_near_anchor()

    def _place_near_anchor(self):
        """Every section, including a wider translation, obeys the same bounds."""
        from .dpi import pixels
        # Reserve space for the OS taskbar and keep every item reachable by
        # scrollbar or Tab even with long translations / high-DPI displays.
        content_width, content_height = self._body.winfo_reqwidth(), self._body.winfo_reqheight()
        max_width = max(240, self.winfo_screenwidth() - pixels(self, 32))
        max_height = max(180, self.winfo_screenheight() - pixels(self, 72))
        vertical = content_height + 2 > max_height
        horizontal = content_width + (self._vertical.winfo_reqwidth() if vertical else 0) + 2 > max_width
        if vertical:
            self._vertical.pack(side="right", fill="y", before=self._viewport)
        else:
            self._vertical.pack_forget()
        if horizontal:
            self._horizontal.pack(side="bottom", fill="x", before=self._viewport)
        else:
            self._horizontal.pack_forget()
        width = min(max_width, content_width + (self._vertical.winfo_reqwidth() if vertical else 0) + 2)
        height = min(max_height, content_height + (self._horizontal.winfo_reqheight() if horizontal else 0) + 2)
        self._viewport.configure(width=width, height=height, scrollregion=(0, 0, content_width, content_height))
        x = min(max(8, self._anchor.winfo_rootx()), max(8, self.winfo_screenwidth() - width - 8))
        above = self._anchor.winfo_rooty() - height - 8
        y = above if above >= 0 else self._anchor.winfo_rooty() + self._anchor.winfo_height() + 8
        y = min(max(8, y), max(8, self.winfo_screenheight() - pixels(self, 64) - height))
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _reveal(self, widget):
        """Keyboard focus scrolls into view, without changing the selected mode."""
        self.update_idletasks()
        top = widget.winfo_rooty() - self._body.winfo_rooty()
        bottom = top + widget.winfo_height()
        current = self._viewport.canvasy(0)
        viewport_height = self._viewport.winfo_height()
        total = max(1, self._body.winfo_reqheight())
        if top < current:
            self._viewport.yview_moveto(top / total)
        elif bottom > current + viewport_height:
            self._viewport.yview_moveto((bottom - viewport_height) / total)
        left = widget.winfo_rootx() - self._body.winfo_rootx()
        right = left + widget.winfo_width()
        current_x = self._viewport.canvasx(0)
        viewport_width = self._viewport.winfo_width()
        total_width = max(1, self._body.winfo_reqwidth())
        if left < current_x:
            self._viewport.xview_moveto(left / total_width)
        elif right > current_x + viewport_width:
            self._viewport.xview_moveto((right - viewport_width) / total_width)

    def _run(self, callback):
        self.close()
        callback()

    def _outside(self, event):
        if not (self.winfo_rootx() <= event.x_root < self.winfo_rootx() + self.winfo_width()
                and self.winfo_rooty() <= event.y_root < self.winfo_rooty() + self.winfo_height()):
            self.close()
            return "break"

    def _lost_focus(self):
        if self._closed:
            return
        focus = self.focus_get()
        if focus is None or focus.winfo_toplevel() is not self:
            self.close(restore=False)

    def close(self, *, restore=True):
        if self._closed:
            return
        self._closed = True
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        if restore and self._prior_focus and self._prior_focus.winfo_exists():
            # Escape/item selection explicitly returns to this app. Windows
            # may leave the owner inactive after an override-redirect popup,
            # so focus_set alone can silently fail. Losing focus elsewhere
            # closes with restore=False and never activates the app again.
            if self._prior_focus.winfo_viewable():
                self._prior_focus.focus_force()
