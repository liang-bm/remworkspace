"""Collect dependency license notices using the exact packaging Python runtime.

Usage: python scripts/collect_runtime_licenses.py RELEASE_ROOT/licenses
The destination must not exist. This reads installed package metadata, not
application configuration, credentials or user data.
"""
from __future__ import annotations

import argparse
import importlib.metadata
from pathlib import Path
import re
import shutil


def collect(destination: Path) -> tuple[int, int]:
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError('Existing license directory preserved')
    destination.mkdir(parents=True)
    notices = packages = 0
    index = ['# Third-party license notices', '',
             'Collected from the Python environment used to build this package.',
             'Some notices belong to build tools rather than runtime components.', '']
    for distribution in sorted(importlib.metadata.distributions(),
                               key=lambda item: item.metadata.get('Name', '').lower()):
        name = distribution.metadata.get('Name', '')
        version = distribution.version
        folder = re.sub(r'[^A-Za-z0-9_.+-]', '_', name + '-' + version)
        found = 0
        for entry in distribution.files or ():
            if '..' in entry.parts or entry.is_absolute():
                continue
            if not entry.name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE', 'AUTHORS')):
                continue
            source = Path(distribution.locate_file(entry))
            if source.is_symlink() or not source.is_file():
                continue
            target = destination / folder / str(entry)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            found += 1
        if found:
            notices += found
            packages += 1
            index.append(f'- {name} {version}: {folder}/ ({found} notices)')
    if not notices:
        raise ValueError('No installed dependency notices were found')
    (destination / 'README.md').write_text('\n'.join(index) + '\n', encoding='utf-8')
    return notices, packages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    arguments = parser.parse_args()
    notices, packages = collect(arguments.destination)
    print(f'Collected {notices} notices from {packages} distributions')


if __name__ == '__main__':
    main()
