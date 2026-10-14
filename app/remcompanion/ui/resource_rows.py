"""Native, searchable resource rows with explicit edit and permission actions.

The small Listbox-compatible selection API keeps existing workflows intact.
Names/descriptions are never translated; switches express permission, not a
pretend connection. No I/O or process launch is performed by this component.
"""

from __future__ import annotations

import tkinter as tk
from .scroll_surface import ScrollCanvas
from tkinter import ttk

from . import theme
from .dpi import display_scale


def permission_images(master):
    """Local pill pictograms; native Checkbuttons retain checked semantics."""
    scale = display_scale(master)
    width, height = round(42 * scale), round(24 * scale)
    radius = height / 2
    result = []
    for enabled in (False, True):
        image = tk.PhotoImage(master=master, width=width, height=height)
        track = theme.LIGHT["MUTED"] if not enabled else theme.LIGHT["ACCENT_DEEP"]
        knob = width - radius if enabled else radius
        knob_radius = radius - max(2, round(3 * scale))
        for y in range(height):
            for x in range(width):
                center = radius if x < radius else width - radius if x > width - radius else x
                inside = (x - center) ** 2 + (y - radius) ** 2 <= radius ** 2
                thumb = (x - knob) ** 2 + (y - radius) ** 2 <= knob_radius ** 2
                if thumb or inside:
                    image.put(theme.LIGHT["ON_ACCENT"] if thumb else track, (x, y))
        result.append(image)
    return tuple(result)


class PermissionSwitch(tk.Checkbutton):
    """Prominent native permission control, not a connection status indicator."""
    def __init__(self, parent, *, text, variable, command=None):
        colors = theme.palette()
        super().__init__(parent, text=text, variable=variable, command=command,
            indicatoron=False, compound="left", bg=colors["WHITE"], fg=colors["INK"],
            activebackground=colors["ACCENT_SOFT"], activeforeground=colors["INK"],
            selectcolor=colors["WHITE"], font=(theme.BODY_FONT, 10, "bold"),
            padx=10, pady=8, bd=0, relief="flat", offrelief="flat", overrelief="flat",
            highlightthickness=1, highlightbackground=colors["LINE"], highlightcolor=colors["ACCENT_DEEP"],
            cursor="hand2", takefocus=True, anchor="w")
        self._images = permission_images(self)
        self.configure(image=self._images[0], selectimage=self._images[1])
        self.bind("<Configure>", lambda event: self.configure(
            wraplength=max(80, event.width - self._images[0].width() - 28)))
        self.bind('<Destroy>',lambda event:self._release_images(event),add=True)

    def _release_images(self,event):
        if event.widget is self:self._images=()


class ResourceRows(tk.Frame):
    def __init__(self, parent, *, query, edit, toggle):
        colors = theme.palette()
        super().__init__(parent, bg=colors["WHITE"])
        self._search_query = query
        self._edit = edit
        self._toggle = toggle
        self._rows = []
        self._selected = None
        self._signature = None
        self._widgets = []
        self._busy = False
        self._switch_images = self._permission_images()
        self.canvas = ScrollCanvas(self, bg=colors["WHITE"], bd=0, highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll = ttk.Scrollbar(self, command=self.canvas.yview)
        self.scroll.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.body = tk.Frame(self.canvas, bg=colors["WHITE"])
        self._item = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.bind("<Configure>", self._layout)
        self.body.bind("<Configure>", self._layout)
        self.bind("<Destroy>", self._cleanup, add=True)
        self._top = self.winfo_toplevel()
        self._wheel_bindings = [(key, self._top.bind(key, self._wheel, add=True))
                                for key in ("<MouseWheel>", "<Button-4>", "<Button-5>")]

    def _permission_images(self):
        """A native Checkbutton retains keyboard/checked semantics; its small
        pill pictogram is drawn locally, never loaded from the network."""
        return permission_images(self)

    def _layout(self, _event=None):
        width = max(1, self.canvas.winfo_width())
        self.canvas.itemconfigure(self._item, width=width)
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _wheel(self, event):
        widget = event.widget
        while widget is not None:
            if widget is self:
                self.canvas.yview_scroll(-1 if getattr(event, "num", 0) == 4 or
                                        getattr(event, "delta", 0) > 0 else 1, "units")
                return "break"
            widget = getattr(widget, "master", None)
        return None

    def _cleanup(self, event):
        if event.widget is self:
            for key, identifier in self._wheel_bindings:
                try:
                    script=str(self._top.tk.call('bind',str(self._top),key))
                    self._top.tk.call('bind',str(self._top),key,'\n'.join(line for line in script.splitlines() if identifier not in line))
                    self._top.deletecommand(identifier)
                except tk.TclError:
                    pass
            self._switch_images=()

    def set_rows(self, rows, *, state):
        signature = repr([(row, state(row)) for row in rows])
        selected_id = self._rows[self._selected]["id"] if self._selected is not None and self._selected < len(self._rows) else None
        self._rows = list(rows)
        self._selected = next((i for i, row in enumerate(rows) if row["id"] == selected_id), None)
        if self._signature == signature:
            return
        self._signature = signature
        y = self.canvas.yview()[0]
        for child in self.body.winfo_children():
            child.destroy()
        self._widgets = []
        colors = theme.palette()
        if not rows:
            label = tk.Label(self.body, text="暂无匹配项。新增服务或导入技能后会显示在这里。",
                             bg=colors["WHITE"], fg=colors["MUTED"], font=(theme.BODY_FONT, 10),
                             padx=16, pady=28, anchor="w", justify="left")
            label.pack(fill="x")
            label.bind("<Configure>", lambda e: label.configure(wraplength=max(100, e.width - 32)))
        for index, row in enumerate(rows):
            host = tk.Frame(self.body, bg=colors["WHITE"], padx=16, pady=12)
            host.pack(fill="x")
            host.columnconfigure(0, weight=1)
            name = tk.Label(host, text=row.get("name", row["id"]), bg=colors["WHITE"], fg=colors["INK"],
                            font=(theme.BODY_FONT, 11, "bold"), anchor="w", justify="left")
            name._remchat_locale_exclude = True
            name.grid(row=0, column=0, sticky="ew", padx=(0, 12))
            description = tk.Label(host, text=state(row), bg=colors["WHITE"], fg=colors["MUTED"],
                                   font=(theme.BODY_FONT, 9), anchor="w", justify="left")
            description.grid(row=1, column=0, sticky="ew", padx=(0, 12), pady=(5, 0))
            name.bind("<Configure>", lambda e, target=name: target.configure(wraplength=max(70, e.width)))
            description.bind("<Configure>", lambda e, target=description: target.configure(wraplength=max(70, e.width)))
            edit = tk.Button(host, text="编辑", command=lambda i=index: self._edit_row(i), font=(theme.BODY_FONT, 10),
                             bg=colors["WHITE"], fg=colors["INK"], activebackground=colors["ACCENT_SOFT"],
                             activeforeground=colors["INK"], relief="flat", bd=0, padx=12, pady=7,
                             highlightthickness=1, highlightbackground=colors["LINE"], cursor="hand2")
            edit.grid(row=0, column=1, rowspan=2, padx=(4, 12))
            variable = tk.BooleanVar(self, bool(row.get("enabled")))
            switch = tk.Checkbutton(host, text="允许", variable=variable, indicatoron=False,
                image=self._switch_images[0], selectimage=self._switch_images[1], compound="left",
                command=lambda i=index, value=variable: self._toggle_row(i, value.get()),
                bg=colors["WHITE"], fg=colors["INK"], selectcolor=colors["ACCENT_SOFT"],
                activebackground=colors["ACCENT_SOFT"], activeforeground=colors["INK"],
                font=(theme.BODY_FONT, 10), padx=12, pady=7, bd=0, relief="flat",
                offrelief="flat", overrelief="flat", highlightthickness=1,
                highlightbackground=colors["LINE"], cursor="hand2", takefocus=True)
            switch.grid(row=0, column=2, rowspan=2)
            for target in (host, name, description):
                target.bind("<Button-1>", lambda _e, i=index: self._choose(i))
                target.bind("<Double-Button-1>", lambda _e, i=index: self._edit_row(i))
            tk.Frame(self.body, bg=colors["LINE"], height=1).pack(fill="x", padx=16)
            self._widgets.append((host, edit, switch, variable))
        self.set_busy(self._busy)
        self.canvas.after_idle(lambda: self.canvas.yview_moveto(y) if self.canvas.winfo_exists() else None)

    def _choose(self, index):
        if self._busy:
            return
        self._selected = index
        self.event_generate("<<ListboxSelect>>")

    def _edit_row(self, index):
        if not self._busy:
            self._selected = index
            self._edit()

    def _toggle_row(self, index, desired):
        if not self._busy:
            self._selected = index
            # The host owns validation/async storage. Restore until committed.
            self._widgets[index][3].set(bool(self._rows[index].get("enabled")))
            self._toggle(self._rows[index], desired)

    def set_busy(self, busy):
        self._busy = busy
        for _host, edit, switch, _variable in self._widgets:
            edit.configure(state="disabled" if busy else "normal")
            switch.configure(state="disabled" if busy else "normal")

    def curselection(self):
        return (self._selected,) if self._selected is not None else ()

    def selection_clear(self, _first, _last=None):
        self._selected = None

    def selection_set(self, index):
        self._selected = int(index) if 0 <= int(index) < len(self._rows) else None

    def see(self, index):
        if self._rows:
            self.canvas.yview_moveto(int(index) / len(self._rows))
