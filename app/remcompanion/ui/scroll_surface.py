"""One viewport commit per event-loop pass, not one native paint per input.

Tk already buffers Canvas items, but its embedded HWNDs are separate surfaces.
After moving them, invalidate the viewport's native child subtree once so old
client pixels cannot survive as ghost controls. Never pump a nested event loop,
retain a DC, or enable WS_EX_COMPOSITED on the whole widget tree.
"""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk
from weakref import WeakKeyDictionary, WeakSet

FRAME_MS=round(1000/60)


class ScrollCanvas(tk.Canvas):
    def __init__(self, parent, *, on_view=None, **options):
        self._view_job=None
        self._view_key=None
        self._on_view=on_view
        self._scroll_command=options.pop('yscrollcommand',None)
        super().__init__(parent,**options)
        super().configure(yscrollcommand=self._report_view)
        self.bind('<Map>',self.schedule_view,add=True)
        self.bind('<Configure>',self.schedule_view,add=True)
        self.bind('<Destroy>',self._destroy_surface,add=True)

    def configure(self, cnf=None, **options):
        if isinstance(cnf,dict):options={**cnf,**options};cnf=None
        if 'yscrollcommand' in options:
            self._scroll_command=options.pop('yscrollcommand')
            options['yscrollcommand']=self._report_view
        return super().configure(cnf,**options)

    config=configure

    def set_view_callback(self, callback):
        self._on_view=callback
        self.schedule_view()

    def yview(self,*args):
        result=super().yview(*args)
        if args:self.schedule_view()
        return result

    def yview_moveto(self,fraction):return self.yview('moveto',fraction)
    def yview_scroll(self,number,what):return self.yview('scroll',number,what)

    def _report_view(self,first,last):
        if callable(self._scroll_command):self._scroll_command(first,last)
        elif self._scroll_command:
            self.tk.call(*self.tk.splitlist(self._scroll_command),first,last)
        key=(first,last,self.winfo_width(),self.winfo_height())
        if key!=self._view_key:
            self._view_key=key
            self.schedule_view()

    def schedule_view(self,_event=None):
        if self._view_job is None:self._view_job=self.after_idle(self._commit_view)

    def _commit_view(self):
        self._view_job=None
        if not self.winfo_exists() or not self.winfo_ismapped():return
        if self._on_view:self._on_view()
        # A trailing debounce never runs during a long thumb drag. Repair is
        # now bounded to one frame, including continuous scrolling, not every
        # input. No nested Tk update loop or whole-window double buffering.
        request_native_repaint(self)

    def _destroy_surface(self,event):
        if event.widget is not self:return
        for name in ('_view_job',):
            job=getattr(self,name)
            if job is not None:
                self.after_cancel(job);setattr(self,name,None)


class CanvasBackdrop:
    """Compatibility surface: background pixels are Canvas items, not HWNDs."""
    def __init__(self,canvas,item):self.canvas,self.item=canvas,item
    def configure(self,*,image='',bg=None):
        self.canvas.itemconfigure(self.item,image=image)
        if bg is not None:self.canvas.configure(bg=bg)
    def winfo_exists(self):return self.canvas.winfo_exists()
    def lower(self):self.canvas.tag_lower(self.item)


_REPAINTS=WeakKeyDictionary()
_WIN_PAINT=None

def _redraw_native(widget):
    if sys.platform!='win32' or not widget.winfo_exists() or not widget.winfo_ismapped():return
    global _WIN_PAINT
    import ctypes
    if _WIN_PAINT is None:
        class Rect(ctypes.Structure):
            _fields_=[('left',ctypes.c_long),('top',ctypes.c_long),('right',ctypes.c_long),('bottom',ctypes.c_long)]
        redraw=ctypes.WINFUNCTYPE(ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint)(('RedrawWindow',ctypes.windll.user32))
        _WIN_PAINT=redraw,Rect
    redraw,Rect=_WIN_PAINT
    viewport=Rect(0,0,widget.winfo_width(),widget.winfo_height())
    # Invalidate only the viewport and its children. WM_PAINT is processed by
    # the normal native/Tk loop, not synchronously inside the scroll callback.
    # RDW_UPDATENOW doubled work on dense forms; no forced nested paint here.
    redraw(widget.winfo_id(),ctypes.byref(viewport),None,0x0001|0x0080|0x0004)

def request_native_repaint(widget):
    if sys.platform!='win32':return
    owner=widget
    while owner is not None:
        coordinator=_REPAINTS.get(owner)
        if coordinator is not None and not coordinator._closed:
            coordinator.request(widget);return
        owner=getattr(owner,'master',None)
    top=widget.winfo_toplevel()
    if top not in _REPAINTS:_REPAINTS[top]=NativeScrollRepaint(top)
    _REPAINTS[top].request(widget)

def install_scroll_repaint(root):
    if sys.platform!='win32':return None
    if root not in _REPAINTS:_REPAINTS[root]=NativeScrollRepaint(root)
    return _REPAINTS[root]

class NativeScrollRepaint:
    """Observe scroll inputs without replacing native/IME/local bindings.

    Canvas pages, Text/Listbox/Treeview and scrollbar thumb drags share a
    coalesced repair. Future dialogs enrol on Map. Offscreen pages stay idle.
    """
    def __init__(self,root):
        self.root=root;self._pending=WeakSet();self._states=WeakKeyDictionary()
        self._job=None;self._closed=False;self.repairs=0
        self._tag='RemChatScrollObserve'+str(id(self))
        self._events=('<MouseWheel>','<Button-4>','<Button-5>','<B1-Motion>','<ButtonRelease-1>','<KeyRelease>')
        for event in self._events:root.bind_class(self._tag,event,self._observe)
        self._map_id=root.bind_all('<Map>',self._mapped,add=True)
        root.bind('<Destroy>',self._destroy,add=True)
        self._scan(root)

    def _owns(self,widget):
        while widget is not None:
            if widget is self.root:return True
            widget=getattr(widget,'master',None)
        return False

    def _enrol(self,widget):
        if isinstance(widget,(tk.Canvas,tk.Text,tk.Listbox,ttk.Treeview,tk.Scrollbar,ttk.Scrollbar)):
            tags=widget.bindtags()
            if self._tag not in tags:widget.bindtags((self._tag,*tags))

    def _scan(self,widget):
        self._enrol(widget)
        for child in widget.winfo_children():self._scan(child)

    def _mapped(self,event):
        if not self._closed and isinstance(event.widget,tk.Misc) and self._owns(event.widget):self._enrol(event.widget)

    @staticmethod
    def _scrollable(widget):
        if isinstance(widget,(tk.Text,tk.Listbox,ttk.Treeview)):return True
        return isinstance(widget,tk.Canvas) and bool(widget.cget('yscrollcommand'))

    def _target(self,widget):
        if isinstance(widget,(tk.Scrollbar,ttk.Scrollbar)):
            for candidate in widget.master.winfo_children():
                if self._scrollable(candidate):return candidate
        while widget is not None and self._owns(widget):
            if self._scrollable(widget):return widget
            widget=getattr(widget,'master',None)
        return None

    def _observe(self,event):
        if self._closed:return
        target=self._target(event.widget)
        if target is not None:self.request(target)
        # Never return 'break': existing wheel scope, keys and selection win.

    def request(self,widget):
        if self._closed or not widget.winfo_exists():return
        self._pending.add(widget)
        if self._job is None:self._job=self.root.after(FRAME_MS,self._flush)

    def _flush(self):
        self._job=None;targets=list(self._pending);self._pending.clear()
        changed=[]
        for widget in targets:
            try:
                if not widget.winfo_exists() or not widget.winfo_ismapped():continue
                state=(widget.yview(),widget.winfo_width(),widget.winfo_height())
                if self._states.get(widget)==state:continue
                self._states[widget]=state
                changed.append(widget)
            except tk.TclError:continue
        # Only an ancestor that will really paint covers a child's repair.
        # An unchanged outer Canvas must not suppress a scrolling inner Text.
        for widget in changed:
            try:
                ancestor=getattr(widget,'master',None);covered=False
                while ancestor is not None:
                    if ancestor in changed:covered=True;break
                    ancestor=getattr(ancestor,'master',None)
                if not covered:_redraw_native(widget);self.repairs+=1
            except tk.TclError:continue

    def _destroy(self,event):
        if event.widget is not self.root:return
        self._closed=True
        if self._job is not None:self.root.after_cancel(self._job);self._job=None
        for sequence in self._events:self.root.unbind_class(self._tag,sequence)
        script=str(self.root.tk.call('bind','all','<Map>'))
        self.root.tk.call('bind','all','<Map>','\n'.join(line for line in script.splitlines() if self._map_id not in line))
        try:self.root.deletecommand(self._map_id)
        except tk.TclError:pass
        self._pending.clear()
        _REPAINTS.pop(self.root,None)
