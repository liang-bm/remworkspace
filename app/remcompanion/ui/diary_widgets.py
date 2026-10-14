"""Native diary controls and a wrapping action row; theme roles stay shared."""
from __future__ import annotations

import tkinter as tk
from .soft_fields import SoftEntry
from . import theme
from .dpi import pixels
from .theme_controller import set_color_roles


def _frame(parent, role="WHITE"):
    widget = tk.Frame(parent, bd=0)
    set_color_roles(widget, background=role)
    return widget


def _label(parent, text="", *, size=10, bold=False, muted=False):
    widget = tk.Label(parent, text=text, anchor="w", font=(theme.BODY_FONT, size, "bold" if bold else "normal"))
    set_color_roles(widget, background="WHITE", foreground="MUTED" if muted else "INK")
    return widget


def _button(parent, text, command, *, primary=False):
    widget = tk.Button(parent, text=text, command=command, relief="flat", bd=0,
                       font=(theme.BODY_FONT, 10), padx=10, pady=7, cursor="hand2", takefocus=True)
    set_color_roles(widget, background="ACCENT_DEEP" if primary else "ACCENT_SOFT",
                    foreground="ON_ACCENT" if primary else "INK",
                    activebackground="ACCENT_HOVER" if primary else "ACCENT_SOFT",
                    activeforeground="ON_ACCENT" if primary else "INK")
    return widget


def _entry(parent, variable, **options):
    widget = SoftEntry(parent, textvariable=variable, relief="flat", bd=0,
                      highlightthickness=1, font=(theme.BODY_FONT, 10), **options)
    set_color_roles(widget, background="APP_BG", foreground="INK", insertbackground="INK",
                    highlightbackground="LINE", highlightcolor="ACCENT",
                    selectbackground="ACCENT_SOFT", selectforeground="INK")
    return widget


class _Toolbar(tk.Frame):
    """Wrap genuine native actions rather than clip a long command row."""
    def __init__(self, parent):
        super().__init__(parent, bd=0)
        set_color_roles(self, background="WHITE")
        self.buttons = []
        self.hidden = set()
        self._job = None
        self.bind("<Configure>", self._schedule)
        self.bind("<Destroy>", self._destroy, add=True)

    def add(self, text, command, *, primary=False):
        button = _button(self, text, command, primary=primary)
        self.buttons.append(button)
        self._schedule()
        return button

    def _schedule(self, _event=None):
        if self._job is None:
            self._job = self.after_idle(self._layout)

    def show(self, button, visible=True):
        self.hidden.discard(button) if visible else self.hidden.add(button)
        self._schedule()

    def _layout(self):
        self._job = None
        width = max(1, self.winfo_width())
        row = column = used = 0
        gap = pixels(self, 6)
        for button in self.buttons:
            if button in self.hidden:
                button.grid_remove()
                continue
            needed = button.winfo_reqwidth()
            if used and used + needed > width:
                row += 1
                column = used = 0
            button.grid(row=row, column=column, sticky="w", padx=(0, gap), pady=(0, gap))
            used += needed + gap
            column += 1

    def _destroy(self, event):
        if event.widget is self and self._job is not None:
            self.after_cancel(self._job)
            self._job = None
