"""Application-local input activity, without recording keys or OS hooks."""

from __future__ import annotations

from collections.abc import Callable
import tkinter as tk
from weakref import WeakSet


class AppActivityTracker:
    """Observe input before widget shortcuts, without consuming their events.

    A private bindtag covers this Tk application's existing and newly mapped
    widgets. Only a timestamp callback and serial are retained, never content.
    The Map listener is interpreter-local, not a global desktop input hook.
    """

    INPUT_EVENTS = ("<KeyPress>", "<ButtonPress>", "<MouseWheel>")

    def __init__(self, root: tk.Misc, on_activity: Callable[[], None]) -> None:
        self.root = root
        self.on_activity = on_activity
        self.serial = 0
        self._closed = False
        self._tag = f"RemChatActivity_{id(self)}"
        self._widgets: WeakSet = WeakSet()
        self._bindings = {
            event: root.bind_class(self._tag, event, self._record)
            for event in self.INPUT_EVENTS
        }
        self._map_binding = root.bind_all("<Map>", self._on_map, add="+")
        self._attach_tree(root)

    def _record(self, _event: tk.Event) -> None:
        if not self._closed:
            self.serial += 1
            self.on_activity()
        # Never return "break": text entry, copy/paste, dragging and wheel
        # handlers retain their original order and semantics.

    def _attach_tree(self, widget: tk.Misc) -> None:
        if self._closed:
            return
        try:
            tags = widget.bindtags()
            if self._tag not in tags:
                widget.bindtags((self._tag, *tags))
            self._widgets.add(widget)
            for child in widget.winfo_children():
                self._attach_tree(child)
        except tk.TclError:
            # A transient menu/dialog may already have been destroyed.
            pass

    def _on_map(self, event: tk.Event) -> None:
        if isinstance(event.widget, tk.Misc):
            self._attach_tree(event.widget)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for widget in list(self._widgets):
            try:
                widget.bindtags(tuple(tag for tag in widget.bindtags() if tag != self._tag))
            except tk.TclError:
                pass
        self._widgets.clear()
        try:
            # Early Python/Tkinter unbind(..., funcid) still drops the complete
            # binding script. Filter only our registered Map command instead.
            script = str(self.root.tk.call("bind", "all", "<Map>"))
            own_prefix = f'if {{"[{self._map_binding} '
            remaining = "\n".join(line for line in script.splitlines()
                                  if not line.startswith(own_prefix))
            self.root.tk.call("bind", "all", "<Map>", remaining)
            self.root.deletecommand(self._map_binding)
            for event, handle in self._bindings.items():
                self.root.unbind_class(self._tag, event)
                self.root.deletecommand(handle)
        except tk.TclError:
            pass
        self.on_activity = lambda: None
