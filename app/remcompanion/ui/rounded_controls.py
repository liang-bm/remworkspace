"""Shared soft native chrome, without replacing editors or their bindings.

Ttk retains its native field drawing. On Windows Tk/ttk child windows
receive a rounded clipping region; their IME, validation, focus, selections and
commands remain native. Region work is coalesced and only runs on size changes.
"""
from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from tkinter import ttk
from weakref import WeakKeyDictionary

from . import theme
from .dpi import pixels


def style_checkbox(widget, mode=None):
    """Draw only the small indicator; native check/variable/space semantics stay.

    Existing permission switches with their own pictograms are already soft
    controls and are not reinterpreted as ordinary checkboxes.
    """
    if not isinstance(widget, tk.Checkbutton): return
    if not widget.cget('indicatoron') and not getattr(widget, '_remchat_round_check', False): return
    from PIL import Image, ImageDraw, ImageTk
    colors = theme.palette(mode)
    size = pixels(widget, 19)
    key = (size, mode or theme.get_mode(), colors['ACCENT_DEEP'], colors['ON_ACCENT'])
    if getattr(widget, '_remchat_check_key', None) == key:
        widget.configure(selectcolor=widget.cget('background'),activebackground=widget.cget('background'))
        return
    if not getattr(widget,'_remchat_round_check',False):
        widget.bind('<Destroy>',_release_checkbox_images,add=True)
    photos = []
    for checked in (False, True):
        im = Image.new('RGBA', (size*3, size*3))
        draw = ImageDraw.Draw(im)
        draw.rounded_rectangle((3,3,size*3-4,size*3-4), radius=size*.72,
                               fill=colors['ACCENT_DEEP'] if checked else None,
                               outline=colors['ACCENT_DEEP'] if checked else colors['SUBTLE'], width=3)
        if checked:
            draw.line([(size*.65,size*1.5),(size*1.25,size*2.05),(size*2.35,size*.85)],
                      fill=colors['ON_ACCENT'], width=max(3,round(size*.28)), joint='curve')
        photos.append(ImageTk.PhotoImage(im.resize((size,size), Image.Resampling.LANCZOS), master=widget))
    widget._remchat_check_photos = photos
    widget._remchat_check_key = key
    widget._remchat_round_check = True
    widget.configure(indicatoron=False, image=photos[0], selectimage=photos[1], compound='left',
                     relief='flat', offrelief='flat', bd=0, padx=pixels(widget,6), pady=pixels(widget,4),
                     highlightthickness=0, selectcolor=widget.cget('background'),
                     activebackground=widget.cget('background'))


def _release_checkbox_images(event):
    # Release Tcl image objects on the UI thread; a background parser's
    # cyclic GC must never become the thread running PhotoImage.__del__.
    if isinstance(event.widget,tk.Checkbutton):
        event.widget._remchat_check_photos=[]
        event.widget._remchat_check_key=None


def rounded_box(canvas, bounds, *, radius=6, **options):
    """Small vector shape used by calendar cells, never a bitmap per cell."""
    x0, y0, x1, y1 = bounds
    r = min(radius, (x1-x0)/2, (y1-y0)/2)
    return canvas.create_polygon(
        x0+r,y0, x1-r,y0, x1,y0, x1,y0+r, x1,y1-r,
        x1,y1, x1-r,y1, x0+r,y1, x0,y1, x0,y1-r,
        x0,y0+r, x0,y0, smooth=True, splinesteps=12, **options,
    )


def configure_ttk_rounding(root, colors):
    """Keep native drawing: stretched RGBA border tiles cause cold-map stalls.

    The Windows child-window masks below round actual field surfaces without
    repainting a large input as hundreds of tiny image tiles. Notebook labels
    retain the native focus/layout elements and explicit shared font.
    """
    style = ttk.Style(root)
    style.layout('TEntry',[('Entry.field',{'sticky':'nsew','children':[
        ('Entry.padding',{'sticky':'nsew','children':[('Entry.textarea',{'sticky':'nsew'})]})]})])
    style.layout('TCombobox',[('Combobox.field',{'sticky':'nsew','children':[
        ('Combobox.downarrow',{'side':'right','sticky':'ns'}),
        ('Combobox.padding',{'sticky':'nsew','children':[('Combobox.textarea',{'sticky':'nsew'})]})]})])
    style.layout('TSpinbox',[('Spinbox.field',{'sticky':'nsew','children':[
        ('Spinbox.uparrow',{'side':'right','sticky':'ne'}),
        ('Spinbox.downarrow',{'side':'right','sticky':'se'}),
        ('Spinbox.padding',{'sticky':'nsew','children':[('Spinbox.textarea',{'sticky':'nsew'})]})]})])
    style.layout('TNotebook.Tab',[('Notebook.tab',{'sticky':'nsew','children':[
        ('Notebook.padding',{'side':'top','sticky':'nsew','children':[
            ('Notebook.focus',{'side':'top','sticky':'nsew','children':[
                ('Notebook.label',{'side':'top','sticky':''})]})]})]})])
    for name in ('TEntry','TCombobox','TSpinbox'):
        style.configure(name,padding=(pixels(root,6),pixels(root,3)))
        style.configure(name, borderwidth=0)
    # ttk checkbuttons use the same rounded indicator grammar without losing
    # their standard focus, selected, disabled or variable state machinery.
    if not hasattr(root, '_remchat_ttk_checks'): root._remchat_ttk_checks = {}
    key = (pixels(root,19), theme.get_mode(), colors['WHITE'], colors['ACCENT_DEEP'], colors['ON_ACCENT'])
    if key not in root._remchat_ttk_checks:
        from PIL import Image, ImageDraw, ImageTk
        size = key[0]; photos = []
        for checked in (False, True):
            im = Image.new('RGBA',(size*3,size*3)); draw=ImageDraw.Draw(im)
            draw.rounded_rectangle((3,3,size*3-4,size*3-4),radius=size*.72,
                                   fill=colors['ACCENT_DEEP'] if checked else None,
                                   outline=colors['ACCENT_DEEP'] if checked else colors['SUBTLE'],width=3)
            if checked: draw.line([(size*.65,size*1.5),(size*1.25,size*2.05),(size*2.35,size*.85)],fill=colors['ON_ACCENT'],width=max(3,round(size*.28)),joint='curve')
            photos.append(ImageTk.PhotoImage(im.resize((size,size),Image.Resampling.LANCZOS),master=root))
        name='RemCheck'+str(len(root._remchat_ttk_checks))
        style.element_create(name,'image',photos[0],('selected',photos[1]),sticky='')
        root._remchat_ttk_checks[key]=(name,photos)
    name, _photos = root._remchat_ttk_checks[key]
    style.layout('TCheckbutton',[('Checkbutton.padding',{'sticky':'nswe','children':[
        (name,{'side':'left','sticky':''}),('Checkbutton.focus',{'side':'left','sticky':'w','children':[
            ('Checkbutton.label',{'sticky':'nswe'})]})]})])
    style.configure('TCheckbutton',padding=(pixels(root,5),pixels(root,4)),font=(theme.BODY_FONT,10))


class RoundedChrome:
    """Windows clip masks scoped to one main chat subtree and its dialogs."""
    def __init__(self, root):
        self.root=root
        self._pending=set()
        self._sizes=WeakKeyDictionary()
        self._job=None
        self._linux_masks=None
        self.enabled=sys.platform=='win32' or sys.platform.startswith('linux')
        self.applied=0
        if not self.enabled:
            return
        if sys.platform.startswith('linux'):
            try:
                if root.tk.call('tk','windowingsystem')!='x11':self.enabled=False;return
                from ..platforms import native_module
                self._linux_masks=native_module('rounded_chrome').WindowMasks(root)
            except (OSError,tk.TclError):self.enabled=False;return
        else:
            from ctypes import wintypes
            self._gdi=ctypes.windll.gdi32
            self._user=ctypes.windll.user32
            self._gdi.CreateRoundRectRgn.argtypes=[ctypes.c_int]*6
            self._gdi.CreateRoundRectRgn.restype=wintypes.HRGN
            self._gdi.DeleteObject.argtypes=[wintypes.HGDIOBJ]
            self._user.SetWindowRgn.argtypes=[wintypes.HWND,wintypes.HRGN,wintypes.BOOL]
            self._user.SetWindowRgn.restype=ctypes.c_int
        self._bindings=[(sequence,root.bind_all(sequence,self._event,add=True))
                        for sequence in ('<Map>','<Configure>')]
        root.bind('<Destroy>',self._destroy,add=True)
        self._scan(root)

    def _owns(self,widget):
        while widget is not None:
            if getattr(widget,'_remchat_theme_exclude',False): return False
            if widget is self.root: return True
            if isinstance(widget,tk.Toplevel):
                try:
                    if widget.attributes('-transparentcolor'): return False
                except tk.TclError: pass
            widget=getattr(widget,'master',None)
        return False

    def _eligible(self,widget):
        if isinstance(widget,(tk.Checkbutton,tk.Button,tk.Entry,tk.Text,tk.Spinbox,tk.Listbox,
                              ttk.Entry,ttk.Combobox,ttk.Spinbox,ttk.Treeview,ttk.Notebook)):
            return True
        if isinstance(widget,tk.Frame):
            return widget.winfo_pixels(widget.cget('bd'))>0 or widget.winfo_pixels(widget.cget('highlightthickness'))>0
        return False

    def _scan(self,widget):
        if self._owns(widget) and self._eligible(widget): self._pending.add(widget)
        for child in widget.winfo_children(): self._scan(child)
        self._schedule()

    def _event(self,event):
        widget=event.widget
        if isinstance(widget,tk.Misc) and self._owns(widget) and self._eligible(widget):
            self._pending.add(widget)
            self._schedule()

    def _schedule(self):
        if self._pending and self._job is None:
            self._job=self.root.after(16,self._flush)

    def _flush(self):
        self._job=None
        pending,self._pending=self._pending,set()
        for widget in pending:
            try:
                if not widget.winfo_exists() or not widget.winfo_ismapped(): continue
                if isinstance(widget,tk.Checkbutton):
                    style_checkbox(widget)
                    continue
                width,height=widget.winfo_width(),widget.winfo_height()
                radius=min(pixels(widget,8 if isinstance(widget,(tk.Button,ttk.Treeview)) else 6),height//2)
                key=(width,height,radius)
                if min(width,height)<3 or self._sizes.get(widget)==key: continue
                if self._linux_masks is not None:
                    if self._linux_masks.apply(widget,width,height,radius):self._sizes[widget]=key;self.applied+=1
                    continue
                region=self._gdi.CreateRoundRectRgn(0,0,width,height,radius*2,radius*2)
                if not region: continue
                # SetWindowRgn takes ownership only on success (MS Win32 docs).
                if self._user.SetWindowRgn(widget.winfo_id(),region,True):
                    self._sizes[widget]=key
                    self.applied+=1
                else:
                    self._gdi.DeleteObject(region)
            except (tk.TclError,OSError): continue

    def _destroy(self,event):
        if event.widget is not self.root: return
        if self._job is not None:
            self.root.after_cancel(self._job)
        for sequence,identifier in self._bindings:
            # Python 3.12 lacks Misc._unbind; remove only our registered line,
            # not other modules' global shortcuts / wheel bindings.
            script=str(self.root.tk.call('bind','all',sequence))
            self.root.tk.call('bind','all',sequence,'\n'.join(
                line for line in script.splitlines() if identifier not in line))
            try: self.root.deletecommand(identifier)
            except tk.TclError: pass
            # Newer Tkinter bind_all registers callbacks on the interpreter's
            # root rather than this frame/Toplevel. Remove that bookkeeping
            # too, otherwise root.destroy() deletes an already-deleted command.
            commands = getattr(self.root._root(), '_tclCommands', None)
            if commands is not None and identifier in commands:
                commands.remove(identifier)
        self._pending.clear()
        if self._linux_masks is not None:self._linux_masks.close()
