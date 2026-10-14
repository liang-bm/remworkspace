"""Copy requested font settings, not a platform-scaled font match."""
from __future__ import annotations
import tkinter as tk
import tkinter.font as tkfont

def native_font(root, *, font=None, **options):
    if font is None:return tkfont.Font(root=root,**options)
    if isinstance(font,tkfont.Font):settings=font.configure()
    else:
        try:
            values=root.tk.splitlist(root.tk.call('font','configure',font))
            settings={str(key).lstrip('-'):value for key,value in zip(values[::2],values[1::2])}
        except tk.TclError:
            values=root.tk.splitlist(font)
            try:size=int(values[1])
            except (ValueError,IndexError):
                # Preserve the incumbent handling of uncommon XLFD formats.
                return tkfont.Font(root=root,font=font,**options)
            settings={'family':values[0],'size':size}
            for style in (word for value in values[2:] for word in str(value).split()):
                if style in ('normal','bold'):settings['weight']=style
                elif style in ('roman','italic'):settings['slant']=style
                elif style in ('underline','overstrike'):settings[style]=True
    settings.update(options)
    # Font(font=...) calls Tcl font actual before creating a named font. On
    # GNOME/Xft this already-scaled match can be scaled again, unlike Font's
    # explicit options. Keep logical points / negative pixel sizes verbatim.
    return tkfont.Font(root=root,**settings)
