"""Opt-in Markdown-example preview with original character coordinates."""
from dataclasses import replace
from .markdown_document import parse_markdown

LANGUAGES=frozenset({'markdown','md','mdown','commonmark','gfm'})

def preview_document(blocks, overrides, enabled):
    result=[]
    for block in blocks:
        if not (enabled and block.kind=='code' and block.language.lower() in LANGUAGES
                and overrides.get(block.start,True)):
            result.append(block)
            continue
        result.append(replace(block,kind='code_preview_header',spans=(),card_start=block.start))
        body_source=block.spans[0].source if block.spans else None
        offset=(body_source.start if isinstance(body_source,range)
                else block.spans[0].start if block.spans else block.start)
        def shifted(span):
            source=span.source
            positions=(range(source.start+offset,source.stop+offset,source.step)
                       if isinstance(source,range) else tuple(p+offset for p in source))
            return replace(span,source=positions)
        for child in parse_markdown(block.code):
            result.append(replace(child,start=child.start+offset,end=child.end+offset,
                                  card_start=block.start,
                                  spans=tuple(shifted(s) for s in child.spans),
                                  cells=tuple(tuple(shifted(s) for s in cell) for cell in child.cells)))
    return tuple(result)
