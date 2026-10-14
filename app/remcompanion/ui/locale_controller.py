"""Update reviewed interface strings in place; leave user text and IDs alone.

No widget rebuild, monkey-patching or periodic polling is needed. Map/configure
events batch refreshes; status StringVars have individual, removed-on-close
traces. Data-bearing controls and excluded subtrees are never rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass
from weakref import WeakKeyDictionary, WeakSet
import tkinter as tk
from tkinter import ttk

from ..i18n import Translator


@dataclass
class _TextState:
    source: str
    expected: str


class LocaleController:
    def __init__(self, root: tk.Misc, translator: Translator) -> None:
        self.root = root
        self.translator = translator
        self._states: WeakKeyDictionary = WeakKeyDictionary()
        self._seen: WeakSet = WeakSet()
        self._dirty: WeakKeyDictionary = WeakKeyDictionary()
        self._full_refresh = False
        self._traces: dict[str, str] = {}
        self._trace_widgets: dict[str, WeakSet] = {}
        self._variable_states: dict[str, _TextState] = {}
        self._pending: str | None = None
        self._applying = False
        self._closed = False
        self._bindings = {event: root.bind(event, self.schedule, add="+")
                          for event in ("<Map>", "<Configure>")}
        root._remchat_translator = translator
        root._remchat_locale_controller = self

    def schedule(self, _event=None) -> None:
        if self._closed or self._applying:
            return
        if _event is None:
            self._full_refresh = True
        else:
            widget = _event.widget
            if not isinstance(widget, tk.Misc) or self._excluded(widget):
                return
            if _event.type == tk.EventType.Map:
                if widget in self._seen:
                    state = self._states.get(widget, {}).get("text")
                    if isinstance(widget, (ttk.Notebook, ttk.Treeview, tk.Menu)):
                        self._dirty.setdefault(widget, False)
                    elif state is not None and str(widget.cget("text")) != state.expected:
                        self._dirty.setdefault(widget, False)
                    else:
                        return
                else:
                    self._dirty[widget] = True
            else:
                # Geometry alone does not change language. A text-bearing
                # control may have been updated by a status callback, however.
                states = self._states.get(widget, {})
                state = states.get("text")
                if not isinstance(widget, (ttk.Notebook, ttk.Treeview)) and (
                    state is None or str(widget.cget("text")) == state.expected
                ):
                    return
                self._dirty.setdefault(widget, False)
        if self._pending is None:
            self._pending = self.root.after_idle(self._refresh)

    def _excluded(self, widget: tk.Misc) -> bool:
        while widget is not None:
            if getattr(widget, "_remchat_locale_exclude", False):
                return True
            widget = widget.master
        return False

    def _schedule_variable(self, widget: tk.Misc) -> None:
        self.schedule_widget(widget)

    def schedule_widget(self, widget: tk.Misc) -> None:
        """Retouch one producer-updated UI control, without a tree scan."""
        if self._closed or self._applying or self._excluded(widget):
            return
        self._dirty.setdefault(widget, False)
        if self._pending is None:
            self._pending = self.root.after_idle(self._refresh)

    def _watch_variable(self, variable: str, widget: tk.Misc) -> None:
        targets = self._trace_widgets.setdefault(variable, WeakSet())
        if widget in targets:
            return
        targets.add(widget)
        if variable not in self._traces:
            def changed(*_args) -> None:
                for target in tuple(self._trace_widgets.get(variable, ())):
                    self._schedule_variable(target)
            callback = self.root.register(changed)
            widget.tk.call("trace", "add", "variable", variable, "write", callback)
            self._traces[variable] = callback
        def destroyed(event) -> None:
            if event.widget is widget:
                targets.discard(widget)
                if not targets:
                    self._unwatch_variable(variable)
        widget.bind("<Destroy>", destroyed, add=True)

    def _unwatch_variable(self, variable: str) -> None:
        callback = self._traces.pop(variable, None)
        self._trace_widgets.pop(variable, None)
        self._variable_states.pop(variable, None)
        if callback:
            try:
                self.root.tk.call("trace", "remove", "variable", variable, "write", callback)
                self.root.deletecommand(callback)
            except tk.TclError:
                pass

    def _refresh(self) -> None:
        self._pending = None
        dirty = dict(self._dirty)
        self._dirty.clear()
        if self._full_refresh:
            self._full_refresh = False
            self.apply()
            return
        for widget, subtree in dirty.items():
            ancestor = widget.master
            while ancestor is not None and not dirty.get(ancestor, False):
                ancestor = ancestor.master
            if ancestor is None:
                self._apply_scope(widget, subtree=subtree)

    def _translated(self, widget: tk.Misc, key: object, current: str) -> str:
        states = self._states.setdefault(widget, {})
        state = states.get(key)
        if state is None or state.expected != current:
            state = _TextState(current, current)
            states[key] = state
        translated = self.translator.display(state.source)
        state.expected = translated
        return translated

    def _walk(self, widget: tk.Misc):
        if getattr(widget, "_remchat_locale_exclude", False):
            return
        yield widget
        for child in widget.winfo_children():
            yield from self._walk(child)

    def apply(self) -> None:
        self._apply_scope(self.root, subtree=True)

    def _apply_scope(self, target: tk.Misc, *, subtree: bool) -> None:
        if self._closed or self._applying:
            return
        self._applying = True
        try:
            for widget in self._walk(target) if subtree else (target,):
                self._seen.add(widget)
                self._apply_widget(widget)
        except tk.TclError:
            # Destruction can race a queued Map/Configure callback on exit.
            pass
        finally:
            self._applying = False

    def _apply_widget(self, widget: tk.Misc) -> None:
        if isinstance(widget, ttk.Combobox) and hasattr(widget, "_remchat_ui_choice_labels"):
            # Opt-in UI choices only; never rewrite models, IDs or user values.
            index = widget.current()
            labels = tuple(self.translator.display(source) for source in widget._remchat_ui_choice_labels)
            if tuple(widget.cget("values")) != labels:
                widget.configure(values=labels)
                if index >= 0:
                    widget.current(index)
        elif isinstance(widget, tk.Menu):
            end = widget.index("end")
            for index in range((end + 1) if end is not None else 0):
                if widget.type(index) in {"separator", "tearoff"}:
                    continue
                current = str(widget.entrycget(index, "label"))
                translated = self._translated(widget, index, current)
                if translated != current:
                    widget.entryconfigure(index, label=translated)
        elif isinstance(widget, ttk.Notebook):
            for tab in widget.tabs():
                current = str(widget.tab(tab, "text"))
                translated = self._translated(widget, tab, current)
                if translated != current:
                    widget.tab(tab, text=translated)
        elif isinstance(widget, ttk.Treeview):
            # Column headings are UI; rows are user data and never touched.
            for column in ("#0", *widget.cget("columns")):
                current = str(widget.heading(column, "text"))
                translated = self._translated(widget, column, current)
                if translated != current:
                    widget.heading(column, text=translated)
        elif isinstance(widget, (tk.Label, tk.Button, tk.Checkbutton, tk.Radiobutton,
                                 tk.LabelFrame, ttk.Label, ttk.Button, ttk.Checkbutton,
                                 ttk.Radiobutton, ttk.LabelFrame)):
            current = str(widget.cget("text"))
            variable = str(widget.cget("textvariable")) if "textvariable" in widget.keys() else ""
            if variable:
                self._watch_variable(variable, widget)
                # A shared StringVar has one source, regardless of which
                # label is visited first or later destroyed. Never treat a
                # sibling's already-translated value as a new English source.
                state = self._variable_states.get(variable)
                if state is None or state.expected != current:
                    state = _TextState(current, current)
                    self._variable_states[variable] = state
                self._states.setdefault(widget, {})["text"] = state
                translated = self.translator.display(state.source)
                state.expected = translated
                if translated != current:
                    widget.setvar(variable, translated)
            else:
                translated = self._translated(widget, "text", current)
                if translated != current:
                    widget.configure(text=translated)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if getattr(self.root, "_remchat_locale_controller", None) is self:
            del self.root._remchat_locale_controller
        if self._pending is not None:
            try:
                self.root.after_cancel(self._pending)
            except tk.TclError:
                pass
            self._pending = None
        for event, binding in self._bindings.items():
            if binding:
                try:
                    self.root.unbind(event, binding)
                except tk.TclError:
                    # A transient dialog can be destroyed before its cleanup.
                    pass
        for variable in tuple(self._traces):
            self._unwatch_variable(variable)


def translator_for(widget: tk.Misc | None) -> Translator:
    while widget is not None:
        translator = getattr(widget, "_remchat_translator", None)
        if translator is not None:
            return translator
        widget = getattr(widget, "master", None)
    return Translator()


def refresh_ui_labels(widget: tk.Misc) -> None:
    """Schedule the nearest controller for a known UI label producer only."""
    ancestor = widget
    while ancestor is not None:
        controller = getattr(ancestor, "_remchat_locale_controller", None)
        if controller is not None:
            controller.schedule_widget(widget)
            return
        ancestor = getattr(ancestor, "master", None)
