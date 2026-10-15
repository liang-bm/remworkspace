"""Software-interface localization, independent of chat and speech languages.

Chinese source strings act as gettext-style message IDs. Only reviewed UI
messages are translated; unknown text (including service errors) stays intact.
This module has no Tk, network, model or user-data dependencies.
"""

from __future__ import annotations

import re
from functools import lru_cache

from .ui_strings import MESSAGES


LANGUAGES = {
    "zh-Hans": "简体中文",
    "zh-Hant": "繁體中文",
    "en": "English",
    "ja": "日本語",
}
DEFAULT_LANGUAGE = "zh-Hans"


def weekday_labels(language: str, *, compact: bool = False) -> tuple[str, ...]:
    """Monday-first labels; calendar indices and stored weekdays stay unchanged."""
    if language == "en":
        return ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun") if compact else (
            "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    if language == "ja":
        return tuple("月火水木金土日") if compact else (
            "月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日")
    return tuple("一二三四五六日") if compact else tuple("星期" + day for day in "一二三四五六日")


def normalize_language(value: object) -> str:
    return value if isinstance(value, str) and value in LANGUAGES else DEFAULT_LANGUAGE


_FIELD = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z_0-9]*)\}(?!\})")


def _fields(source: str) -> set[str]:
    return set(_FIELD.findall(source))


def _pattern(source: str) -> re.Pattern[str]:
    parts = []
    fields = set()
    offset = 0
    for match in _FIELD.finditer(source):
        parts.append(re.escape(source[offset:match.start()]))
        field = match[1]
        if field in fields:
            raise ValueError(f"Repeated UI interpolation field: {source!r}")
        fields.add(field)
        parts.append(f"(?P<{field}>.+?)")
        offset = match.end()
    parts.append(re.escape(source[offset:]))
    return re.compile("".join(parts), re.DOTALL)


# Literal specificity makes an error/status template unable to swallow a
# longer, more specific message. Patterns are reviewed, not inferred from data.
_TEMPLATES = tuple(
    (source, _pattern(source))
    for source in sorted(MESSAGES, key=len, reverse=True)
    if _fields(source)
)

_TRANSLATED_SOURCES = {}
for _source, _translations in MESSAGES.items():
    for _translated in _translations.values():
        _TRANSLATED_SOURCES.setdefault(_translated, _source)

_REVERSE_TEMPLATES = tuple(
    (source, _pattern(text))
    for source in sorted(MESSAGES, key=len, reverse=True)
    if _fields(source)
    for text in MESSAGES[source].values()
    if any(character.isalnum() for character in _FIELD.sub('', text))
)


@lru_cache(maxsize=4096)
def canonical_source(text: str) -> str:
    """Recover reviewed UI copy, including a previously formatted translation."""
    if text in MESSAGES:
        return text
    if text in _TRANSLATED_SOURCES:
        return _TRANSLATED_SOURCES[text]
    # Formatted source copy takes precedence over translated patterns. Without
    # this, a broad "{month} · {weekday}" could swallow a search-result status.
    if any(pattern.fullmatch(text) for _source, pattern in _TEMPLATES):
        return text
    for source, pattern in _REVERSE_TEMPLATES:
        match = pattern.fullmatch(text)
        if match is not None:
            return _FIELD.sub(lambda field: match[field[1]], source)
    return text


@lru_cache(maxsize=4096)
def _display(language: str, text: str) -> str:
    source = canonical_source(text)
    if source in MESSAGES:
        return source if language == DEFAULT_LANGUAGE else MESSAGES[source][language]
    for message, pattern in _TEMPLATES:
        match = pattern.fullmatch(source)
        if match is not None:
            translated = message if language == DEFAULT_LANGUAGE else MESSAGES[message][language]
            return _FIELD.sub(lambda field: match[field[1]], translated)
    return text


class Translator:
    def __init__(self, language: str = DEFAULT_LANGUAGE) -> None:
        self.language = normalize_language(language)

    def set_language(self, language: str) -> None:
        if language not in LANGUAGES:
            raise ValueError(f"Unsupported interface language: {language!r}")
        self.language = language

    def __call__(self, source: str, **values: object) -> str:
        messages = MESSAGES.get(source)
        translated = source if self.language == DEFAULT_LANGUAGE or messages is None else messages[self.language]
        return translated.format(**values) if values else translated

    def display(self, source: str) -> str:
        """Translate a known, already-formatted UI status; never free-form text."""
        return _display(self.language, source)


def validate_catalog() -> None:
    """Reject incomplete locales and interpolation drift before releasing."""
    for source, translations in MESSAGES.items():
        fields = _fields(source)
        if set(translations) != set(LANGUAGES) - {DEFAULT_LANGUAGE}:
            raise ValueError(f"Incomplete UI message: {source!r}")
        for language, text in translations.items():
            translated_fields = _fields(text)
            if not text or translated_fields != fields:
                raise ValueError(f"Invalid {language} interpolation: {source!r}")
