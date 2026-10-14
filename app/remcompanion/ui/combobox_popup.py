"""Native ttk dropdowns that cannot detach from a scrolling page anchor."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .theme import BODY_FONT
from .soft_fields import SoftCombobox


class AnchoredCombobox(SoftCombobox):
    """Keep native list scrolling/keys, dismiss when its field moves away.

    Tk's popdown is an independent toplevel positioned only at posting time.
    An ancestor Canvas can scroll without sending Configure to this widget,
    so a short-lived watcher is needed only while the native list is open.
    """

    def __init__(self, parent, **options):
        self._popup_job = None
        self._popup_anchor = None
        self._prepare_popup = options.pop("postcommand", None)
        options.setdefault("height", 8)
        options.setdefault("font", (BODY_FONT, 10))
        super().__init__(parent, postcommand=self._before_post, **options)
        self.bind("<Configure>", self._anchor_changed, add=True)
        self.bind("<Unmap>", lambda event: self.dismiss_popup(), add=True)
        self.bind("<<ThemeChanged>>", lambda event: self.dismiss_popup(), add=True)
        self.bind("<Destroy>", self._destroyed, add=True)

    def _anchor(self):
        return (self.winfo_rootx(), self.winfo_rooty(), self.winfo_width(), self.winfo_height())

    def popup_is_open(self):
        path = str(self) + ".popdown"
        try:
            return bool(self.tk.getboolean(self.tk.call("winfo", "exists", path)) and
                        self.tk.getboolean(self.tk.call("winfo", "ismapped", path)))
        except tk.TclError:
            return False

    def _cancel_watch(self):
        if self._popup_job is not None:
            self.after_cancel(self._popup_job)
            self._popup_job = None

    def _before_post(self):
        if self._prepare_popup:
            self._prepare_popup()
        self._cancel_watch()
        self._popup_anchor = self._anchor()
        # Post runs update idletasks before deiconifying its popdown. An idle
        # callback would run too early and incorrectly think it was closed.
        self._popup_job = self.after(50, self._watch_popup)

    def _watch_popup(self):
        self._popup_job = None
        if not self.popup_is_open():
            return
        if not self.winfo_viewable() or self._anchor() != self._popup_anchor:
            self.dismiss_popup()
            return
        self._popup_job = self.after(50, self._watch_popup)

    def _anchor_changed(self, _event=None):
        if self.popup_is_open() and self._anchor() != self._popup_anchor:
            self.dismiss_popup()

    def dismiss_popup(self):
        self._cancel_watch()
        if self.popup_is_open():
            # Unpost also releases the native list's grab, including when a
            # parent page disappears while focus is inside that list.
            self.tk.call("ttk::combobox::Unpost", str(self))
        self._popup_anchor = None

    def set_values(self, values):
        values = tuple(str(value) for value in values)
        if tuple(self.cget("values")) != values:
            self.dismiss_popup()
            self.configure(values=values)

    def _destroyed(self, event):
        if event.widget is self:
            self._cancel_watch()
