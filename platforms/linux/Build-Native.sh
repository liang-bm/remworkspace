#!/usr/bin/env bash
# Linux-only bridge build; uses the existing compiler, never sudo/downloads.
set -euo pipefail
remchat_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
remchat_python="${REMCHAT_PYTHON:-python3}"
exec "$remchat_python" -B "$remchat_root/platforms/linux/scripts/build-native.py"
