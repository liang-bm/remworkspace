"""Poll Linux local activation tokens on Tk's owner thread."""
from __future__ import annotations
import tkinter as tk
from .instance import activation_pending, signal_existing_instance

EVENT_NAME = "Local\\RemCompanionActivate"
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0


class ActivationListener:
    def __init__(self, root: tk.Misc, callback) -> None:
        self.root, self.callback = root, callback
        self.handle = root.after(160, self._poll)

    def _poll(self) -> None:
        try:
            if activation_pending():
                self.callback()
            self.handle = self.root.after(160, self._poll)
        except tk.TclError:
            pass

    def close(self) -> None:
        if self.handle is not None:
            try:
                self.root.after_cancel(self.handle)
            except tk.TclError:
                pass
        self.handle = None
