"""Small vector field borders around genuine native editors.

The backing has no large tiled image or resize-time blur. Text selection,
validation, IME, undo and ttk popdowns remain owned by the original widget.
Geometry methods route to the backing so callers keep their normal API.
"""
import tkinter as tk
from tkinter import ttk
from . import theme
from .dpi import pixels
from .rounded_controls import rounded_box


class _FieldHost(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, bd=0, highlightthickness=0, width=1, height=1,
                         bg=theme.WHITE)
        self._remchat_theme_fixed = True
        self.editor = None
        self._job = None
        self._destroying = False
        self.bind('<Configure>', self.schedule)
        self.bind('<<ThemeChanged>>', self.schedule, add=True)

    def schedule(self, _event=None):
        if self._job is None:
            self._job = self.after_idle(self.draw)

    def draw(self):
        self._job = None
        if not self.winfo_exists() or self.editor is None: return
        colors = theme.palette()
        try:
            background = self.master.cget('background')
        except tk.TclError:
            background = colors['WHITE']
        self.configure(bg=background)
        try:
            fill = self.editor.cget('background')
        except tk.TclError:
            fill = colors['APP_BG']
        if not fill: fill = colors['APP_BG']
        focused = self.focus_get() is self.editor
        width, height = self.winfo_width(), self.winfo_height()
        self.delete('all')
        if min(width, height) < 5: return
        radius = pixels(self, 10)
        if focused:
            rounded_box(self, (1,1,width-1,height-1), radius=radius,
                        fill=colors['ACCENT_SOFT'], outline='')
        edge = pixels(self, 2 if focused else 1)
        rounded_box(self, (edge,edge,width-edge,height-edge), radius=radius,
                    fill=fill, outline=colors['ACCENT'] if focused else colors['LINE'],
                    width=pixels(self, 1))

    def destroy(self):
        self._destroying = True
        if self._job is not None:
            self.after_cancel(self._job); self._job = None
        super().destroy()


class _SoftGeometry:
    def __init__(self, parent, **options):
        self._field_host = _FieldHost(parent)
        if self._native:
            options.update(bd=0, relief='flat', highlightthickness=0)
            options.setdefault('bg',theme.APP_BG)
            options.setdefault('fg',theme.INK)
            options.setdefault('insertbackground',theme.INK)
        super().__init__(self._field_host, **options)
        host = self._field_host
        host.editor = self
        self._remchat_soft_field = True
        inset = pixels(self, 6)
        # Direct native placement avoids routing this internal editor back
        # through the public host placement method below.
        tk.Place.place_configure(self, x=inset, y=inset, relwidth=1, relheight=1,
                                 width=-2*inset, height=-2*inset)
        host.configure(width=super().winfo_reqwidth()+2*inset,
                       height=super().winfo_reqheight()+2*inset)
        self.bind('<FocusIn>', host.schedule, add=True)
        self.bind('<FocusOut>', host.schedule, add=True)
        self.bind('<Map>', host.schedule, add=True)
        self.bind('<<ThemeChanged>>', host.schedule, add=True)
        host.schedule()

    def configure(self, cnf=None, **options):
        if isinstance(cnf, dict): options = {**cnf, **options}; cnf = None
        if self._native:
            for key in ('bd', 'borderwidth', 'highlightthickness', 'relief'):
                options.pop(key, None)
        result = super().configure(cnf, **options)
        if hasattr(self, '_field_host') and self._field_host.editor is not None:
            self._field_host.schedule()
        return result

    config = configure
    def pack(self, *args, **kwargs): return self._field_host.pack(*args, **kwargs)
    pack_configure = pack
    def grid(self, *args, **kwargs): return self._field_host.grid(*args, **kwargs)
    grid_configure = grid
    def place(self, *args, **kwargs): return self._field_host.place(*args, **kwargs)
    place_configure = place
    def pack_forget(self): return self._field_host.pack_forget()
    def grid_forget(self): return self._field_host.grid_forget()
    def grid_remove(self): return self._field_host.grid_remove()
    def place_forget(self): return self._field_host.place_forget()
    def pack_info(self): return self._field_host.pack_info()
    def grid_info(self): return self._field_host.grid_info()
    def place_info(self): return self._field_host.place_info()
    def winfo_reqwidth(self): return self._field_host.winfo_reqwidth()
    def winfo_reqheight(self): return self._field_host.winfo_reqheight()
    def destroy(self):
        if self._field_host._destroying: return super().destroy()
        self._field_host.destroy()


class SoftEntry(_SoftGeometry, tk.Entry):
    _native = True


class SoftTtkEntry(_SoftGeometry, ttk.Entry):
    _native = False


class SoftCombobox(_SoftGeometry, ttk.Combobox):
    _native = False


class SoftSpinbox(_SoftGeometry, tk.Spinbox):
    _native = True
