"""Safe, source-mapped Markdown subset for the native chat canvas.

No HTML, command execution, image fetch or document mutation. Every displayed
character retains its original Python character offset, including escaped and
formatted text. This is intentionally not a full CommonMark implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Span:
    text: str
    source: range | tuple[int, ...]
    styles: frozenset[str] = frozenset()
    link: str = ""

    @property
    def start(self):
        return self.source[0] if self.source else 0

    @property
    def end(self):
        return self.source[-1] + 1 if self.source else self.start


@dataclass(frozen=True)
class Block:
    kind: str
    start: int
    end: int
    spans: tuple[Span, ...] = ()
    level: int = 0
    prefix: str = ""
    language: str = ""
    code: str = ""
    cells: tuple[tuple[Span, ...], ...] = ()
    align: tuple[str, ...] = ()
    header: bool = False
    card_start: int | None = None


def safe_link(value: str) -> str:
    """Only ordinary user-clicked HTTP(S) URLs; never shell/file/data schemes."""
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) <= 32 for c in value):
        return ""
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            return ""
        if parts.username is not None or parts.password is not None:
            return ""
        return value
    except ValueError:
        return ""


def _plain(text, start, styles=frozenset(), link=""):
    return Span(text, range(start, start + len(text)), styles, link)


def _closing_delimiter(text, begin, delimiter, budget):
    pos = begin
    while pos < len(text) and budget[0] > 0:
        candidate = text.find(delimiter[0], pos)
        if candidate < 0:
            budget[0] -= len(text) - pos
            return -1
        tick = text.find("`", pos, candidate)
        if tick >= 0:
            count = 1
            while tick + count < len(text) and text[tick + count] == "`":
                count += 1
            close_tick = text.find("`" * count, tick + count)
            if close_tick >= 0:
                budget[0] -= close_tick + count - pos
                pos = close_tick + count
                continue
        budget[0] -= max(1, candidate - pos)
        end = candidate
        while end < len(text) and text[end] == delimiter[0]:
            end += 1
        budget[0] -= end - candidate
        slash = candidate - 1
        while slash >= 0 and text[slash] == "\\":
            slash -= 1
        escaped = (candidate - slash - 1) % 2
        if (not escaped and end - candidate >= len(delimiter) and candidate > begin
                and not text[candidate - 1].isspace()):
            # The last marks close the outer span, leaving earlier marks for
            # nested emphasis: **bold *and italic***, *italic **and bold***.
            return end - len(delimiter)
        pos = end
    return -1


def inline(text: str, start: int, styles=frozenset(), link="", depth=0, budget=None) -> tuple[Span, ...]:
    """Bounded inline scanner. Unsupported/incomplete syntax stays literal."""
    if depth >= 8 or len(text) > 65536:
        return (_plain(text, start, styles, link),) if text else ()
    if budget is None:
        budget = [max(1, len(text)) * 8]
    result = []
    plain_start = 0
    i = 0

    def flush(end):
        nonlocal plain_start
        if end > plain_start:
            result.append(_plain(text[plain_start:end], start + plain_start, styles, link))

    while i < len(text):
        if budget[0] <= 0:
            break
        if text[i] == "\\" and i + 1 < len(text) and text[i + 1] in r"\`*_{}[]()#+-.!>|~":
            flush(i)
            result.append(_plain(text[i + 1], start + i + 1, styles, link))
            i += 2
            plain_start = i
            continue
        if text[i] == "`":
            size = 1
            while i + size < len(text) and text[i + size] == "`":
                size += 1
            close = text.find("`" * size, i + size)
            if close >= 0:
                flush(i)
                result.append(_plain(text[i + size:close], start + i + size,
                                     styles | {"code"}, link))
                i = close + size
                plain_start = i
                continue
        # Images remain ordinary alt text with no network request or action.
        if text[i] == "[" or (text[i] == "!" and text[i:i + 2] == "!["):
            image = text[i] == "!"
            label_start = i + (2 if image else 1)
            close = text.find("](", label_start, min(len(text), label_start + 4096))
            if close >= 0:
                url_start = close + 2
                pos, nesting = url_start, 1
                while pos < min(len(text), url_start + 4096):
                    if text[pos] == "\\":
                        pos += 2
                        continue
                    if text[pos] == "(":
                        nesting += 1
                    elif text[pos] == ")":
                        nesting -= 1
                        if nesting == 0:
                            break
                    pos += 1
                if nesting == 0:
                    flush(i)
                    raw_url = text[url_start:pos].strip()
                    if raw_url.startswith("<") and raw_url.endswith(">"):
                        raw_url = raw_url[1:-1]
                    # Optional quoted link title is metadata, never executed.
                    raw_url = re.split(r'\s+["\']', raw_url, maxsplit=1)[0]
                    target = "" if image else safe_link(raw_url)
                    result.extend(inline(text[label_start:close], start + label_start,
                                         styles, target, depth + 1, budget))
                    i = pos + 1
                    plain_start = i
                    continue
        if text[i] == "<":
            close = text.find(">", i + 1, min(len(text), i + 4096))
            if close >= 0 and safe_link(text[i + 1:close]):
                flush(i)
                result.append(_plain(text[i + 1:close], start + i + 1, styles,
                                     safe_link(text[i + 1:close])))
                i = close + 1
                plain_start = i
                continue
        delimiter = next((mark for mark in ("***", "___", "**", "__", "~~", "==", "*", "_")
                          if text.startswith(mark, i)), "")
        # Intraword underscores are not emphasis (variable_name, URL paths).
        if delimiter and not (delimiter[0] == "_" and i and text[i - 1].isalnum()):
            close = _closing_delimiter(text, i + len(delimiter), delimiter, budget)
            if close > i + len(delimiter) and not text[i + len(delimiter)].isspace():
                if not text[close - 1].isspace():
                    flush(i)
                    extra = {"highlight"} if delimiter == "==" else {"strike"} if delimiter == "~~" else (
                        {"bold", "italic"} if len(delimiter) == 3 else
                        {"bold"} if len(delimiter) == 2 else {"italic"})
                    result.extend(inline(text[i + len(delimiter):close],
                                         start + i + len(delimiter), styles | extra, link, depth + 1, budget))
                    i = close + len(delimiter)
                    plain_start = i
                    continue
        i += 1
    flush(len(text))
    return tuple(result)


def _cells(line: str, offset: int):
    """Split unescaped pipes outside backticks, keeping exact cell offsets."""
    pieces = []
    begin, quoted, i = 0, 0, 0
    while i < len(line):
        if line[i] == "\\":
            i += 2
            continue
        if line[i] == "`":
            count = 1
            while i + count < len(line) and line[i + count] == "`":
                count += 1
            quoted = 0 if quoted == count else count if not quoted else quoted
            i += count
            continue
        if line[i] == "|" and not quoted:
            pieces.append((line[begin:i], offset + begin))
            begin = i + 1
        i += 1
    pieces.append((line[begin:], offset + begin))
    if pieces and not pieces[0][0].strip():
        pieces.pop(0)
    if pieces and not pieces[-1][0].strip():
        pieces.pop()
    return tuple((value.strip(), position + len(value) - len(value.lstrip()))
                 for value, position in pieces)


def parse_markdown(source: str) -> tuple[Block, ...]:
    lines = []
    offset = 0
    for item in source.splitlines(keepends=True):
        content = item.rstrip("\r\n")
        lines.append((content, offset, offset + len(item)))
        offset += len(item)
    if not lines or source.endswith(("\n", "\r")):
        lines.append(("", len(source), len(source)))
    blocks = []
    i = 0
    # Bounded inline interpretation; a very large tail remains selectable raw
    # prose instead of being silently truncated or allocating an HTML document.
    while i < len(lines):
        line, start, end = lines[i]
        if start > 524288:
            blocks.extend(Block("paragraph", a, b, (_plain(s, a),)) for s, a, b in lines[i:])
            break
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            mark = fence.group(1)
            language = fence.group(2).strip().split(" ", 1)[0][:40]
            code_start = end
            j = i + 1
            while j < len(lines):
                candidate = lines[j][0]
                if re.fullmatch(r" {0,3}" + re.escape(mark[0]) + "{" + str(len(mark)) + r",}\s*", candidate):
                    break
                j += 1
            code_end = lines[j][1] if j < len(lines) else len(source)
            code = source[code_start:code_end]
            spans = (_plain(code, code_start, frozenset({"code"})),)
            blocks.append(Block("code", start, lines[j][2] if j < len(lines) else len(source),
                                spans, language=language, code=code))
            i = j + 1
            continue
        if not line.strip():
            blocks.append(Block("space", start, end))
            i += 1
            continue
        if re.fullmatch(r" {0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})", line):
            blocks.append(Block("rule", start, end))
            i += 1
            continue
        # A table must have a real delimiter row; ordinary pipes stay prose.
        if i + 1 < len(lines) and "|" in line:
            head = _cells(line, start)
            separator = _cells(lines[i + 1][0], lines[i + 1][1])
            if (1 < len(head) <= 12 and len(separator) == len(head)
                    and all(re.fullmatch(r":?-{3,}:?", cell) for cell, _ in separator)):
                alignment = tuple("center" if s.startswith(":") and s.endswith(":") else
                                  "right" if s.endswith(":") else "left" for s, _ in separator)
                j = i
                while j < len(lines):
                    if j == i + 1:
                        j += 1
                        continue
                    cells = _cells(lines[j][0], lines[j][1])
                    if len(cells) != len(head) or "|" not in lines[j][0]:
                        break
                    blocks.append(Block("table", lines[j][1], lines[j][2],
                                        cells=tuple(inline(s, a, frozenset({"bold"}) if j == i else frozenset())
                                                    for s, a in cells), align=alignment, header=j == i))
                    j += 1
                i = j
                continue
        heading = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            blocks.append(Block("heading", start, end, inline(heading.group(2), start + heading.start(2)),
                                level=len(heading.group(1))))
        elif i + 1 < len(lines) and re.fullmatch(r" {0,3}(?:=+|-+)\s*", lines[i + 1][0]):
            blocks.append(Block("heading", start, lines[i + 1][2], inline(line, start),
                                level=1 if lines[i + 1][0].strip().startswith("=") else 2))
            i += 1
        else:
            quote = re.match(r"^ {0,3}(>\s*)+", line)
            listing = re.match(r"^( {0,12})([-+*]|\d{1,9}[.)])\s+", line)
            if quote:
                blocks.append(Block("quote", start, end, inline(line[quote.end():], start + quote.end())))
            elif listing:
                content = line[listing.end():]
                content_start = start + listing.end()
                prefix = "•" if listing.group(2) in "-+*" else listing.group(2)
                task = re.match(r"\[([ xX])\]\s+", content)
                if task:
                    prefix = "☑" if task.group(1).lower() == "x" else "☐"
                    content_start += task.end()
                    content = content[task.end():]
                blocks.append(Block("list", start, end, inline(content, content_start),
                                    prefix=prefix, level=min(4, len(listing.group(1)) // 2)))
            else:
                blocks.append(Block("paragraph", start, end, inline(line, start)))
        i += 1
    return tuple(blocks)
