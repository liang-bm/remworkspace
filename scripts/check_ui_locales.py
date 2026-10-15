"""Release audit for reviewed UI copy; never inspects user files or imports Tk.

Run from any directory: python -S scripts/check_ui_locales.py
Catalog completeness, interpolation parity and literal UI coverage are checked.
This is a source audit, not a claim that all runtime screens were visually tested.
"""
from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))

from remcompanion.i18n import LANGUAGES, Translator, validate_catalog
from remcompanion.ui_strings import MESSAGES


def _cjk(text: str) -> bool:
    return any('\u4e00' <= character <= '\u9fff' for character in text)


def audit_sources() -> list[str]:
    failures = []
    translator = Translator('en')
    base = ROOT / 'app' / 'remcompanion'
    for path in sorted(base.rglob('*.py')):
        if path.name.endswith('strings.py'):
            continue
        if 'ui' not in path.parts and path.name not in (
                'chat_window.py', 'overlay.py', 'pet_window.py', 'voice_setup_dialog.py'):
            continue
        tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            values = [item.value for item in node.keywords if item.arg in ('text', 'label', 'title')]
            function = ast.unparse(node.func)
            if function in ('_label', '_button', 'self._label', 'self._button'):
                values.extend(node.args[1:2])
            if function.endswith(('.add', '.title', '.set', '._set_status')):
                values.extend(node.args[:1])
            for value in values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    source = value.value
                    missing = _cjk(source) and source not in MESSAGES
                elif isinstance(value, ast.JoinedStr):
                    # Substitute a neutral value, preserving the actual punctuation
                    # and spacing. Translation templates must match the producer.
                    source = ''.join(item.value if isinstance(item, ast.Constant)
                                     else '1' for item in value.values)
                    missing = _cjk(source) and translator.display(source) == source
                else:
                    continue
                if missing:
                    failures.append(f'{path.relative_to(ROOT)}:{value.lineno}: {source!r}')
    return sorted(set(failures))


def main() -> int:
    validate_catalog()
    failures = audit_sources()
    if failures:
        for failure in failures:
            print(failure)
        print(f'Unreviewed UI messages: {len(failures)}', file=sys.stderr)
        return 1
    print(f'UI locale audit passed: {len(MESSAGES)} reviewed messages, {len(LANGUAGES)} languages')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
