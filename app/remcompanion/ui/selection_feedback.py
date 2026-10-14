"""Visible native-table selection and short, non-modal UI feedback."""
from __future__ import annotations

from collections.abc import Callable, Iterable
import tkinter as tk
from tkinter import ttk

from . import theme
from .dpi import pixels
from .theme_controller import set_color_roles


class CheckedTreeSelection:
    """Checkbox affordance backed exclusively by ``Treeview.selection``.

    Consumers provide current leaf IDs and a first data column named ``choose``.
    Group checkboxes act on their currently displayed descendant leaves only.
    Ordinary native row selection, including Ctrl/Shift, remains available.
    """

    def __init__(self, tree: ttk.Treeview, leaf_ids: Callable[[], Iterable[str]],
                 group_ids: Callable[[], Iterable[str]] | None = None,
                 enabled: Callable[[], bool] | None = None):
        self.tree, self.leaf_ids, self.group_ids = tree, leaf_ids, group_ids
        self.enabled = enabled or (lambda: True)
        self._bindings = [(event, tree.bind(event, callback, add=True)) for event, callback in (
            ("<Button-1>", self._click), ("<space>", self._space),
            ("<Control-a>", self._all_key), ("<Control-A>", self._all_key),
            ("<<TreeviewSelect>>", lambda _event: self.sync()),
        )]
        tree.heading("choose", command=self._toggle_all)
        self.sync()

    def _leaves(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.leaf_ids() if self.tree.exists(str(item)))

    def selected_ids(self) -> tuple[str, ...]:
        selected = set(self.tree.selection())
        return tuple(item for item in self._leaves() if item in selected)

    def _descendants(self, item: str, leaves: set[str]) -> tuple[str, ...]:
        found = []
        for child in self.tree.get_children(item):
            if child in leaves:
                found.append(child)
            found.extend(self._descendants(child, leaves))
        return tuple(found)

    def sync(self) -> None:
        if not self.tree.winfo_exists():
            return
        leaves, selected = self._leaves(), set(self.tree.selection())
        for item in leaves:
            self.tree.set(item, "choose", "☑" if item in selected else "☐")
        if self.group_ids:
            for item in self.group_ids():
                if self.tree.exists(item):
                    children = self._descendants(item, set(leaves))
                    picked = sum(child in selected for child in children)
                    self.tree.set(item, "choose", "☑" if children and picked == len(children) else "−" if picked else "☐")
        count = sum(item in selected for item in leaves)
        self.tree.heading("choose", text="☑" if leaves and count == len(leaves) else "−" if count else "☐")

    def select_all(self) -> None:
        if self.enabled():
            self.tree.selection_set(self._leaves())
            self.sync()

    def clear(self) -> None:
        if self.enabled():
            self.tree.selection_remove(self.tree.selection())
            self.sync()

    def _toggle_all(self) -> None:
        leaves = self._leaves()
        if leaves and len(self.selected_ids()) == len(leaves):
            self.clear()
        else:
            self.select_all()

    def _toggle(self, item: str) -> None:
        if not self.enabled():
            return
        leaves = set(self._leaves())
        ids = (item,) if item in leaves else self._descendants(item, leaves)
        if not ids:
            return
        selected = set(self.tree.selection())
        if all(identifier in selected for identifier in ids):
            self.tree.selection_remove(ids)
        else:
            # A parent item is navigation, not an independently actionable task.
            if item not in leaves:
                self.tree.selection_remove(item)
            self.tree.selection_add(ids)
        self.sync()

    def _click(self, event):
        column = self.tree.identify_column(event.x)
        if self.tree.identify_region(event.x, event.y) != "cell" or not column:
            return None
        if self.tree.column(column, "id") != "choose":
            return None
        item = self.tree.identify_row(event.y)
        if item and self.enabled():
            self.tree.focus(item)
            self.tree.focus_set()
            self._toggle(item)
        return "break"

    def _space(self, _event):
        item = self.tree.focus()
        if item:
            self._toggle(item)
        return "break"

    def _all_key(self, _event):
        self.select_all()
        return "break"

    def close(self) -> None:
        for event, identifier in self._bindings:
            if identifier and self.tree.winfo_exists():
                self.tree.unbind(event, identifier)
        self._bindings.clear()


class Tooltip:
    """Hover and keyboard-focus explanations, never the sole action label."""

    def __init__(self, widget: tk.Misc, text: str | Callable[[], str]):
        self.widget, self.text = widget, text
        self.window = None
        self.pending = None
        for event in ("<Enter>", "<FocusIn>"):
            widget.bind(event, self._schedule, add=True)
        for event in ("<Leave>", "<FocusOut>", "<ButtonPress>", "<Unmap>", "<Escape>"):
            widget.bind(event, lambda _event: self.close(), add=True)
        widget.bind("<Destroy>", self._destroyed, add=True)

    def _schedule(self, _event=None):
        self.close()
        self.pending = self.widget.after(450, self._show)

    def _show(self):
        self.pending = None
        if not self.widget.winfo_exists() or not self.widget.winfo_viewable():
            return
        message = self.text() if callable(self.text) else self.text
        if not message:
            return
        window = self.window = tk.Toplevel(self.widget)
        window.withdraw()
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        set_color_roles(window, background="LINE")
        label = tk.Label(window, text=message, justify="left", anchor="w",
                         font=(theme.BODY_FONT, 10), padx=pixels(window, 12), pady=pixels(window, 8),
                         wraplength=min(pixels(window, 360), window.winfo_screenwidth() - 32))
        set_color_roles(label, background="WHITE", foreground="INK")
        label.pack(padx=1, pady=1)
        window.update_idletasks()
        x = min(self.widget.winfo_rootx(), window.winfo_screenwidth() - window.winfo_reqwidth() - 8)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + pixels(window, 6)
        if y + window.winfo_reqheight() > window.winfo_screenheight() - 8:
            y = self.widget.winfo_rooty() - window.winfo_reqheight() - pixels(window, 6)
        window.geometry(f"+{max(8, x)}+{max(8, y)}")
        window.deiconify()

    def _destroyed(self, event):
        if event.widget is self.widget:
            self.close()

    def close(self):
        if self.pending is not None:
            try:
                self.widget.after_cancel(self.pending)
            except tk.TclError:
                pass
            self.pending = None
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None


class TopNotice:
    """A five-second top banner in the owner's window; no focus or modal grab."""

    def __init__(self, owner: tk.Misc):
        self.owner, self.root = owner, owner.winfo_toplevel()
        self.frame = None
        self.pending = None
        self._resize_binding = self.root.bind("<Configure>", self._resize, add=True)
        self._unmap_binding = self.root.bind("<Unmap>", self._unmapped, add=True)
        owner.bind("<Destroy>", self._destroyed, add=True)

    def show(self, message: str, error: bool = False) -> None:
        self.close()
        if not self.owner.winfo_exists() or not self.root.winfo_viewable():
            return
        self.frame = tk.Frame(self.root, borderwidth=0, highlightthickness=1)
        set_color_roles(self.frame, background="WHITE", highlightbackground="LINE")
        self.label = tk.Label(self.frame, text=message, anchor="w", justify="left",
                              font=(theme.BODY_FONT, 10), padx=pixels(self.root, 14), pady=pixels(self.root, 10))
        set_color_roles(self.label, background="WHITE", foreground="ERROR" if error else "SUCCESS")
        self.label.pack(side="left", fill="both", expand=True)
        dismiss = tk.Button(self.frame, text="×", command=self.close, relief="flat", borderwidth=0,
                            font=(theme.BODY_FONT, 13), padx=pixels(self.root, 10), takefocus=True,
                            highlightthickness=1)
        set_color_roles(dismiss, background="WHITE", foreground="MUTED", activebackground="ACCENT_SOFT",
                        activeforeground="INK", highlightbackground="WHITE", highlightcolor="ACCENT")
        dismiss.pack(side="right", padx=(0, pixels(self.root, 4)))
        Tooltip(dismiss, "关闭这条提示，不改变观察状态。")
        self._resize()
        self.frame.lift()
        self.pending = self.root.after(5000, self.close)

    def _resize(self, event=None):
        if self.frame is None or (event is not None and event.widget is not self.root):
            return
        width = max(120, min(pixels(self.root, 620), self.root.winfo_width() - pixels(self.root, 32)))
        self.label.configure(wraplength=max(80, width - pixels(self.root, 70)))
        self.frame.place(relx=0.5, y=pixels(self.root, 16), anchor="n", width=width)

    def _unmapped(self, event):
        if event.widget is self.root:
            self.close()

    def _destroyed(self, event):
        if event.widget is self.owner:
            self.close()
            for sequence, identifier in (("<Configure>", self._resize_binding), ("<Unmap>", self._unmap_binding)):
                try:
                    self.root.unbind(sequence, identifier)
                except tk.TclError:
                    pass

    def close(self) -> None:
        if self.pending is not None:
            try:
                self.root.after_cancel(self.pending)
            except tk.TclError:
                pass
            self.pending = None
        if self.frame is not None:
            try:
                self.frame.destroy()
            except tk.TclError:
                pass
            self.frame = None
