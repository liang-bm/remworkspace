"""主聊天半透明消息：背景像素合成，文字仍由 Tk 清晰绘制。

THESIS: 一句话占一句话的空间，背景不会被整块不透明 Text 遮住。
OWN-WORLD: 既有蓝白/深色主题，50% 底板和原生字体。
STORY: 阅读、拖选复制、展开长文；不产生模型调用。
FIRST VIEWPORT: 用户靠右、雷姆靠左，宽高由实际字体与内容决定。
FORM: 局部替换消息组件，保持历史分页和共享对话生命周期。
"""

from __future__ import annotations

import re
from math import ceil
from bisect import bisect_right
import tkinter as tk
import tkinter.font as tkfont
from .font_source import native_font
import webbrowser

from PIL import Image, ImageDraw, ImageTk

from ..clipboard import copy_text
from . import theme
from .markdown_document import parse_markdown, safe_link
from .markdown_layout import FontMeasurements, copy_visible, layout_document
from .markdown_preview import preview_document, LANGUAGES


class MessageBubble(tk.Canvas):
    PREVIEW_LINES = 12
    OPACITY = 228

    def __init__(self, parent, *, role, font, background, on_layout, visible_region=None,
                 before_layout=None, after_layout=None, render_fenced_markdown=True,
                 show_role=True, full_width=False):
        super().__init__(parent, width=80, height=60, highlightthickness=0,
                         bd=0, takefocus=True, cursor="xterm", bg=theme.APP_BG)
        self.role = role
        self._show_role = show_role
        self._full_width = full_width
        self._render_fenced_markdown = render_fenced_markdown
        self._preview_overrides = {}
        self._font_spec = font
        self._background = background
        self._on_layout = on_layout
        self._visible_region = visible_region
        self._before_layout = before_layout
        self._after_layout = after_layout
        self._layout_key = None
        self._text = ""
        self._lines = []
        self._document = ()
        self._document_text = None
        self._actions = []
        self._expanded = False
        self._selection = (0, 0)
        self._pressed = 0
        self._photo = None
        self._paint_key = None
        self._viewport = 600
        self._display_top = 0
        self._remchat_theme_fixed = True
        self._remchat_locale_exclude = True  # 对话正文不能当 UI 文案翻译
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Motion>", self._hover)
        self.bind("<Tab>", lambda event: self._focus_action(event, 1))
        self.bind("<Shift-Tab>", lambda event: self._focus_action(event, -1))
        self.bind("<ISO_Left_Tab>", lambda event: self._focus_action(event, -1))
        self.bind("<Double-Button-1>", self._select_word)
        self.bind("<Control-a>", self._select_all)
        self.bind("<Control-c>", self._copy)
        self.bind("<<Copy>>", self._copy)
        self.bind("<Button-3>", self._menu)
        self.bind("<Return>", self._keyboard_activate)
        self.bind("<space>", self._keyboard_activate)
        self.bind("<FocusIn>", lambda _: self.paint(), add=True)
        self.bind("<FocusOut>", lambda _: self.paint(), add=True)
        self.bind("<Configure>", self._geometry_paint)
        self.bind("<Map>", self._geometry_paint, add=True)

    def configure(self, cnf=None, **kw):
        if isinstance(cnf, dict):
            kw = {**cnf, **kw}
            cnf = None
        changed = False
        if "font" in kw:
            font = kw.pop("font")
            changed = font != self._font_spec
            self._font_spec = font
        kw.pop("state", None)  # 只读；兼容现有回复更新入口
        result = super().configure(cnf, **kw) if kw or cnf is not None else None
        if changed and hasattr(self, "_text"):
            self.fit(self._viewport)
        return result

    config = configure

    def cget(self, key):
        if key == "font":
            return self._font_spec
        return super().cget(key)

    def get(self, _start="1.0", _end="end-1c"):
        return self._text

    def insert(self, _index, text):
        self._text = str(text)
        self._selection = (0, 0)

    def delete(self, first, last=None):
        if first == "1.0":
            self._text = ""
            self._selection = (0, 0)
            self._preview_overrides.clear()
        else:
            super().delete(first, *(() if last is None else (last,)))

    def _tr(self, text):
        translator = getattr(self.winfo_toplevel(), "_remchat_translator", None)
        return translator(text) if translator else text

    def fit(self, viewport_width):
        self._viewport = viewport_width
        # Repeated Configure, batch, font and locale callbacks often describe
        # the same layout. Keep the character map and selection in that case.
        key = (viewport_width, self._text, self._font_spec, self._expanded,
               self._tr("你"), self._tr("雷姆"), self._tr("展开全文"),
               tuple(sorted(self._preview_overrides.items())), float(self.tk.call("tk", "scaling")))
        if key == self._layout_key:
            return
        font_key = (self._font_spec, key[-1])
        if getattr(self, "_font_key", None) != font_key:
            self._font = self._shared_font(("body",), font=self._font_spec)
            self._small = self._shared_font(("small",), font=(self._font.cget("family"), 9))
            self._font_key = font_key
            self._styled_fonts = {}
        self._pad = max(16, self._font.measure("0")*1.3)
        max_width = (max(36,viewport_width) if self._full_width else
                     max(36,min(viewport_width-64,int(viewport_width*.80),self._font.measure('0')*70)))
        limit = max(12, max_width - self._pad * 2)
        if self._document_text != self._text:
            self._raw_document = parse_markdown(self._text)
            self._document_text = self._text
        self._document = preview_document(self._raw_document,self._preview_overrides,
                                          self._render_fenced_markdown)
        self._lines, _content_height, used_width = layout_document(
            self._document, limit, self._styled_font, self._font.measure("0"),
            max_rows=None if self._expanded else self.PREVIEW_LINES + 1,
        )
        self._visible = self._lines if self._expanded else self._lines[:self.PREVIEW_LINES]
        self._row_tops = [row.y for row in self._visible]
        self._source_rows = sorted((fragment.start, fragment.end, row_index)
                                   for row_index, row in enumerate(self._visible)
                                   for fragment in row.fragments if fragment.source)
        self._source_starts = [entry[0] for entry in self._source_rows]
        self._line_height = self._font.metrics("linespace") + 4
        self._header_height = self._small.metrics("linespace") + 10 if self._show_role else 0
        self._footer_height = self._small.metrics("linespace") + 14
        self._foldable = len(self._lines) > self.PREVIEW_LINES
        width = max(self._small.measure(self._tr("雷姆" if self.role != "user" else "你")),
                    used_width) + 2 * self._pad
        if self._foldable:
            width = max(width, self._small.measure(self._tr("展开全文")) + 2 * self._pad)
        self._width = ceil(min(max_width, max(64, width)))
        if self._full_width:self._width=ceil(max_width)
        content_height = self._visible[-1].y + self._visible[-1].height if self._visible else 0
        self._height = (self._header_height + 2 * self._pad + content_height
                        + (self._footer_height if self._foldable else 0))
        self._height = int(self._height + .5)
        self._layout_key = key
        # A visible-region host owns the physical slice height. Never briefly
        # allocate a full native window for an expanded, very long answer.
        if self._visible_region:
            super().configure(width=self._width)
        else:
            super().configure(width=self._width, height=self._height)
        self.paint()

    def _styled_font(self, key):
        if key == (False, False, False, False, 0):
            return self._font
        if key not in self._styled_fonts:
            code, bold, italic, strike, level = key
            size = int(self._font.cget("size"))
            if level:
                size += (1 if size>=0 else -1)*max(1,round(abs(size)*{1:.45,2:.28,3:.15}.get(level,.05)))
            self._styled_fonts[key] = self._shared_font(
                ("style", key), family="Consolas" if code else self._font.cget("family"),
                size=size, weight="bold" if bold else "normal",
                slant="italic" if italic else "roman", overstrike=bool(strike),
            )
        return self._styled_fonts[key]

    def _shared_font(self, key, **options):
        # Share glyph fallback and width caches between answers in one window;
        # a new DPI or body size gets distinct fonts, never stale measurements.
        owner = self.winfo_toplevel()
        pool = getattr(owner, "_remchat_markdown_fonts", None)
        if pool is None:
            pool = owner._remchat_markdown_fonts = {}
        identity = (repr(self._font_spec), float(self.tk.call("tk", "scaling")), key)
        if identity not in pool:
            pool[identity] = FontMeasurements(native_font(owner,**options))
            if len(pool) > 256:
                pool.pop(next(iter(pool)))
        return pool[identity]

    def set_visible_slice(self, top, bottom):
        """Mount a logical answer slice; keep text, selection and full height."""
        self._display_top = top
        height = max(1, bottom - top)
        if int(super().cget("height")) != height:
            super().configure(height=height)

    def capture_text_anchor(self, local_y):
        """Identify the reading line by source character, not old font pixels."""
        text_top = self._header_height + self._pad
        row = bisect_right(self._row_tops, local_y - text_top) - 1
        if not 0 <= row < len(self._visible):
            return None
        line = self._visible[row]
        if not line.fragments:
            # Fences, rules and blank spacer rows have no source glyph to
            # relocate. Let the host keep its pixel anchor instead of mapping
            # the block's opening marker to a different body-text row.
            return None
        return line.start, local_y - text_top - line.y

    def text_anchor_position(self, anchor):
        character, within_line = anchor
        if not self._visible:
            return self._header_height + self._pad
        index = max(0, bisect_right(self._source_starts, character) - 1)
        row = self._source_rows[index][2] if self._source_rows else 0
        line = self._visible[row]
        return (self._header_height + self._pad + line.y
                + min(within_line, line.height - 1))

    def toggle_expanded(self):
        if self._foldable:
            anchor = self._before_layout() if self._before_layout else None
            self._expanded = not self._expanded
            self.fit(self._viewport)
            self._on_layout()
            if self._after_layout:
                self._after_layout(anchor)
        return "break"

    def paint(self):
        # Cached synchronous painting keeps selection feedback immediate and
        # avoids a separate timer/Configure burst for every visible bubble.
        self._paint_now()

    def _geometry_paint(self,_event=None):
        provider=getattr(self._background,'__self__',None)
        if provider is not None and hasattr(provider,'message_canvas'):
            if self._paint_key is None:self.paint()
        else:self.paint()

    def _paint_now(self):
        if not hasattr(self, "_font") or not self.winfo_exists():
            return
        region = self._visible_region(self, self._height) if self._visible_region else (0, self._height)
        if region is None:
            # A page still retains its text, geometry and selection, but not
            # a full-size Pillow/Tk image for every off-screen message.
            self._photo = None
            self._composite = None
            self._paint_key = None
            self._content_key = None
            self._actions = []
            super().delete("all")
            return
        if not self.winfo_ismapped():return
        top, bottom = region
        # The known chat provider exposes immutable viewport pixels. Compare
        # their identity and sampling coordinates before cropping/allocating a
        # bitmap. Unknown providers remain uncached rather than risk stale art.
        provider = getattr(self._background, "__self__", None)
        if provider is not None and hasattr(provider, "message_canvas"):
            pixels = getattr(provider, "_background_pixels", None)
            background_key = (id(pixels), getattr(provider, "_background_render_key", None))
            if pixels is not None:
                background_key += (getattr(self, "_message_world_x", 0),
                                   getattr(self, "_message_world_y", 0),
                                   int(provider.message_canvas.canvasy(0)))
        else:
            background_key = object()
        # A wider viewport may still produce exactly the same constrained
        # bubble/line geometry. Cache the rendered shape, not the input width.
        shape = (self._layout_key[1:], self._width, self._height, tuple(self._visible))
        content_key=(shape,region,self._display_top,self._selection,theme.get_mode(),
                     getattr(self,'_focused_action',None),self.focus_get() is self)
        paint_key = (content_key,background_key)
        if paint_key == self._paint_key:
            return
        keep_text=content_key==getattr(self,'_content_key',None)
        if not keep_text:super().delete("all")
        self._paint_top = top
        render_height = bottom - top
        colors = theme.palette()
        base = self._background(self, self._width, render_height)
        # Snapshot callback returns the exact viewport pixels at this widget.
        # No toplevel alpha: text, controls and the desktop pet remain opaque.
        if base is None:
            base = Image.new("RGB", (self._width, render_height), colors["APP_BG"])
        base = base.convert("RGBA")
        overlay = Image.new("RGBA", base.size)
        ink = colors["USER_BUBBLE" if self.role == "user" else "REM_BUBBLE"]
        rgb = tuple(int(ink[n:n + 2], 16) for n in (1, 3, 5))
        draw = ImageDraw.Draw(overlay)
        draw.rounded_rectangle(
            (0, -top, self._width - 1, self._height - 1 - top),
            radius=16, fill=(*rgb, self.OPACITY))
        # One rounded, contained card for the entire fenced block, including
        # rendered Markdown examples. Never paint flat strips per text row.
        cards = {}
        for line in self._visible:
            if line.card_start is not None:
                start, end = cards.get(line.card_start, (line.y, line.y+line.height))
                cards[line.card_start] = (min(start,line.y), max(end,line.y+line.height))
        card_fill = '#242A34' if theme.get_mode() == 'dark' else '#F2F5FA'
        card_top = self._header_height+self._pad
        for start,end in cards.values():
            draw.rounded_rectangle((self._pad-5,card_top+start-top-4,
                                    self._width-self._pad+5,card_top+end-top+8),
                                   radius=14, fill=card_fill, outline=colors['LINE'], width=1)
        self._composite = Image.alpha_composite(base, overlay)
        if keep_text:
            self._photo.paste(self._composite)
            self._paint_key=paint_key
            return
        self._photo = ImageTk.PhotoImage(self._composite, master=self)
        self.create_image(0, top - self._display_top, image=self._photo, anchor="nw")
        self._content_key=content_key
        if self._show_role and top < self._header_height:
            self._draw_text(self._width - 10 if self.role == "user" else 10, 6,
                         anchor="ne" if self.role == "user" else "nw",
                         text=self._tr("你" if self.role == "user" else "雷姆"),
                         font=self._small, fill=colors["INK"])
        a, b = sorted(self._selection)
        text_top = self._header_height + self._pad
        self._actions = []
        first_row = max(0, bisect_right(self._row_tops, top - text_top) - 1)
        last_row = min(len(self._visible), bisect_right(self._row_tops, bottom - text_top) + 1)
        for row in range(first_row, last_row):
            line = self._visible[row]
            y = text_top + line.y
            local_y = y - self._display_top
            if line.kind == "table_header":
                self.create_rectangle(self._pad - 4, local_y, self._width - self._pad + 4,
                                      local_y + line.height, fill=colors["APP_BG"], outline="")
            if line.kind == "rule":
                self.create_line(self._pad, local_y + line.height / 2,
                                 self._width - self._pad, local_y + line.height / 2,
                                 fill=colors["LINE"])
            if line.kind == "quote":
                self.create_line(self._pad, local_y, self._pad, local_y + line.height,
                                 fill=colors["MUTED"])
            if line.cell_edges:
                for edge in line.cell_edges:
                    self.create_line(self._pad + edge, local_y, self._pad + edge,
                                     local_y + line.height, fill=colors["LINE"])
                if line.table_top:
                    self.create_line(self._pad, local_y, self._width - self._pad, local_y, fill=colors["LINE"])
                if line.table_end:
                    self.create_line(self._pad, local_y + line.height,
                                     self._width - self._pad, local_y + line.height, fill=colors["LINE"])
            if line.prefix:
                prefix_x = self._pad + line.prefix_x
                if line.prefix in ("☑", "☐"):
                    side = self._font.metrics("linespace") * .65
                    box_y = local_y + self._font.metrics("linespace") * .15
                    from .rounded_controls import rounded_box
                    rounded_box(self, (prefix_x,box_y,prefix_x+side,box_y+side),radius=side*.22,
                                outline=colors['ACCENT_DEEP'] if line.prefix=='☑' else colors['MUTED'],
                                fill=colors['ACCENT_DEEP'] if line.prefix=='☑' else '', width=1)
                    if line.prefix == "☑":
                        self.create_line(prefix_x + side * .18, box_y + side * .5,
                                         prefix_x + side * .42, box_y + side * .75,
                                         prefix_x + side * .86, box_y + side * .2,
                                         fill=colors['ON_ACCENT'], width=max(1, round(side / 9)))
                else:
                    prefix = line.prefix
                    if line.kind == "code_header":
                        available = max(0, self._width - 2 * self._pad - self._small.measure(self._tr("复制代码")) - self._small.measure(self._tr("预览")) - 36)
                        while prefix and self._small.measure(prefix) > available:
                            prefix = prefix[:-1]
                    self._draw_text(prefix_x, y, anchor="nw", text=prefix,
                                    font=self._small if line.kind == "code_header" else self._font,
                                    fill=colors["MUTED"] if line.kind == "code_header" else colors["INK"])
            if line.action:
                label = self._tr("复制")
                action_width = self._small.measure(label)
                action_x = self._width - self._pad - action_width
                self._draw_text(action_x, y, anchor="nw", text=label, font=self._small,
                                fill=colors["ACCENT"] if theme.get_mode() == "dark" else colors["ACCENT_DEEP"])
                self._actions.append((action_x - 4, local_y - 2, self._width - self._pad + 4,
                                      local_y + line.height, "copy_code", line.code))
                block=next((b for b in self._raw_document if b.kind=='code' and b.start==line.start),None)
                if self._render_fenced_markdown and block and block.language.lower() in LANGUAGES:
                    label=self._tr('源码' if self._preview_overrides.get(block.start,True) else '预览')
                    right=action_x-16
                    left=right-self._small.measure(label)
                    self._draw_text(left,y,anchor='nw',text=label,font=self._small,fill=colors['ACCENT'] if theme.get_mode()=='dark' else colors['ACCENT_DEEP'])
                    self._actions.append((left-4,local_y-2,right+4,local_y+line.height,'markdown_preview',block.start))
            for fragment in line.fragments:
                font = self._styled_font(fragment.font_key)
                x = self._pad + fragment.x
                if 'highlight' in fragment.styles:
                    self.create_rectangle(x-1,local_y,x+fragment.width+1,local_y+line.height,
                        fill='#665528' if theme.get_mode()=='dark' else '#FFF1A8',outline='')
                if "code" in fragment.styles and line.kind != "code":
                    from .rounded_controls import rounded_box
                    rounded_box(self,(x-2,local_y,x+fragment.width+2,local_y+line.height),
                                radius=4,fill=colors['APP_BG'],outline='')
                selected = [index for index, position in enumerate(fragment.source) if a <= position < b]
                if selected:
                    x1 = x + font.measure(fragment.text[:selected[0]])
                    x2 = x + font.measure(fragment.text[:selected[-1] + 1])
                    self.create_rectangle(x1, local_y, x2, local_y + line.height,
                                          fill=colors["ACCENT_SOFT"], outline="")
                self._draw_text(x, y, anchor="nw", text=fragment.text, font=font,
                                fill=(colors["ACCENT"] if theme.get_mode() == "dark" else colors["ACCENT_DEEP"])
                                if fragment.link else colors["INK"])
                if fragment.link:
                    self.create_line(x, local_y + font.metrics("linespace"),
                                     x + fragment.width, local_y + font.metrics("linespace"),
                                     fill=colors["ACCENT"] if theme.get_mode() == "dark" else colors["ACCENT_DEEP"])
                    self._actions.append((x, local_y, x + fragment.width, local_y + line.height,
                                          "link", fragment.link))
        focused = getattr(self, "_focused_action", None)
        if focused and self.focus_get() is self:
            for action in self._actions:
                if (action[4], action[5]) == focused:
                    self.create_rectangle(action[0] - 2, action[1] - 1,
                                          action[2] + 2, action[3] + 1,
                                          outline=colors["ACCENT"], dash=(2, 2))
        if self._foldable and bottom >= self._height - self._footer_height:
            self._draw_text(self._pad, self._height - self._footer_height,
                             anchor="nw", text=self._tr("收起全文" if self._expanded else "展开全文"),
                             fill=colors["ACCENT"] if theme.get_mode() == "dark" else colors["ACCENT_DEEP"],
                             font=self._small, tags="fold")
            self.tag_bind("fold", "<Button-1>", lambda _: self.toggle_expanded())
        self._paint_key = paint_key

    def _draw_text(self, x, y, **options):
        """A readable plate replaces the four-pass cartoon glyph outline."""
        y -= self._display_top
        return self.create_text(x, y, **options)

    def _index_at(self, event):
        if not self._visible:
            return 0
        row = min(len(self._visible) - 1, max(0, bisect_right(self._row_tops,
            event.y + self._display_top - self._header_height - self._pad) - 1))
        line = self._visible[row]
        if not line.fragments:
            return line.start
        relative_x = event.x - self._pad
        # Formatting delimiters create source gaps at the exact same pixel.
        # Prefer the run on the right at such a boundary, not the removed '*'.
        fragment = next((f for f in line.fragments if f.x <= relative_x < f.x + f.width), None)
        if fragment is None:
            fragment = min(reversed(line.fragments), key=lambda f: max(
                f.x - relative_x, relative_x - f.x - f.width, 0))
        text = fragment.text
        font = self._styled_font(fragment.font_key)
        x = max(0, event.x - self._pad - fragment.x)
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.measure(text[:mid]) <= x:
                lo = mid
            else:
                hi = mid - 1
        if lo < len(text) and x > (font.measure(text[:lo]) + font.measure(text[:lo + 1])) / 2:
            lo += 1
        return fragment.source[lo] if lo < len(fragment.source) else fragment.end

    def _press(self, event):
        self.focus_set()
        self._focused_action = None
        self._press_point = (event.x, event.y)
        self._press_action = self._action_at(event)
        self._pressed = self._index_at(event)
        self._selection = (self._pressed, self._pressed)
        self.paint()

    def _drag(self, event):
        self._selection = (self._pressed, self._index_at(event))
        self.paint()
        return "break"

    def _action_at(self, event):
        return next((action for action in self._actions
                     if action[0] <= event.x <= action[2] and action[1] <= event.y <= action[3]), None)

    def _hover(self, event):
        self.configure(cursor="hand2" if self._action_at(event) else "xterm")

    def _release(self, event):
        action = self._action_at(event)
        point = getattr(self, "_press_point", (event.x, event.y))
        if (action and action == getattr(self, "_press_action", None)
                and max(abs(event.x - point[0]), abs(event.y - point[1])) <= 4):
            self._activate_action(action[4], action[5])
            return "break"

    def _activate_action(self, kind, value):
        if kind == "markdown_preview":
            anchor=self._before_layout() if self._before_layout else None
            self._preview_overrides[value]=not self._preview_overrides.get(value,True)
            self.fit(self._viewport)
            self._on_layout()
            if self._after_layout: self._after_layout(anchor)
        elif kind == "copy_code":
            copy_text(self, value)
        elif kind == "link" and safe_link(value):
            try:
                webbrowser.open(value)
            except (OSError, webbrowser.Error):
                self.bell()

    def _focus_action(self, _event, step):
        actions = list(dict.fromkeys((action[4], action[5]) for action in self._actions))
        if not actions:
            return None
        current = getattr(self, "_focused_action", None)
        index = actions.index(current) if current in actions else (-1 if step > 0 else len(actions))
        index += step
        if not 0 <= index < len(actions):
            self._focused_action = None
            self._paint_key = None
            self.paint()
            return None  # Leave this canvas through Tk's ordinary focus order.
        self._focused_action = actions[index]
        self._paint_key = None
        self.paint()
        return "break"

    def _keyboard_activate(self, _event):
        focused = getattr(self, "_focused_action", None)
        if focused and any((action[4], action[5]) == focused for action in self._actions):
            self._activate_action(*focused)
            return "break"
        return self.toggle_expanded()

    def _select_word(self, event):
        index = self._index_at(event)
        for match in re.finditer(r"\w+|\S", self._text):
            if match.start() <= index <= match.end():
                self._selection = (match.start(), match.end())
                self.paint()
                break
        return "break"

    def _select_all(self, _event=None):
        self._selection = (0, len(self._text))
        self.paint()
        return "break"

    def _copy(self, _event=None, *, full=False, visible_full=False):
        a, b = sorted(self._selection)
        text = self._text if full else copy_visible(self._document) if visible_full else copy_visible(self._document, a, b)
        if text:
            copy_text(self, text)
        return "break"

    def _menu(self, event):
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label=self._tr("复制"), command=self._copy,
                         state="normal" if self._selection[0] != self._selection[1] else "disabled")
        menu.add_command(label=self._tr("复制 Markdown 原文"), command=lambda: self._copy(full=True))
        menu.add_command(label=self._tr("复制渲染全文"), command=lambda: self._copy(visible_full=True))
        menu.add_command(label=self._tr("全选"), command=self._select_all)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        old = getattr(self, "_context_menu", None)
        if old:
            old.destroy()
        self._context_menu = menu
