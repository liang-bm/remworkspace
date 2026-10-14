#!/usr/bin/env bash
# Removes only owned program files; configuration/memory/models are retained.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
if [[ "$(uname -s)" != Linux ]]; then printf '%s\n' 'Run this entry on Linux.' >&2; exit 1; fi
remchat_entry="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ -f "$remchat_entry/installer/install-desktop.py" ]]; then
    exec python3 "$remchat_entry/installer/install-desktop.py" uninstall "$@"
fi
remchat_source="$(cd -- "$remchat_entry/../.." && pwd -P)"
export PYTHONPATH="$remchat_source/app/remcompanion/platforms/linux${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$remchat_entry/scripts/install-desktop.py" uninstall "$@"
