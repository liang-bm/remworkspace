"""Logical Canvas rows: no native child is placed inside a huge parent frame."""

from __future__ import annotations


def layout_message_rows(view) -> None:
    canvas = view.message_canvas
    width = max(1, canvas.winfo_width())
    viewport_height = max(1, canvas.winfo_height())
    header_height = (view.messages_frame.winfo_reqheight()
                     if view.messages_frame.winfo_children() else 0)
    canvas.itemconfigure(view.message_window, width=width,
                         height=max(1, header_height),
                         state="normal" if header_height else "hidden")
    y = header_height
    for message in view._message_labels:
        if not message.winfo_exists():
            continue
        # Full answer geometry belongs to the logical scroll surface. Native
        # child windows are mounted separately using only their visible slice.
        bubble_width = getattr(message, "_width", message.winfo_reqwidth())
        bubble_height = getattr(message, "_height", message.winfo_reqheight())
        x = width - bubble_width - 18 if message.role == "user" else 18
        y += 9
        message._message_world_x = max(0, x)
        message._message_world_y = y
        y += bubble_height + 9
    view._messages_height = max(viewport_height, y)
    # The fixed viewport background must not contribute to the scroll extent.
    canvas.configure(scrollregion=(0, 0, width, view._messages_height))
    position_visible_message_rows(view)


def position_visible_message_rows(view) -> None:
    """Keep physical windows viewport-sized, including a single huge answer.

    Moving a 70k-pixel native child far above the viewport can clamp its Y
    coordinate on Windows. Logical height remains unbounded; paint and pointer
    events translate through the slice's local origin instead.
    """
    canvas = view.message_canvas
    viewport_top = int(canvas.canvasy(0))
    viewport_bottom = viewport_top + max(1, canvas.winfo_height())
    for message in view._message_labels:
        if (not message.winfo_exists() or not hasattr(message, "_message_world_y")
                or not hasattr(message, "_height")):
            continue
        y = message._message_world_y
        top = max(y, viewport_top)
        bottom = min(y + message._height, viewport_bottom)
        if bottom <= top:
            if getattr(message,'_mounted_slice',None) is not None:
                canvas.itemconfigure(message._row_window, state="hidden")
                message._mounted_slice=None
            continue
        mounted=(message._message_world_x,top,bottom,message._width,y)
        if mounted==getattr(message,'_mounted_slice',None):continue
        message._mounted_slice=mounted
        message.set_visible_slice(top - y, bottom - y)
        canvas.coords(message._row_window, message._message_world_x, top)
        canvas.itemconfigure(message._row_window, width=message._width,
                             height=bottom - top, state="normal")
