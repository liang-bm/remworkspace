"""本地单词卡片，导入文件与复习记录分开，用户可自行编辑。"""

from __future__ import annotations

import csv
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import re

from ..word_memory import (MAX_CREDIT, MAX_EXAMPLE_TEXT, MAX_SOURCE_TITLE,
                           MAX_TRANSLATION)


DEMO_WORDS = [
    ("companion", "伙伴；陪伴者", "A companion makes a long day feel shorter."),
    ("patient", "耐心的", "Be patient with yourself."),
    ("curious", "好奇的", "Stay curious about the world."),
    ("bloom", "开花；绽放", "The flowers bloom in spring."),
    ("gentle", "温柔的；轻柔的", "She gave me a gentle smile."),
    ("focus", "专注；集中", "Let's focus on one thing at a time."),
    ("progress", "进展；进步", "Small steps still make progress."),
    ("remember", "记住；想起", "Remember to take a short break."),
]


@dataclass
class WordDeck:
    cards: list[dict[str, object]] = field(default_factory=list)

    @classmethod
    def demo(cls) -> WordDeck:
        return cls([{"word": word, "meaning": meaning, "example": example,
                     "reviews": 0, "known": 0, "due": ""}
                    for word, meaning, example in DEMO_WORDS])

    @classmethod
    def restore(cls, raw: dict[str, object]) -> WordDeck:
        if not raw:
            return cls.demo()
        cards = raw.get("cards")
        if not isinstance(cards, list):
            raise ValueError("词库存档应包含 cards 列表，原文件已保留")
        result = []
        for card in cards:
            if not isinstance(card, dict) or not str(card.get("word", "")).strip():
                raise ValueError("词库存在空白或格式错误的卡片，原文件已保留")
            # Preserve user notes, source references and future metadata while
            # normalizing the existing core fields. Merely restoring a deck
            # never writes or replaces legacy user material on disk.
            restored = dict(card)
            restored.update({"word": str(card["word"]), "meaning": str(card.get("meaning", "")),
                           "example": str(card.get("example", "")),
                           "notes": str(card.get("notes", "")),
                           "reviews": max(0, int(card.get("reviews", 0))),
                           "known": max(0, int(card.get("known", 0))),
                           "due": str(card.get("due", ""))})
            result.append(restored)
        return cls(result)

    def next_due(self, now: datetime | None = None) -> dict[str, object] | None:
        now = now or datetime.now()
        candidates = []
        for card in self.cards:
            try:
                due = datetime.fromisoformat(str(card.get("due", "")))
            except ValueError:
                due = datetime.min
            # 兼容手动编辑或从其他时区导入的 ISO 时间。
            if due.tzinfo is not None:
                due = due.astimezone().replace(tzinfo=None)
            if due <= now:
                candidates.append((due, int(card.get("reviews", 0)), str(card["word"]), card))
        return min(candidates, key=lambda item: item[:3])[3] if candidates else None

    def review(self, card: dict[str, object], known: bool, now: datetime | None = None) -> None:
        now = now or datetime.now()
        if card not in self.cards:
            raise ValueError("卡片不在当前词库中")
        card["reviews"] = int(card.get("reviews", 0)) + 1
        card["known"] = int(card.get("known", 0)) + (1 if known else 0)
        # 透明的卡片规则：记住后次日再见，没记住 10 分钟后重试。
        card["due"] = (now + (timedelta(days=1) if known else timedelta(minutes=10))).isoformat(timespec="seconds")

    def import_file(self, path: Path | str) -> int:
        path = Path(path)
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("词库文件超过 32 MB，请分批导入")
        text = path.read_text(encoding="utf-8-sig")
        incoming: list[dict[str, object]] = []
        if path.suffix.lower() == ".json":
            raw = json.loads(text)
            rows = raw.get("cards", []) if isinstance(raw, dict) else raw
            if not isinstance(rows, list):
                raise ValueError("JSON 需为卡片数组，或包含 cards 的对象")
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("每个 JSON 卡片需包含 word、meaning，可选 example")
                material = dict(row)
                material.update({key: str(row.get(key, "")) for key in ("word", "meaning", "example", "notes")})
                incoming.append(material)
        else:
            delimiter = "\t" if path.suffix.lower() in (".tsv", ".txt") else ","
            rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
            if rows and rows[0] and rows[0][0].strip().lower() in ("word", "单词"):
                rows = rows[1:]
            for row in rows:
                if not row or not row[0].strip() or row[0].lstrip().startswith("#"):
                    continue
                incoming.append({"word": row[0], "meaning": row[1] if len(row) > 1 else "",
                                 "example": row[2] if len(row) > 2 else "", "notes": row[3] if len(row) > 3 else ""})
        if not incoming:
            raise ValueError("文件里没有可导入的单词")
        if len(incoming) > 100000:
            raise ValueError("一次导入最多 100000 张卡片")
        existing = {str(card["word"]).casefold() for card in self.cards}
        added = []
        for row in incoming:
            word = str(row["word"]).strip()
            if not word:
                raise ValueError("单词不能为空；此次导入未保存")
            if word.casefold() not in existing:
                material = dict(row)
                material.update({"word": word, "meaning": str(row["meaning"]).strip(),
                                 "example": str(row["example"]).strip(), "notes": str(row.get("notes", "")).strip(),
                                 "reviews": max(0, int(row.get("reviews", 0))),
                                 "known": max(0, int(row.get("known", 0))), "due": str(row.get("due", ""))})
                added.append(material)
                existing.add(word.casefold())
        self.cards.extend(added)
        return len(added)

    def add_dictionary_entry(self, entry: dict[str, str]) -> bool:
        """Explicitly add one lookup result; never replace an existing review card."""
        word = str(entry.get("word", "")).strip()
        if not word:
            raise ValueError("词条缺少单词，未加入词卡")
        if any(str(card["word"]).casefold() == word.casefold() for card in self.cards):
            return False
        source = str(entry.get("source", "")).strip()
        # The dictionary remains an authoritative resource. Store the word and
        # reference, not a silent copy into the user's editable own material.
        self.cards.append({"word": word, "meaning": "", "example": "", "notes": "",
                           "dictionary_source": source,
                           "reviews": 0, "known": 0, "due": ""})
        return True

    def update_material(self, word: str, *, notes: str, meaning: str | None = None, example: str | None = None) -> None:
        """Edit only personal fields, leaving scheduling/history untouched."""
        matches = [card for card in self.cards if str(card["word"]) == word]
        if len(matches) != 1:
            raise ValueError("词卡已变化或存在同名卡片，请重新打开编辑窗口")
        matches[0]["notes"] = str(notes).strip()
        for key, value in (("meaning", meaning), ("example", example)):
            if value is not None:
                matches[0][key] = str(value).strip()

    def _example_card(self, word: str) -> dict[str, object]:
        matches = [card for card in self.cards if str(card["word"]) == word]
        if len(matches) != 1:
            raise ValueError("词卡已变化或存在同名卡片，请重新打开 AI 帮记")
        return matches[0]

    @staticmethod
    def _example_record(raw: object) -> dict[str, str]:
        """Validate an explicitly selected record; verification precedes storage."""
        if not isinstance(raw, dict):
            raise ValueError("例句格式不正确，未加入个人例句库")
        result = {}
        for key, maximum in (("text", MAX_EXAMPLE_TEXT), ("translation", MAX_TRANSLATION),
                             ("source_title", MAX_SOURCE_TITLE), ("credit", MAX_CREDIT), ("quote_id", 96)):
            value = raw.get(key, "")
            if (not isinstance(value, str) or len(value) > maximum
                    or any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
                raise ValueError("例句内容过长或格式不正确，未加入个人例句库")
            result[key] = value.strip()
        if not result["text"] or not result["translation"]:
            raise ValueError("例句及中文翻译不能为空，未加入个人例句库")
        kind = raw.get("kind")
        if not isinstance(kind, str) or kind not in {"ai", "quote"}:
            raise ValueError("例句类型不正确，未加入个人例句库")
        result["kind"] = kind
        result["source_kind"] = ""
        if kind == "ai":
            # An AI example must never persist a model-invented attribution.
            result.update({"source_title": "", "credit": "", "quote_id": ""})
        else:
            source_kind = raw.get("source_kind", "book")
            if (not isinstance(source_kind, str) or source_kind not in {"book", "film", "tv"} or not result["source_title"]
                    or not result["credit"] or not result["quote_id"]
                    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", result["quote_id"]) is None
                    or result["text"] != raw["text"]):
                raise ValueError("原句缺少核验出处，请重新生成 AI 帮记")
            result["source_kind"] = source_kind
        return result

    @staticmethod
    def _example_identity(text: str) -> str:
        return re.sub(r"\s+", " ", text.strip()).casefold().replace("’", "'")

    def get_examples(self, word: str) -> tuple[dict[str, object], ...]:
        """Read isolated copies of valid personal examples, without auto-migration."""
        bank = self._example_card(word).get("example_bank", [])
        if not isinstance(bank, list):
            return ()
        result = []
        for raw in bank:
            if not isinstance(raw, dict):
                continue
            try:
                normalized = self._example_record(raw)
            except ValueError:
                continue
            # Preserve user-owned extra metadata while protecting storage from
            # aliases in a rendering/editor consumer, including nested extras.
            result.append({**deepcopy(raw), **normalized})
        return tuple(result)

    def add_examples(self, word: str, examples: list[dict[str, object]] | tuple[dict[str, object], ...]) -> int:
        """Explicit deduplicated append; notes/legacy examples/reviews untouched.

        The UI passes only checked rows from ``parse_memory_result``. No model
        request, migration or explanation-to-notes operation is implied here.
        Batch validation completes before mutation, and damaged existing banks
        are preserved instead of replaced with a fresh empty list.
        """
        card = self._example_card(word)
        if not isinstance(examples, (list, tuple)):
            raise ValueError("请选择要加入的例句")
        prepared = [self._example_record(item) for item in examples]
        bank = card.get("example_bank", [])
        if not isinstance(bank, list):
            raise ValueError("个人例句库存档格式异常，原内容已保留；请先检查存档")
        seen = {self._example_identity(raw["text"]) for raw in bank
                if isinstance(raw, dict) and isinstance(raw.get("text"), str)}
        added = []
        for item in prepared:
            identity = self._example_identity(item["text"])
            if identity not in seen:
                added.append(item)
                seen.add(identity)
        if added:
            card["example_bank"] = [*bank, *added]
        return len(added)

    def snapshot(self) -> dict[str, object]:
        return {"cards": self.cards}
