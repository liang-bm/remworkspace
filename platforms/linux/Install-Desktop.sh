#!/usr/bin/env bash
# Native Linux installation only; never forwards to Windows setup.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
if [[ "$(uname -s)" != Linux ]]; then printf '%s\n' 'Run this entry on Linux.' >&2; exit 1; fi
remchat_entry="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ -f "$remchat_entry/installer/install-desktop.py" ]]; then
    exec python3 "$remchat_entry/installer/install-desktop.py" install --payload "$remchat_entry" "$@"
fi
remchat_source="$(cd -- "$remchat_entry/../.." && pwd -P)"
export PYTHONPATH="$remchat_source/app/remcompanion/platforms/linux${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$remchat_entry/scripts/install-desktop.py" install "$@"
