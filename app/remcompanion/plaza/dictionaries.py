"""Offline dictionaries: original local definitions, with no model or network.

MDX files live in data/plaza/dictionaries. Only a reusable headword/block index
is cached; definitions are read on demand from the original local MDX. MDD
images/audio and HTML scripts/resources are deliberately not loaded.
"""

from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
from threading import RLock
from urllib.parse import unquote

from ._mdx import MAX_RECORD, MdxError, MdxIndex, normalize_key
from .dictionary_entries import structure_entry


LEGACY_FILENAMES = ("柯林斯高阶英汉词典.mdx", "Shorter Oxford English Dictionary 6th.mdx")
MAX_IMPORT_BYTES = 512 * 1024 * 1024


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0)
                                                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except FileNotFoundError:
        return False


def _local_path(path: Path | str) -> Path:
    path = Path(path).expanduser()
    if str(path).startswith(("\\\\", "//")):
        raise ValueError("只支持本机词典文件，不读取网络共享")
    return path


def _check_directory(path: Path):
    """Reject junction/symlink ancestors before creating private local indexes."""
    for candidate in (path, *path.parents):
        if _is_reparse(candidate):
            raise ValueError("词典目录不能是符号链接或重解析点")


def _digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


class _PlainEntry(HTMLParser):
    """Read text only; never hand untrusted dictionary HTML to a renderer."""

    BLOCKS = {"p", "div", "li", "ul", "ol", "dl", "dt", "dd", "tr", "h1", "h2", "h3", "h4",
              "blockquote", "section", "fieldset", "legend", "d", "dv", "qq"}
    IGNORED = {"script", "style", "head", "iframe", "object", "embed", "svg", "audio", "video"}
    EXAMPLE_CLASSES = {"example", "examples", "ex", "quote", "quotation", "cit", "sentence"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.meanings: list[str] = []
        self.examples: list[str] = []
        self._stack: list[tuple[str, bool, bool, bool]] = []
        self._ignored = 0
        self._example = 0

    def _append(self, text: str):
        if not self._ignored:
            self.parts.append(text)
            if self._example:
                self.examples.append(text)
            else:
                self.meanings.append(text)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attributes = {key: value or "" for key, value in attrs}
        original_classes = set(attributes.get("class", "").lower().split())
        classes = set(re.split(r"[\s_-]+", attributes.get("class", "").lower()))
        # Confirmed from the actual local files, not guessed from colored text:
        # Collins uses <li> inside .collins_en_cn for bilingual examples; SOED
        # wraps source-attributed quotations in <qq><au>...<qt>...</qt></qq>.
        collins = "collins_en_cn" in original_classes
        example = (tag in {"q", "blockquote", "qq"} or bool(classes & self.EXAMPLE_CLASSES)
                   or tag == "li" and any(entry[3] for entry in self._stack))
        ignored = tag in self.IGNORED or "hidden" in attributes or attributes.get("aria-hidden") == "true"
        # CSS is never evaluated, but literal display:none/visibility:hidden is
        # enough to suppress obvious hidden helper text in these dictionaries.
        style = re.sub(r"\s+", "", attributes.get("style", "").lower())
        ignored = ignored or "display:none" in style or "visibility:hidden" in style
        if tag in self.BLOCKS or tag in {"br", "hr"}:
            self._append("\n")
        if tag in {"td", "th"}:
            self._append(" ")
        if tag in {"br", "hr", "img", "input", "meta", "link", "embed", "source", "wbr"}:
            return
        if len(self._stack) >= 512:
            raise MdxError("词典 HTML 嵌套超过安全上限")
        self._stack.append((tag, ignored, example, collins))
        self._ignored += int(ignored)
        self._example += int(example)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                for _, ignored, example, _ in self._stack[index:]:
                    self._ignored -= int(ignored)
                    self._example -= int(example)
                del self._stack[index:]
                break
        if tag in self.BLOCKS:
            self._append("\n")

    def handle_data(self, data):
        self._append(data)

    @staticmethod
    def clean(parts: list[str]) -> str:
        text = "".join(parts).replace("\xa0", " ")
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
        lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n")]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _plain_entry(raw: str, stylesheet: str = "") -> tuple[str, str, str]:
    if stylesheet:
        styles = stylesheet.splitlines()
        pairs = {styles[i]: (styles[i + 1], styles[i + 2]) for i in range(0, len(styles) - 2, 3)}
        pieces = re.split(r"(`\d+`)", raw)
        expanded = [pieces[0]]
        expanded_length = len(pieces[0])
        for i in range(1, len(pieces) - 1, 2):
            begin, end = pairs.get(pieces[i][1:-1], ("", ""))
            expanded_length += len(begin) + len(pieces[i + 1]) + len(end)
            if expanded_length > MAX_RECORD:
                raise MdxError("词典样式展开超过安全上限")
            expanded.extend((begin, pieces[i + 1], end))
        raw = "".join(expanded)
    parser = _PlainEntry()
    parser.feed(raw)
    parser.close()
    return parser.clean(parser.parts), parser.clean(parser.meanings), parser.clean(parser.examples)


class LocalDictionaries:
    """Thread-safe local library. First lookup builds a key-only SQLite index.

    Call lookup/import_file in a UI worker; construction/status do no record
    decompression. A failed source is reported in status without hiding results
    from a healthy dictionary. Closing is safe after the worker is finished.
    """

    def __init__(self, project_root: Path, *, cache_dir: Path | None = None):
        self.root = _local_path(project_root).resolve()
        _local_path(self.root)
        self.directory = self.root / "data" / "plaza" / "dictionaries"
        self.cache_dir = _local_path(cache_dir) if cache_dir is not None else self.directory / ".index"
        self._lock = RLock()
        self._readers: dict[Path, MdxIndex] = {}
        self._errors: dict[Path, str] = {}
        self._paths: list[Path] = []
        self.refresh()

    def refresh(self):
        with self._lock:
            paths = []
            if self.directory.is_dir() and not _is_reparse(self.directory):
                paths.extend(sorted((p for p in self.directory.iterdir() if p.suffix.lower() == ".mdx"
                                     and p.is_file() and not _is_reparse(p)), key=lambda p: p.name.casefold()))
            # The two old installation-root files are a migration fallback,
            # never recursive discovery of arbitrary directories or MDD files.
            known_names = {p.name.casefold() for p in paths}
            paths.extend(self.root / name for name in LEGACY_FILENAMES
                         if name.casefold() not in known_names and (self.root / name).is_file()
                         and not _is_reparse(self.root / name))
            self._paths = list(dict.fromkeys(p.resolve() for p in paths))
            for path in list(self._readers):
                if path not in self._paths:
                    self._readers.pop(path).close()
            for path in list(self._errors):
                if path not in self._paths:
                    del self._errors[path]

    @property
    def status(self) -> list[dict]:
        with self._lock:
            result = []
            for path in self._paths:
                reader = self._readers.get(path)
                result.append({"source": path.stem, "path": str(path), "available": path.is_file(),
                               "indexed": reader is not None, "entries": reader.entries if reader else 0,
                               "error": self._errors.get(path, "")})
            return result

    def _reader(self, path: Path) -> MdxIndex:
        if path not in self._readers:
            _check_directory(self.cache_dir)
            self._readers[path] = MdxIndex(path, self.cache_dir)
        return self._readers[path]

    @staticmethod
    def _resolve(reader: MdxIndex, key: str, raw: str) -> tuple[str, str]:
        seen = {normalize_key(key)}
        for _ in range(8):
            stripped = raw.strip("\x00 \t\r\n")
            if not stripped.startswith("@@@LINK="):
                return key, raw
            target = unquote(stripped[len("@@@LINK="):].splitlines()[0].strip())
            normalized = normalize_key(target)
            if not target or len(target) > 512 or normalized in seen:
                raise MdxError("词典链接条目无效或形成循环")
            seen.add(normalized)
            records = reader.lookup(target)
            if not records:
                raise MdxError("词典链接指向的条目不存在")
            key, raw = records[0]
        raise MdxError("词典条目链接超过 8 层安全上限")

    def lookup(self, word: str) -> list[dict]:
        word = str(word).strip()
        if not word or len(word) > 512 or any(ord(c) < 32 for c in word):
            return []
        with self._lock:
            self.refresh()
            result = []
            for path in self._paths:
                try:
                    reader = self._reader(path)
                    entries = reader.lookup(word)
                    seen = set()
                    for key, raw in entries:
                        key, raw = self._resolve(reader, key, raw)
                        text, meaning, example = _plain_entry(raw, reader.styles)
                        if text and text not in seen:
                            seen.add(text)
                            entry = {"source": path.stem, "word": key, "meaning": meaning,
                                     "example": example, "text": text}
                            try:
                                entry.update(structure_entry(raw))
                            except (ValueError, RecursionError):
                                # Unknown/over-complex structure must not hide
                                # the already validated safe original text.
                                entry.update(phonetics=[], parts_of_speech=[], senses=[], structured=False)
                            result.append(entry)
                    self._errors.pop(path, None)
                except (OSError, ValueError, sqlite3.Error) as exc:
                    self._errors[path] = str(exc)
                    reader = self._readers.pop(path, None)
                    if reader:
                        reader.close()
            return result

    def import_file(self, path: Path | str) -> Path:
        """Validate and atomically copy one selected MDX into the local library.

        Existing different files are never replaced. Identical files are an
        idempotent import. Copying is local only; no dictionary is packaged,
        uploaded, or expanded into a text/JSON dump.
        """
        source = _local_path(path).resolve(strict=True)
        _local_path(source)
        if source.suffix.lower() != ".mdx" or not source.is_file() or not 0 < source.stat().st_size <= MAX_IMPORT_BYTES:
            raise ValueError("请选择不超过 512 MB 的本地 .mdx 词典文件")
        with self._lock:
            _check_directory(self.directory)
            _check_directory(self.cache_dir)
            self.directory.mkdir(parents=True, exist_ok=True)
            destination = self.directory / source.name
            if _is_reparse(destination):
                raise ValueError("目标词典不能是符号链接或重解析点")
            # Validate all key/block descriptors in a disposable index. It does
            # not read all records and is removed immediately after validation.
            source_info = source.stat()
            signature = (source_info.st_size, source_info.st_mtime_ns)
            with tempfile.TemporaryDirectory(dir=self.directory, prefix=".validate-") as validation:
                reader = MdxIndex(source, Path(validation))
                reader.close()
            source_digest = _digest(source)
            if signature != (source.stat().st_size, source.stat().st_mtime_ns):
                raise ValueError("验证期间源词典发生变化，导入已取消")
            if destination.exists():
                if source == destination.resolve() or (source.stat().st_size == destination.stat().st_size
                                                       and source_digest == _digest(destination)):
                    self.refresh()
                    return destination
                raise ValueError("同名词典已存在且内容不同；请先为新词典改名，原文件未覆盖")
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.directory, prefix=".import-", suffix=".tmp", delete=False) as stream:
                    temporary = Path(stream.name)
                    with source.open("rb") as incoming:
                        shutil.copyfileobj(incoming, stream, length=1024 * 1024)
                    stream.flush()
                    os.fsync(stream.fileno())
                if (signature != (source.stat().st_size, source.stat().st_mtime_ns)
                        or temporary.stat().st_size != signature[0] or _digest(temporary) != source_digest):
                    raise ValueError("复制期间源词典发生变化，导入已取消")
                # Windows rename refuses an existing destination, even on
                # filesystems without hard links. POSIX uses atomic link/create
                # to preserve the same no-overwrite guarantee.
                if os.name == "nt":
                    os.rename(temporary, destination)
                else:
                    os.link(temporary, destination)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
            self.refresh()
            return destination

    def close(self):
        with self._lock:
            for reader in self._readers.values():
                reader.close()
            self._readers.clear()
