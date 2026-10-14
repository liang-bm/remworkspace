"""Source-preserving display plan for one live journal writing surface.

No HTML, network images, executable code or transformed replacement text.
The native Text retains every Unicode scalar and its original undo history.
"""
from dataclasses import dataclass
from bisect import bisect_right
import re
from .markdown_document import parse_markdown
from .journal_syntax import syntax_ranges

PREFIX = '_live:'

@dataclass(frozen=True)
class CodeRegion:
    start: int
    end: int
    code_start: int
    code_end: int
    language: str
    closing: int | None = None
    literal: bool = False

@dataclass(frozen=True)
class Chunk:
    start: int
    end: int
    key: tuple
    styles: tuple
    hidden: tuple

@dataclass(frozen=True)
class DisplayPlan:
    source: str
    lines: tuple
    chunks: tuple
    codes: tuple
    bullets: tuple
    links: tuple

    def index(self, offset):
        offset = max(0, min(len(self.source), offset))
        line = max(0, bisect_right(self.lines, offset) - 1)
        # +Nc traverses Unicode scalars even on Tk 8.6's UTF-16 columns.
        return f'{line + 1}.0+{offset - self.lines[line]}c'

def _gaps(begin, end, spans):
    position = begin
    for a, b in sorted(spans):
        if a > position: yield position, min(a, end)
        position = max(position, b)
    if position < end: yield position, end

def display_plan(source, literal_codes=(), cancelled=lambda: False):
    lines = (0, *(match.end() for match in re.finditer('\n', source)))
    chunks, codes, bullets, links = [], [], [], []
    for block in parse_markdown(source):
        if cancelled(): return None
        styles, hidden = [], []
        raw = source[block.start:block.end]
        literal = any(a < block.end and b > block.start for a, b in literal_codes)
        if literal:
            for a,b in literal_codes:
                if a>=block.end or b<=block.start:continue
                styles.append((PREFIX+'code',max(a,block.start),min(b,block.end)))
                if block.start<=a<block.end:
                    first=source.find('\n',a,b)
                    styles.append((PREFIX+'literal_head',a,min(b,first+1 if first>=0 else b)))
                if block.start<b<=block.end:
                    last=source.rfind('\n',a,max(a,b-1))+1
                    styles.append((PREFIX+'literal_foot',max(a,last),b))
        if block.kind == 'code' and not literal:
            newline = source.find('\n', block.start, block.end)
            begin = newline + 1 if newline >= 0 else block.end
            finish = begin + len(block.code)
            closing = finish if finish < block.end else None
            codes.append(CodeRegion(block.start, block.end, begin, finish, block.language, closing))
            styles.append((PREFIX + 'code', block.start, block.end))
            styles.append((PREFIX + 'head', block.start, begin))
            hidden.append((block.start, max(block.start, begin - (1 if newline >= 0 else 0))))
            if closing is not None:
                styles.append((PREFIX + 'foot', closing, block.end))
                hidden.append((closing, block.end - (1 if raw.endswith('\n') else 0)))
            for category, a, b in syntax_ranges(block.code, block.language):
                styles.append((PREFIX + 'syntax:' + category, begin + a, begin + b))
        elif not literal:
            spans = block.spans
            if block.kind == 'table':
                spans = tuple(span for cell in block.cells for span in cell)
                if block.header: styles.append((PREFIX + 'bold', block.start, block.end))
            visible = []
            for span in spans:
                if not span.source: continue
                visible.extend((value, value + 1) for value in span.source) if not isinstance(span.source, range) else visible.append((span.start, span.end))
                for tag in span.styles:
                    name = 'inlinecode' if tag == 'code' else tag
                    styles.append((PREFIX + name, span.start, span.end))
                if {'bold', 'italic'} <= span.styles: styles.append((PREFIX + 'bolditalic', span.start, span.end))
                if span.link:
                    styles.append((PREFIX + 'link', span.start, span.end))
                    links.append((span.start, span.end, span.link))
            content_end = block.end - (1 if raw.endswith('\n') else 0)
            if block.kind not in ('space', 'rule', 'table'):
                hidden.extend(_gaps(block.start, content_end, visible))
            if block.kind == 'heading': styles.append((PREFIX + 'h' + str(block.level), block.start, block.end))
            elif block.kind == 'quote': styles.append((PREFIX + 'quote', block.start, block.end))
            elif block.kind == 'list':
                styles.append((PREFIX + 'bullet', block.start, block.end))
                if block.prefix in ('•', '☐', '☑'):
                    content = spans[0].start if spans and spans[0].source else content_end
                    bullets.append((block.start, content, block.prefix))
                else:
                    # Keep ordered numbering in the same editable line.
                    mark = re.match(r'\s*\d+[.)]\s+', raw)
                    if mark: hidden = [(a, b) for a, b in hidden if a >= block.start + mark.end()]
            elif block.kind == 'rule': styles.append((PREFIX + 'rule', block.start, block.end))
        relative = tuple((t, a - block.start, b - block.start) for t, a, b in styles)
        chunks.append(Chunk(block.start, block.end, (block.kind, raw, relative, literal), tuple(styles), tuple(hidden)))
    for a, b in literal_codes:
        codes.append(CodeRegion(a, b, a, b, 'text', literal=True))
    return DisplayPlan(source, lines, tuple(chunks), tuple(codes), tuple(bullets), tuple(links))
