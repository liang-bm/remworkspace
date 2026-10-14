"""Pixel layout for safe Markdown, with exact source offsets on every glyph.

The caller supplies native fonts. No Tk event-loop reentry, widgets, bitmap or
network allocation happens here. Collapsed answers stop after one extra row.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections import OrderedDict
import re

from .markdown_document import Block, Span


class FontMeasurements:
    """One interpreter-owned font plus a bounded native measurement cache."""
    def __init__(self, native):
        self.native = native
        self._widths = OrderedDict()
        self._metrics = {}

    def __str__(self):
        return str(self.native)

    def actual(self, *args, **kwargs):
        return self.native.actual(*args, **kwargs)

    def cget(self, option):
        return self.native.cget(option)

    def measure(self, text):
        if text in self._widths:
            return self._widths[text]
        value = self.native.measure(text)
        if len(text) <= 4096:
            self._widths[text] = value
            if len(self._widths) > 2048:
                self._widths.popitem(last=False)
        return value

    def metrics(self, key):
        if key not in self._metrics:
            self._metrics[key] = self.native.metrics(key)
        return self._metrics[key]


@dataclass(frozen=True)
class Fragment:
    text: str
    source: range | tuple[int, ...]
    styles: frozenset[str]
    link: str
    x: float
    width: float
    font_key: tuple

    @property
    def start(self):
        return self.source[0] if self.source else 0

    @property
    def end(self):
        return self.source[-1] + 1 if self.source else self.start


@dataclass(frozen=True)
class Row:
    y: float
    height: float
    start: int
    end: int
    fragments: tuple[Fragment, ...] = ()
    kind: str = "paragraph"
    prefix: str = ""
    prefix_x: float = 0
    action: str = ""
    code: str = ""
    cell_edges: tuple[float, ...] = ()
    table_top: bool = False
    table_end: bool = False
    card_start: int | None = None


def font_key(styles, level=0):
    return ("code" in styles, "bold" in styles or bool(level), "italic" in styles,
            "strike" in styles, level)


def _wrap(spans, width, fonts, level=0, max_rows=None):
    """Wrap runs with bounded measure probes, not repeated full-tail measures."""
    if max_rows is not None and max_rows <= 0:
        return []
    rows = []
    fragments = []
    x = 0
    height = fonts(font_key(frozenset(), level)).metrics("linespace")

    def finish():
        nonlocal fragments, x, height
        rows.append((tuple(fragments), height))
        fragments = []
        x = 0
        height = fonts(font_key(frozenset(), level)).metrics("linespace")

    for span in spans:
        key = font_key(span.styles, level)
        font = fonts(key)
        cursor = 0
        while cursor < len(span.text):
            if max_rows is not None and len(rows) >= max_rows:
                return rows
            if span.text[cursor] == "\n":
                finish()
                cursor += 1
                continue
            newline = span.text.find("\n", cursor)
            stop = min(len(span.text) if newline < 0 else newline, cursor + 256)
            available = max(1, width - x)
            chunk = span.text[cursor:stop]
            full_width = font.measure(chunk)
            if full_width <= available:
                take = len(chunk)
                pixels = full_width
            else:
                lo, hi = 0, len(chunk)
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if font.measure(chunk[:mid]) <= available:
                        lo = mid
                    else:
                        hi = mid - 1
                take = lo
                if not take:
                    if fragments:
                        finish()
                        continue
                    take = 1  # A single glyph may exceed an extremely narrow cell.
                if take < len(chunk):
                    cut = chunk.rfind(" ", 0, take)
                    if cut > 0 and re.search(r"[A-Za-z]", chunk[cut + 1:take]):
                        take = cut + 1
                pixels = font.measure(chunk[:take])
            text = span.text[cursor:cursor + take]
            fragments.append(Fragment(text, span.source[cursor:cursor + take],
                                      span.styles, span.link, x, pixels, key))
            x += pixels
            height = max(height, font.metrics("linespace"))
            cursor += take
            if take < len(chunk):
                finish()
    if fragments or not rows:
        finish()
    return rows


def layout_document(blocks: tuple[Block, ...], width, fonts, unit, max_rows=None):
    rows = []
    y = 0
    used_width = 0
    body_height = fonts(font_key(frozenset())).metrics("linespace")
    leading = max(3, round(unit * .25))
    gap = max(5, round(unit * .6))
    card_start = None
    previous_card = None

    def add(row):
        nonlocal y, used_width
        rows.append(replace(row, y=y, card_start=card_start))
        y += row.height
        used_width = max(used_width, *(f.x + f.width for f in row.fragments),
                         width if row.kind in ("code", "code_header", "table", "table_header", "rule") else 0)

    for block in blocks:
        card_start = block.card_start if block.card_start is not None else (block.start if block.kind == 'code' else None)
        if rows and previous_card != card_start and (previous_card is not None or card_start is not None):
            y += max(12,gap*3)
        previous_card=card_start
        remaining = None if max_rows is None else max_rows - len(rows)
        if remaining is not None and remaining <= 0:
            break
        if block.kind == "space":
            add(Row(y, gap, block.start, block.end, kind="space"))
            continue
        if block.kind == "rule":
            add(Row(y, gap * 2, block.start, block.end, kind="rule"))
            continue
        if block.kind in ("code", "code_preview_header"):
            if rows and rows[-1].kind != "space":
                y += gap
            add(Row(y, body_height + gap*2, block.start, block.start, kind="code_header",
                    prefix=block.language or "Code", prefix_x=gap,
                    action="copy_code", code=block.code))
            if block.kind == "code_preview_header":
                continue
            remaining = None if max_rows is None else max_rows - len(rows)
            inner = max(1, width - gap * 2)
            for fragments, height in _wrap(block.spans, inner, fonts, max_rows=remaining):
                moved = tuple(replace(f, x=f.x + gap) for f in fragments)
                add(Row(y, height + leading, min((f.start for f in moved), default=block.start),
                        max((f.end for f in moved), default=block.end), moved, kind="code"))
            y += gap
            continue
        if block.kind == "table":
            count = len(block.cells)
            column_width = width / max(1, count)
            padding = min(gap, column_width * .12)
            columns = [_wrap(cell, max(1, column_width - padding * 2), fonts,
                             max_rows=remaining) for cell in block.cells]
            row_count = max(len(column) for column in columns)
            for index in range(row_count):
                if max_rows is not None and len(rows) >= max_rows:
                    break
                fragments = []
                height = body_height
                for col, column in enumerate(columns):
                    if index >= len(column):
                        continue
                    content, cell_height = column[index]
                    height = max(height, cell_height)
                    used = max((f.x + f.width for f in content), default=0)
                    align = block.align[col]
                    extra = max(0, column_width - 2 * padding - used)
                    shift = extra if align == "right" else extra / 2 if align == "center" else 0
                    fragments.extend(replace(f, x=f.x + col * column_width + padding + shift) for f in content)
                add(Row(y, height + leading + padding,
                        min((f.start for f in fragments), default=block.start),
                        max((f.end for f in fragments), default=block.end), tuple(fragments),
                        kind="table_header" if block.header else "table",
                        cell_edges=tuple(col * column_width for col in range(count + 1)),
                        table_top=index == 0, table_end=index == row_count - 1))
            continue
        level = block.level if block.kind == "heading" else 0
        if level and rows:
            y += gap
        indent = gap*2 if card_start is not None else 0
        if block.kind == "quote":
            indent += gap * 2
        elif block.kind == "list":
            prefix_width = body_height * .65 if block.prefix in ("☑", "☐") else fonts(font_key(frozenset())).measure(block.prefix)
            indent = max(gap * 2, prefix_width + gap) + block.level * unit
        for index, (fragments, height) in enumerate(_wrap(block.spans, max(1, width - indent), fonts,
                                                       level=level, max_rows=remaining)):
            moved = tuple(replace(f, x=f.x + indent) for f in fragments)
            add(Row(y, height + leading + (gap * .5 if level else 0),
                    min((f.start for f in moved), default=block.start),
                    max((f.end for f in moved), default=block.end), moved, kind=block.kind,
                    prefix=block.prefix if index == 0 else "",
                    prefix_x=max(0, indent - (prefix_width if block.kind == "list" else 0) - gap)))
    return tuple(rows), y, used_width


def copy_visible(blocks, first=0, last=None):
    """Copy readable glyphs whose original offsets fall within a selection."""
    if last is None:
        last = 2 ** 63
    result = []
    for block in blocks:
        if block.end <= first or block.start >= last:
            continue
        if block.kind == "space":
            result.append("\n")
            continue
        if block.kind == "list" and first <= block.start < last:
            result.append(block.prefix + " ")
        groups = block.cells if block.kind == "table" else (block.spans,)
        selected = ["".join(char for span in spans
                            for char, position in zip(span.text, span.source)
                            if first <= position < last) for spans in groups]
        if block.kind == "table":
            # Outside cells are not part of a partial selection. Preserve the
            # separators between selected cells (including intervening empty
            # cells), while whole-row copying retains every table column.
            if first <= block.start and last >= block.end:
                result.append("\t".join(selected))
            else:
                occupied = [index for index, text in enumerate(selected) if text]
                if occupied:
                    result.append("\t".join(selected[occupied[0]:occupied[-1] + 1]))
        else:
            result.extend(selected)
        if block.kind != "code" and block.end - 1 < last and block.end > first:
            result.append("\n")
    return "".join(result).rstrip("\n")
