"""Software-interface localization, independent of chat and speech languages.

Chinese source strings act as gettext-style message IDs. Only reviewed UI
messages are translated; unknown text (including service errors) stays intact.
This module has no Tk, network, model or user-data dependencies.
"""

from __future__ import annotations

from string import Formatter
import re

from .ui_strings import MESSAGES


LANGUAGES = {
    "zh-Hans": "简体中文",
    "zh-Hant": "繁體中文",
    "en": "English",
    "ja": "日本語",
}
DEFAULT_LANGUAGE = "zh-Hans"


def normalize_language(value: object) -> str:
    return value if isinstance(value, str) and value in LANGUAGES else DEFAULT_LANGUAGE


def _pattern(source: str) -> re.Pattern[str]:
    parts = []
    fields = set()
    for literal, field, _spec, _conversion in Formatter().parse(source):
        parts.append(re.escape(literal))
        if field is not None:
            if not field.isidentifier() or field in fields:
                raise ValueError(f"Invalid UI interpolation field: {source!r}")
            fields.add(field)
            parts.append(f"(?P<{field}>.+?)")
    return re.compile("".join(parts), re.DOTALL)


# Literal specificity makes an error/status template unable to swallow a
# longer, more specific message. Patterns are reviewed, not inferred from data.
_TEMPLATES = tuple(
    (source, _pattern(source))
    for source in sorted(MESSAGES, key=len, reverse=True)
    if any(field is not None for _, field, _, _ in Formatter().parse(source))
)


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
        if source in MESSAGES:
            return self(source)
        for message, pattern in _TEMPLATES:
            match = pattern.fullmatch(source)
            if match is not None:
                return self(message, **match.groupdict())
        return source


def validate_catalog() -> None:
    """Reject incomplete locales and interpolation drift before releasing."""
    for source, translations in MESSAGES.items():
        fields = {field for _, field, _, _ in Formatter().parse(source) if field is not None}
        if set(translations) != set(LANGUAGES) - {DEFAULT_LANGUAGE}:
            raise ValueError(f"Incomplete UI message: {source!r}")
        for language, text in translations.items():
            translated_fields = {field for _, field, _, _ in Formatter().parse(text) if field is not None}
            if not text or translated_fields != fields:
                raise ValueError(f"Invalid {language} interpolation: {source!r}")
