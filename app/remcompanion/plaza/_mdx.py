"""Bounded, read-only MDX v1/v2 lookup; the cache contains indexes, not records.

Independent implementation of the community file-format description:
https://github.com/zhansliu/writemdict/blob/master/fileformat.md
RIPEMD-128 follows the algorithm's author-supplied specification, not GPL code:
https://homes.esat.kuleuven.be/~bosselae/ripemd/rmd128.txt
"""

from __future__ import annotations

from collections import OrderedDict
import hashlib
from html import unescape
import json
from pathlib import Path
import re
import sqlite3
import struct
import tempfile
from threading import RLock
import unicodedata
import xml.etree.ElementTree as ET
import zlib


class MdxError(ValueError):
    """An invalid, changed, or unsupported local dictionary."""


MAX_BLOCK = 16 * 1024 * 1024
MAX_RECORD = 4 * 1024 * 1024
MAX_ENTRIES = 2_000_000
MAX_KEY_BYTES = 4096
CACHE_BYTES = 16 * 1024 * 1024
INDEX_VERSION = 1


def normalize_key(word: str, strip: bool = False, case_sensitive: bool = False) -> str:
    word = unicodedata.normalize("NFC", word.strip())
    if not case_sensitive:
        word = word.casefold()
    if strip:
        word = "".join(c for c in word if not c.isspace()
                       and not unicodedata.category(c).startswith("P"))
    return word


def _ripemd128(message: bytes) -> bytes:
    """RIPEMD-128; used only to decode the MDX key-index obfuscation."""
    left_order = (
        0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
        7, 4, 13, 1, 10, 6, 15, 3, 12, 0, 9, 5, 2, 14, 11, 8,
        3, 10, 14, 4, 9, 15, 8, 1, 2, 7, 0, 6, 13, 11, 5, 12,
        1, 9, 11, 10, 0, 8, 12, 4, 13, 3, 7, 15, 14, 5, 6, 2,
    )
    right_order = (
        5, 14, 7, 0, 9, 2, 11, 4, 13, 6, 15, 8, 1, 10, 3, 12,
        6, 11, 3, 7, 0, 13, 5, 10, 14, 15, 8, 12, 4, 9, 1, 2,
        15, 5, 1, 3, 7, 14, 6, 9, 11, 8, 12, 2, 10, 0, 4, 13,
        8, 6, 4, 1, 3, 11, 15, 0, 5, 12, 2, 13, 9, 7, 10, 14,
    )
    left_rotate = (
        11, 14, 15, 12, 5, 8, 7, 9, 11, 13, 14, 15, 6, 7, 9, 8,
        7, 6, 8, 13, 11, 9, 7, 15, 7, 12, 15, 9, 11, 7, 13, 12,
        11, 13, 6, 7, 14, 9, 13, 15, 14, 8, 13, 6, 5, 12, 7, 5,
        11, 12, 14, 15, 14, 15, 9, 8, 9, 14, 5, 6, 8, 6, 5, 12,
    )
    right_rotate = (
        8, 9, 9, 11, 13, 15, 15, 5, 7, 7, 8, 11, 14, 14, 12, 6,
        9, 13, 15, 7, 12, 8, 9, 11, 7, 7, 12, 7, 6, 15, 13, 11,
        9, 7, 15, 11, 8, 6, 6, 14, 12, 13, 5, 14, 13, 13, 7, 5,
        15, 5, 8, 11, 14, 14, 6, 14, 6, 9, 12, 9, 12, 5, 15, 8,
    )
    constants = (0, 0x5A827999, 0x6ED9EBA1, 0x8F1BBCDC)
    parallel_constants = (0x50A28BE6, 0x5C4DD124, 0x6D703EF3, 0)

    def function(round_id: int, x: int, y: int, z: int) -> int:
        if round_id == 0:
            return x ^ y ^ z
        if round_id == 1:
            return (x & y) | (~x & z)
        if round_id == 2:
            return (x | ~y) ^ z
        return (x & z) | (y & ~z)

    def step(state, value, shift, constant, round_id):
        a, b, c, d = state
        value = (a + function(round_id, b, c, d) + value + constant) & 0xFFFFFFFF
        return d, ((value << shift) | (value >> (32 - shift))) & 0xFFFFFFFF, b, c

    padded = message + b"\x80" + b"\x00" * ((55 - len(message)) % 64)
    padded += struct.pack("<Q", len(message) * 8)
    digest = (0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476)
    for offset in range(0, len(padded), 64):
        words = struct.unpack_from("<16I", padded, offset)
        left = right = digest
        for i in range(64):
            round_id = i // 16
            left = step(left, words[left_order[i]], left_rotate[i], constants[round_id], round_id)
            right = step(right, words[right_order[i]], right_rotate[i],
                         parallel_constants[round_id], 3 - round_id)
        a, b, c, d = left
        aa, bb, cc, dd = right
        h0, h1, h2, h3 = digest
        digest = tuple(v & 0xFFFFFFFF for v in
                       (h1 + c + dd, h2 + d + aa, h3 + a + bb, h0 + b + cc))
    return struct.pack("<4I", *digest)


def _decode_block(data: bytes, expected: int, encrypted: bool = False) -> bytes:
    if len(data) < 8 or not 0 <= expected <= MAX_BLOCK:
        raise MdxError("词典数据块大小无效或超过安全上限")
    if encrypted:
        key = _ripemd128(data[4:8] + b"\x95\x36\x00\x00")
        payload = bytearray(data[8:])
        previous = 0x36
        for i, value in enumerate(payload):
            payload[i] = ((value >> 4) | (value << 4) & 0xFF) ^ previous ^ (i & 0xFF) ^ key[i % 16]
            previous = value
        data = data[:8] + payload
    compression = struct.unpack_from("<I", data)[0]
    if compression == 0:
        result = data[8:]
    elif compression == 2:
        try:
            decoder = zlib.decompressobj()
            result = decoder.decompress(data[8:], expected + 1)
            if not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
                raise MdxError("词典压缩块不完整或含多余数据")
        except zlib.error as exc:
            raise MdxError("词典压缩块已损坏") from exc
    elif compression == 1:
        raise MdxError("此词典使用 LZO；当前本地读取器只支持 zlib/未压缩 MDX")
    else:
        raise MdxError("未知的 MDX 压缩类型")
    if len(result) != expected or zlib.adler32(result) & 0xFFFFFFFF != struct.unpack_from(">I", data, 4)[0]:
        raise MdxError("词典数据块长度或校验和不匹配")
    return result


class MdxIndex:
    """Lazily decompress just the record blocks intersecting a requested word.

    All public operations are serialized per instance, including SQLite and LRU
    access. An index rebuild is atomic, and never decompresses record blocks.
    """

    def __init__(self, path: Path, cache_dir: Path):
        self.path = path.resolve(strict=True)
        self._lock = RLock()
        self._cache: OrderedDict[int, bytes] = OrderedDict()
        self._cache_bytes = 0
        self._db: sqlite3.Connection | None = None
        stat = self.path.stat()
        self._signature = (stat.st_size, stat.st_mtime_ns)
        self._size = stat.st_size
        with self.path.open("rb") as stream:
            header_len = struct.unpack(">I", self._read(stream, 4))[0]
            if not 2 <= header_len <= 1024 * 1024 or header_len % 2:
                raise MdxError("不是有效的 MDX 词典文件")
            header = self._read(stream, header_len)
            if zlib.adler32(header) & 0xFFFFFFFF != struct.unpack("<I", self._read(stream, 4))[0]:
                raise MdxError("词典文件头校验失败")
            try:
                header_text = header.decode("utf-16-le").rstrip("\x00").lstrip("\ufeff")
                if "<!" in header_text:
                    raise MdxError("词典文件头不允许 XML 实体声明")
                element = ET.fromstring(header_text)
                if element.tag != "Dictionary":
                    raise MdxError("文件不是 MDX 词典")
                self._attrs = element.attrib
                self.version = float(self._attrs["GeneratedByEngineVersion"])
            except (UnicodeError, ET.ParseError, KeyError, ValueError) as exc:
                raise MdxError("无法解析 MDX 文件头") from exc
            if not 1 <= self.version < 3:
                raise MdxError("当前读取器支持 MDX v1/v2，不支持此版本")
            encryption = self._attrs.get("Encrypted", "0")
            try:
                self._encryption = {"No": 0, "Yes": 1}.get(encryption, int(encryption))
            except ValueError:
                self._encryption = {"No": 0, "Yes": 1}.get(encryption, -1)
            if self._encryption not in (0, 2):
                raise MdxError("此 MDX 需要授权密码，当前读取器不尝试绕过")
            encodings = {"utf8": "utf-8", "utf16": "utf-16-le", "utf16le": "utf-16-le",
                         "gbk": "gb18030", "gb2312": "gb18030", "gb18030": "gb18030", "big5": "big5"}
            encoding = self._attrs.get("Encoding", "UTF-8").lower().replace("-", "").replace("_", "")
            if encoding not in encodings:
                raise MdxError("不支持此词典的文字编码")
            self.encoding = encodings[encoding]
            self._width = 8 if self.version >= 2 else 4
            self._unit = 2 if self.encoding == "utf-16-le" else 1
            self._case_sensitive = self._attrs.get("KeyCaseSensitive", "No") == "Yes"
            self._strip = self._attrs.get("StripKey", "No") == "Yes"
            self._key_section = stream.tell()
        # XML attribute normalization would turn literal newlines into spaces,
        # but MDX's compact stylesheet is a newline-delimited triple table.
        stylesheet = re.search(r"\bStyleSheet\s*=\s*([\"'])(.*?)\1", header_text, re.DOTALL)
        self.styles = unescape(stylesheet.group(2)) if stylesheet else ""
        fingerprint = json.dumps([INDEX_VERSION, str(self.path), *self._signature,
                                  hashlib.sha256(header).hexdigest()], ensure_ascii=False)
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = cache_dir / (hashlib.sha256(str(self.path).encode("utf-8")).hexdigest()[:24] + ".sqlite3")
        if not self._open_existing(fingerprint):
            self._build(fingerprint, cache_dir)
            if not self._open_existing(fingerprint):
                raise MdxError("无法打开词典索引")

    def _read(self, stream, amount: int) -> bytes:
        if not 0 <= amount <= MAX_BLOCK or amount > self._size - stream.tell():
            raise MdxError("词典数据越界或超过安全上限")
        data = stream.read(amount)
        if len(data) != amount:
            raise MdxError("词典文件意外截断")
        return data

    def _number(self, data: bytes, offset: int = 0) -> int:
        if offset < 0 or offset + self._width > len(data):
            raise MdxError("词典索引数据不完整")
        return int.from_bytes(data[offset:offset + self._width], "big")

    def _open_existing(self, fingerprint: str) -> bool:
        connection = None
        try:
            if not self.index_path.is_file() or self.index_path.is_symlink():
                return False
            connection = sqlite3.connect(self.index_path.as_uri() + "?mode=ro", uri=True, check_same_thread=False)
            connection.execute("PRAGMA trusted_schema=OFF")
            metadata = dict(connection.execute("SELECT name,value FROM metadata"))
            if metadata.get("fingerprint") != fingerprint or metadata.get("complete") != "yes":
                return False
            self.entries = int(metadata["entries"])
            self.total_records = int(metadata["total_records"])
            if not 0 < self.entries <= MAX_ENTRIES or not 0 < self.total_records <= 4 * 1024**3:
                return False
            if connection.execute("SELECT COUNT(*) FROM keys").fetchone()[0] != self.entries:
                return False
            self._db = connection
            connection = None
            return True
        except (OSError, sqlite3.Error, KeyError, ValueError):
            return False
        finally:
            if connection is not None:
                connection.close()

    def _key_descriptors(self, data: bytes, count: int):
        cursor = 0
        length_width = 2 if self.version >= 2 else 1
        for _ in range(count):
            entries = self._number(data, cursor)
            cursor += self._width
            for _ in range(2):
                if cursor + length_width > len(data):
                    raise MdxError("词典键索引已截断")
                length = int.from_bytes(data[cursor:cursor + length_width], "big")
                cursor += length_width
                amount = length * self._unit
                if amount > MAX_KEY_BYTES or cursor + amount > len(data):
                    raise MdxError("词典键索引文字长度无效")
                data[cursor:cursor + amount].decode(self.encoding)
                cursor += amount
                if self.version >= 2:
                    if data[cursor:cursor + self._unit] != b"\x00" * self._unit:
                        raise MdxError("词典键索引缺少终止符")
                    cursor += self._unit
            compressed = self._number(data, cursor)
            expanded = self._number(data, cursor + self._width)
            cursor += self._width * 2
            if not 0 < entries <= MAX_ENTRIES or not 8 <= compressed <= MAX_BLOCK or not 0 < expanded <= MAX_BLOCK:
                raise MdxError("词典键数据块信息无效")
            yield entries, compressed, expanded
        if cursor != len(data):
            raise MdxError("词典键索引含多余数据")

    def _keys(self, data: bytes):
        cursor = 0
        while cursor < len(data):
            start = self._number(data, cursor)
            cursor += self._width
            end = cursor
            while end + self._unit <= len(data) and data[end:end + self._unit] != b"\x00" * self._unit:
                end += self._unit
                if end - cursor > MAX_KEY_BYTES:
                    raise MdxError("词典条目键过长")
            if end + self._unit > len(data):
                raise MdxError("词典条目键缺少终止符")
            word = data[cursor:end].decode(self.encoding).strip()
            if not word:
                raise MdxError("词典包含空白条目键")
            cursor = end + self._unit
            yield word, start

    def _build(self, fingerprint: str, cache_dir: Path):
        with tempfile.NamedTemporaryFile(dir=cache_dir, prefix=".mdx-index-", suffix=".tmp", delete=False) as temp:
            temporary = Path(temp.name)
        connection = None
        try:
            connection = sqlite3.connect(temporary)
            connection.executescript("""
                CREATE TABLE metadata(name TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE keys(word TEXT NOT NULL, exact TEXT NOT NULL, normalized TEXT NOT NULL,start INTEGER NOT NULL);
                CREATE TABLE blocks(start INTEGER PRIMARY KEY,file_offset INTEGER NOT NULL,compressed INTEGER NOT NULL,expanded INTEGER NOT NULL);
            """)
            with self.path.open("rb") as stream:
                stream.seek(self._key_section)
                preamble = self._read(stream, self._width * (5 if self.version >= 2 else 4))
                values = [self._number(preamble, i) for i in range(0, len(preamble), self._width)]
                if self.version >= 2:
                    count, entries, expanded, compressed, block_size = values
                    if zlib.adler32(preamble) & 0xFFFFFFFF != int.from_bytes(self._read(stream, 4), "big"):
                        raise MdxError("词典键区文件头校验失败")
                else:
                    count, entries, compressed, block_size = values
                    expanded = compressed
                if not 0 < count <= MAX_ENTRIES or not 0 < entries <= MAX_ENTRIES:
                    raise MdxError("词典条目数无效或超过安全上限")
                index = self._read(stream, compressed)
                if self.version >= 2:
                    index = _decode_block(index, expanded, bool(self._encryption & 2))
                key_start = stream.tell()
                if key_start + block_size > self._size:
                    raise MdxError("词典键区超出文件范围")
                parsed = consumed = 0
                previous = -1
                for expected_entries, size, decoded_size in self._key_descriptors(index, count):
                    if consumed + size > block_size:
                        raise MdxError("词典键区数据块越界")
                    block = _decode_block(self._read(stream, size), decoded_size)
                    batch = []
                    block_entries = 0
                    for word, start in self._keys(block):
                        if start < previous or start > 4 * 1024**3:
                            raise MdxError("词典条目偏移无效")
                        previous = start
                        batch.append((word, normalize_key(word, case_sensitive=self._case_sensitive),
                                      normalize_key(word, self._strip, self._case_sensitive), start))
                        block_entries += 1
                        if len(batch) >= 2048:
                            connection.executemany("INSERT INTO keys VALUES (?,?,?,?)", batch)
                            batch.clear()
                    connection.executemany("INSERT INTO keys VALUES (?,?,?,?)", batch)
                    if block_entries != expected_entries:
                        raise MdxError("词典键数据块条目数不匹配")
                    parsed += block_entries
                    consumed += size
                if consumed != block_size or parsed != entries:
                    raise MdxError("词典键区长度或条目数不匹配")
                record_header = self._read(stream, self._width * 4)
                record_count, record_entries, info_size, records_size = [
                    self._number(record_header, i) for i in range(0, len(record_header), self._width)]
                if record_entries != entries or not 0 < record_count <= MAX_ENTRIES or info_size != record_count * self._width * 2:
                    raise MdxError("词典记录区索引无效")
                info = self._read(stream, info_size)
                file_offset = stream.tell()
                if file_offset + records_size != self._size:
                    raise MdxError("词典记录区长度与文件不匹配")
                record_start = record_bytes = 0
                for i in range(record_count):
                    size = self._number(info, i * self._width * 2)
                    expanded_size = self._number(info, (i * 2 + 1) * self._width)
                    if not 8 <= size <= MAX_BLOCK or not 0 < expanded_size <= MAX_BLOCK:
                        raise MdxError("词典记录数据块大小超过安全上限")
                    connection.execute("INSERT INTO blocks VALUES (?,?,?,?)",
                                       (record_start, file_offset + record_bytes, size, expanded_size))
                    record_start += expanded_size
                    record_bytes += size
                    if record_bytes > records_size or record_start > 4 * 1024**3:
                        raise MdxError("词典记录数据块越界")
                if record_bytes != records_size or previous >= record_start:
                    raise MdxError("词典记录区偏移不匹配")
                connection.executescript("""
                    CREATE INDEX keys_exact ON keys(exact);
                    CREATE INDEX keys_normalized ON keys(normalized);
                    CREATE INDEX keys_offset ON keys(start);
                """)
                connection.executemany("INSERT INTO metadata VALUES (?,?)", [
                    ("fingerprint", fingerprint), ("entries", str(entries)),
                    ("total_records", str(record_start)), ("complete", "yes")])
                connection.commit()
            if self._signature != (self.path.stat().st_size, self.path.stat().st_mtime_ns):
                raise MdxError("词典文件在建立索引时被修改；请重新查词")
            connection.close()
            connection = None
            temporary.replace(self.index_path)
        except (UnicodeError, sqlite3.Error) as exc:
            raise MdxError("词典文字编码或本地索引无效") from exc
        finally:
            if connection is not None:
                connection.close()
            temporary.unlink(missing_ok=True)

    def _record(self, start: int) -> str:
        assert self._db is not None
        row = self._db.execute("SELECT MIN(start) FROM keys WHERE start>?", (start,)).fetchone()
        end = row[0] if row[0] is not None else self.total_records
        if not 0 <= start < end <= self.total_records or end - start > MAX_RECORD:
            raise MdxError("词典条目大小无效或超过安全上限")
        blocks = self._db.execute(
            "SELECT start,file_offset,compressed,expanded FROM blocks WHERE start<? AND start+expanded>? ORDER BY start LIMIT 129",
            (end, start)).fetchall()
        if not blocks or len(blocks) > 128:
            raise MdxError("词典条目跨越的数据块数量异常")
        result = bytearray()
        cursor = start
        with self.path.open("rb") as stream:
            for block_start, offset, compressed, expanded in blocks:
                if block_start > cursor or not block_start <= cursor < block_start + expanded:
                    raise MdxError("词典条目数据块不连续")
                block = self._cache.get(block_start)
                if block is None:
                    stream.seek(offset)
                    block = _decode_block(self._read(stream, compressed), expanded)
                    while self._cache and self._cache_bytes + len(block) > CACHE_BYTES:
                        _, old = self._cache.popitem(last=False)
                        self._cache_bytes -= len(old)
                    self._cache[block_start] = block
                    self._cache_bytes += len(block)
                else:
                    self._cache.move_to_end(block_start)
                stop = min(end, block_start + expanded)
                result.extend(block[cursor - block_start:stop - block_start])
                cursor = stop
        if cursor != end:
            raise MdxError("词典条目数据不完整")
        try:
            return bytes(result).decode(self.encoding).strip("\x00\ufeff")
        except UnicodeError as exc:
            raise MdxError("词典条目文字解码失败") from exc

    def lookup(self, word: str) -> list[tuple[str, str]]:
        with self._lock:
            stat = self.path.stat()
            if self._signature != (stat.st_size, stat.st_mtime_ns):
                raise MdxError("词典文件已更改；请重新查词以更新索引")
            assert self._db is not None
            exact = normalize_key(word, case_sensitive=self._case_sensitive)
            rows = self._db.execute("SELECT word,start FROM keys WHERE exact=? ORDER BY word LIMIT 8", (exact,)).fetchall()
            if not rows and self._strip:
                normalized = normalize_key(word, True, self._case_sensitive)
                rows = self._db.execute("SELECT word,start FROM keys WHERE normalized=? ORDER BY word LIMIT 8", (normalized,)).fetchall()
            return [(key, self._record(start)) for key, start in rows]

    def close(self):
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None
            self._cache.clear()
            self._cache_bytes = 0
