"""Verified book/film/TV originals for optional AI-memory-aid context.

These are classical literary usages, not a claim about present-day idiom.
No generated wording or guessed attribution enters this catalogue. The first
load verifies the original file hash and indexes short, complete sentences;
call ``warm_classic_examples`` from a worker before opening the card page.
Ordinary word-card examples remain dictionary-provided: this catalogue never
supplies an AI-written example or a guessed screen quotation. Screen resources
are brief curated excerpts, not bundled copyrighted scripts.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import re
from threading import Lock
from urllib.parse import urlparse


ASSET_DIR = Path(__file__).resolve().parents[1] / "assets" / "word-examples"
_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")
_QUERY = re.compile(r"[A-Za-z]+(?:['’ -][A-Za-z]+)*\Z")
_END = re.compile(r"[.!?]+[\u201d\u2019\"']*(?=\s+[\u201c\u2018\"'A-Z0-9]|\Z)")
_CHAPTER = re.compile(r"^(?:CHAPTER|Chapter)\s+([IVXLCDM]+|\d+)\b")
_ABBREVIATIONS = frozenset({"mr", "mrs", "ms", "dr", "st", "prof", "rev", "capt", "col", "jr", "sr", "esq", "vs", "messrs"})
_FIRST_STORY = {
    1342: ("It is a truth universally acknowledged", "Chapter I"),
    11: ("Alice was beginning to get very tired", "Chapter I"),
    2701: ("Call me Ishmael.", "Chapter 1"),
}
_LOAD_LOCK = Lock()
_SCREEN_HOSTS = frozenset({"downloads.bbc.co.uk", "www.sonyclassics.com", "shop.a24films.com"})


@dataclass(frozen=True)
class ClassicExample:
    text: str
    book: str
    author: str
    locator: str
    source_url: str
    source_kind: str = "book"
    speaker: str = ""
    attribution: str = ""


def _sentences(paragraph: str):
    """Split at real sentence stops, not Mr./Mrs. or personal initials."""
    start = 0
    for stop in _END.finditer(paragraph):
        before = paragraph[:stop.start()]
        last_word = re.search(r"([A-Za-z]+)\Z", before)
        if stop.group().startswith(".") and last_word is not None:
            token = last_word.group(1)
            if token.casefold() in _ABBREVIATIONS or (len(token) == 1 and token != "I"):
                continue
        sentence = paragraph[start:stop.end()].strip()
        start = stop.end()
        # Sentence extraction may cut at a stop inside continuing dialogue.
        # Drop a lone edge quotation mark only: the displayed result remains
        # an exact substring of its original paragraph. Reject complex broken
        # quotations and visible footnote/italics/editorial markup instead.
        opening, closing = sentence.count("“"), sentence.count("”")
        if opening == 1 and closing == 0 and sentence.startswith("“"):
            sentence = sentence[1:]
        elif opening == 0 and closing == 1 and sentence.endswith("”"):
            sentence = sentence[:-1]
        elif opening != closing:
            continue
        if any(mark in sentence for mark in ("*", "_", "[", "]")):
            continue
        if re.match(r'^[A-Z“‘\"]', sentence) is None:
            continue
        words = _WORD.findall(sentence)
        # Do not crop a long sentence into a falsely complete quotation.
        if 3 <= len(words) <= 36 and 8 <= len(sentence) <= 240:
            yield sentence, tuple(word.casefold() for word in words)


def _book_examples(raw: bytes, entry: dict):
    text = raw.decode("utf-8-sig")
    lines = text.splitlines()
    marker = next((index for index, line in enumerate(lines)
                   if line.startswith("*** START OF THE PROJECT GUTENBERG EBOOK")), None)
    end = next((index for index, line in enumerate(lines)
                if line.startswith("*** END OF THE PROJECT GUTENBERG EBOOK")), None)
    signature, chapter = _FIRST_STORY.get(entry.get("gutenberg_id"), ("", ""))
    if marker is None or end is None or not signature:
        return
    start = next((index for index in range(marker + 1, end)
                  if signature in lines[index]), None)
    if start is None:
        return
    paragraph_lines, paragraph_start, paragraph_number = [], start, 0

    def finish(last_line):
        paragraph = re.sub(r"\s+", " ", " ".join(paragraph_lines)).strip()
        locator = f"{chapter} · paragraph {paragraph_number} · original lines {paragraph_start + 1}–{last_line + 1}"
        for sentence, words in _sentences(paragraph):
            yield ClassicExample(sentence, entry["title"], entry["author"], locator, entry["source_url"]), words

    for index in range(start, end):
        line = lines[index]
        match = _CHAPTER.match(line.strip())
        if match:
            chapter = "Chapter " + match.group(1)
        if line.strip():
            if not paragraph_lines:
                paragraph_start = index
                paragraph_number += 1
            paragraph_lines.append(line)
        elif paragraph_lines:
            yield from finish(index - 1)
            paragraph_lines.clear()
    if paragraph_lines:
        yield from finish(end - 1)


@lru_cache(maxsize=1)
def _book_catalogue():
    """Integrity-checked read-only data; absent/damaged resources yield no quote."""
    try:
        manifest = json.loads((ASSET_DIR / "manifest.json").read_text(encoding="utf-8"))
        entries = manifest["books"]
        if manifest.get("version") != 1 or not isinstance(entries, list):
            return (), {}, 0
    except (OSError, ValueError, TypeError, KeyError):
        return (), {}, 0
    examples, index, sources = [], {}, 0
    seen = set()
    for entry in entries:
        try:
            if not isinstance(entry, dict) or entry.get("gutenberg_id") not in _FIRST_STORY:
                continue
            path = ASSET_DIR / entry["file"]
            if not path.resolve().is_relative_to(ASSET_DIR.resolve()):
                continue
            raw = path.read_bytes()
            if sha256(raw).hexdigest() != entry["sha256"]:
                continue
            book_examples = tuple(_book_examples(raw, entry))
            if not book_examples:
                continue
            for example, words in book_examples:
                if example.text in seen:
                    continue
                seen.add(example.text)
                identity = len(examples)
                examples.append(example)
                for word in set(words):
                    index.setdefault(word, []).append(identity)
            sources += 1
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            # One invalid book must not erase verified examples from the others.
            continue
    return tuple(examples), index, sources


def _screen_examples():
    """Load only verified, short official-source excerpts; fail closed by source.

    Public availability of a script is not a redistribution licence. Enforce a
    25-word combined excerpt ceiling even if two JSON entries share a URL, and
    keep the full original script out of the bundled assets.
    """
    try:
        data = json.loads((ASSET_DIR / "screen-quotes.json").read_text(encoding="utf-8"))
        sources = data["sources"]
        if data.get("version") != 1 or not isinstance(sources, list):
            return ()
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        return ()
    budgets = {}
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("quotes"), list):
            continue
        source_url = source.get("source_url")
        if not isinstance(source_url, str):
            continue
        budgets[source_url] = budgets.get(source_url, 0) + sum(
            len(_WORD.findall(item.get("text", "")))
            for item in source["quotes"] if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    examples = []
    for source in sources:
        try:
            kind, url = source["source_kind"], source["source_url"]
            parsed = urlparse(url)
            if (kind not in {"film", "tv"} or parsed.scheme != "https"
                    or parsed.hostname not in _SCREEN_HOSTS
                    or parsed.username is not None or parsed.password is not None
                    or not 1 <= budgets[url] <= 25
                    or not source["evidence_stage"].startswith("official_")):
                continue
            title, author = source["title"], source["author"]
            if not all(isinstance(value, str) and value.strip() for value in (title, author)):
                continue
            prepared = []
            for item in source["quotes"]:
                text, locator = item["text"], item["locator"]
                speaker = item.get("speaker", "")
                if (not all(isinstance(value, str) for value in (text, locator, speaker))
                        or not locator.strip() or not 2 <= len(_WORD.findall(text)) <= 25
                        or not 3 <= len(text) <= 240 or text[-1] not in ".!?"
                        or text != text.strip() or any(mark in text for mark in ("*", "_", "[", "]", "\n", "\r"))):
                    raise ValueError("Invalid curated quotation")
                attribution = f"{title} · {speaker or author}"
                prepared.append(ClassicExample(text, title, author, locator, url, kind, speaker, attribution))
            examples.extend(prepared)
        except (ValueError, TypeError, KeyError, AttributeError):
            # One invalid source must not erase books or other verified sources.
            continue
    return tuple(examples)


@lru_cache(maxsize=1)
def _catalogue():
    books, _book_index, book_count = _book_catalogue()
    examples, index, seen = [], {}, set()
    for example in (*_screen_examples(), *books):
        if example.text in seen:
            continue
        seen.add(example.text)
        identity = len(examples)
        examples.append(example)
        for word in set(_WORD.findall(example.text)):
            index.setdefault(word.casefold().replace("’", "'"), []).append(identity)
    return tuple(examples), index, book_count


def warm_classic_examples() -> int:
    """Preload all sources; return verified book count for API compatibility."""
    with _LOAD_LOCK:
        return _catalogue()[2]


def warm_example_source_counts() -> dict[str, int]:
    """Return usable distinct-source counts without exposing quoted text."""
    with _LOAD_LOCK:
        examples, _index, _book_count = _catalogue()
    sources = {kind: set() for kind in ("book", "film", "tv")}
    for example in examples:
        sources[example.source_kind].add(example.source_url)
    return {kind: len(items) for kind, items in sources.items()}


@lru_cache(maxsize=512)
def _lookup(word: str, limit: int) -> tuple[ClassicExample, ...]:
    with _LOAD_LOCK:
        examples, index, _sources = _catalogue()
    tokens = tuple(token.casefold().replace("’", "'") for token in _WORD.findall(word))
    if not tokens:
        return ()
    candidates = index.get(tokens[0], ())
    # A hyphenated compound or possessive is not an exact uninflected target.
    # In particular, "house" must not choose "wood-house" as the short hit.
    pattern = re.compile(r"(?<![A-Za-z0-9_'’-])" + re.escape(word).replace(r"\ ", r"\s+") + r"(?![A-Za-z0-9_'’-])", re.IGNORECASE)
    hits = [examples[identity] for identity in candidates
            if pattern.search(examples[identity].text.replace("’", "'"))]
    # Concise modern screen dialogue first, then concise literary originals.
    # Take one actual hit per kind before filling remaining slots. A missing
    # kind is never invented, and diversity never changes the original wording.
    hits.sort(key=lambda item: (item.source_kind == "book", len(_WORD.findall(item.text)), len(item.text)))
    selected, seen_kinds = [], set()
    for item in hits:
        if item.source_kind not in seen_kinds:
            selected.append(item)
            seen_kinds.add(item.source_kind)
            if len(selected) == limit:
                return tuple(selected)
    for item in hits:
        if item not in selected:
            selected.append(item)
            if len(selected) == limit:
                break
    return tuple(selected)


def classic_examples(word: str, limit: int = 3) -> tuple[ClassicExample, ...]:
    """Find whole-word/phrase original sentences, never inflected substrings.

    Zero matches returns an empty tuple. This function performs no network,
    model call, file modification, translation or invented attribution.
    """
    if not isinstance(word, str):
        return ()
    word = " ".join(word.strip().split()).casefold().replace("’", "'")
    if len(word) > 80 or _QUERY.fullmatch(word) is None:
        return ()
    try:
        count = min(3, max(0, int(limit)))
    except (TypeError, ValueError, OverflowError):
        return ()
    return _lookup(word, count) if count else ()
