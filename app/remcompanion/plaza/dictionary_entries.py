"""Source-aware structure from local dictionary markup, never generated facts.

The complete safe plain entry remains available even when a dictionary uses
unknown markup. IPA/POS are populated only from explicit fields in the MDX.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re


@dataclass(slots=True)
class _Node:
    tag: str
    attrs: dict
    order: int
    children: list = field(default_factory=list)


class _Document(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {}, 0)
        self.stack = [self.root]
        self.count = 0

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 100000 or len(self.stack) > 512:
            raise ValueError("词典结构超过安全上限")
        node = _Node(tag.lower(), {key: value or "" for key, value in attrs}, self.count)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _hidden(node: _Node) -> bool:
    style = re.sub(r"\s+", "", node.attrs.get("style", "").lower())
    return (node.tag in {"script", "style", "head", "iframe", "object", "embed", "svg", "audio", "video"}
            or "hidden" in node.attrs or node.attrs.get("aria-hidden") == "true"
            or "display:none" in style or "visibility:hidden" in style)


def _walk(node: _Node, parents=()):
    if _hidden(node):
        return
    yield node, parents
    for child in node.children:
        if isinstance(child, _Node):
            yield from _walk(child, (*parents, node))


def _text(node: _Node, *, omit=frozenset()) -> str:
    parts = []

    def visit(current):
        if _hidden(current) or current.tag in omit:
            return
        block = current.tag in {"div", "p", "li", "br", "qq", "qt", "fieldset", "legend", "tr"}
        if block:
            parts.append("\n")
        elif current.tag == "w":
            parts.append(" ")
        for child in current.children:
            if isinstance(child, str):
                parts.append(child)
            else:
                visit(child)
        if block:
            parts.append("\n")
        elif current.tag == "w":
            parts.append(" ")

    visit(node)
    text = "".join(parts).replace("\xa0", " ")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    lines = [re.sub(r"[ \t\r]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def structure_entry(raw: str) -> dict:
    document = _Document()
    document.feed(raw)
    document.close()
    nodes = list(_walk(document.root))
    phonetics = [_text(node) for node, _ in nodes if node.tag == "ph"]
    result = {"phonetics": [phonetics[0]] if phonetics and phonetics[0] else [],
              "parts_of_speech": [], "senses": [], "structured": False}
    collins = [node for node, _ in nodes if "collins_en_cn" in node.attrs.get("class", "").split()]
    for node in collins:
        inside = list(_walk(node))
        captions = [n for n, _ in inside if "caption" in n.attrs.get("class", "").split()]
        labels = [_text(n) for n, _ in inside if "num" in n.attrs.get("class", "").split()]
        poses = [_text(n) for n, _ in inside if "st" in n.attrs.get("class", "").split()]
        examples = [_text(n) for n, _ in inside if n.tag == "li"]
        definition = _text(captions[0], omit=frozenset({"script", "style"})) if captions else ""
        label = labels[0] if labels else ""
        pos = poses[0] if poses else ""
        if pos.lower().startswith("see"):
            # A .st span is also used for cross-reference headers in Collins;
            # it is not part of speech. Full plain text keeps these references.
            continue
        # The caption includes its original numeric/POS prefix; remove only
        # exact leading source strings, not inferred grammar or translations.
        for prefix in (label, pos):
            if prefix and definition.startswith(prefix):
                definition = definition[len(prefix):].strip()
        if definition:
            result["senses"].append({"label": label, "pos": pos, "definition": definition,
                                     "examples": [e for e in examples if e]})
    if not collins:
        poses = [(node.order, _text(node)) for node, parents in nodes if node.tag == "ps"
                 and not any(p.tag in {"ey", "xr", "qq", "db"} for p in parents)]
        for node, parents in nodes:
            if node.tag != "s3" or any(p.tag == "s3" for p in parents):
                continue
            inside = list(_walk(node))
            labels = [_text(n) for n, _ in inside if n.tag == "n"]
            local_pos = [_text(n) for n, ancestry in inside if n.tag == "ps"
                         and not any(p.tag in {"ey", "xr", "qq", "db"} for p in ancestry)]
            prior_pos = [text for order, text in poses if order < node.order]
            pos = local_pos[0] if local_pos else (prior_pos[-1] if prior_pos else "")
            definition = _text(node, omit=frozenset({"qq", "n", "ps"}))
            examples = [_text(n) for n, _ in inside if n.tag == "qq"]
            if definition:
                result["senses"].append({"label": labels[0] if labels else "", "pos": pos,
                                         "definition": definition, "examples": [e for e in examples if e]})
    result["parts_of_speech"] = list(dict.fromkeys(sense["pos"] for sense in result["senses"] if sense["pos"]))
    result["structured"] = bool(result["senses"])
    return result
