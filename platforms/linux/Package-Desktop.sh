#!/usr/bin/env bash
# Linux archive/install inputs only; no upload, sudo or dependency installation.
set -euo pipefail
if [[ "$(uname -s)" != Linux ]]; then printf '%s\n' 'Run this packaging entry on Linux.' >&2; exit 1; fi
if [[ "$#" -lt 3 || "$#" -gt 4 ]]; then printf '%s\n' 'Usage: Package-Desktop.sh RELEASE_ROOT VERSION OUTPUT_DIRECTORY [--private-local]' >&2; exit 1; fi
exec python3 "$(dirname -- "${BASH_SOURCE[0]}")/scripts/package-desktop.py" "$@"
