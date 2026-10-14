#!/usr/bin/env bash
# 只准备 Linux 前端。无 sudo / shell init / GPU 包 / 模型下载。
set -euo pipefail
remchat_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
if [[ "$(uname -m)" != x86_64 || "$(uname -s)" != Linux ]]; then
    printf '%s\n' 'This setup supports Linux x86_64 (including WSL2) only.' >&2
    exit 1
fi
for dependency in curl sha256sum; do
    command -v "$dependency" >/dev/null || { printf 'Missing: %s\n' "$dependency" >&2; exit 1; }
done
remchat_tools="$remchat_root/runtime/linux-tools"
remchat_fonts="$remchat_root/runtime/linux-fonts"
remchat_prefix="$remchat_root/runtime/desktop-linux-xft"
export TMPDIR="$remchat_root/tmp/linux-setup"
export MAMBA_ROOT_PREFIX="$remchat_root/cache/micromamba-linux"
export PIP_CACHE_DIR="$remchat_root/cache/pip-linux"
mkdir -p -- "$remchat_tools" "$remchat_fonts" "$TMPDIR" "$MAMBA_ROOT_PREFIX" "$PIP_CACHE_DIR"

fetch_verified() {
    local url="$1" expected="$2" target="$3" temporary
    if [[ -f "$target" ]]; then
        if [[ "$(sha256sum -- "$target" | cut -d ' ' -f 1)" == "$expected" ]]; then
            printf 'Reuse verified: %s\n' "$target"
            return
        fi
        printf 'Existing file has a different checksum; preserved: %s\n' "$target" >&2
        return 1
    fi
    temporary="$(mktemp "$TMPDIR/download.XXXXXXXX")"
    if ! curl --fail --location --retry 3 --connect-timeout 20 --max-time 300 -H 'Accept: application/vnd.github.raw+json' "$url" -o "$temporary"; then
        rm -- "$temporary"
        return 1
    fi
    if [[ "$(sha256sum -- "$temporary" | cut -d ' ' -f 1)" != "$expected" ]]; then
        printf 'Checksum verification failed: %s\n' "$url" >&2
        rm -- "$temporary"
        return 1
    fi
    mv -- "$temporary" "$target"
}

remchat_mamba="$remchat_tools/micromamba-2.9.0"
fetch_verified 'https://github.com/mamba-org/micromamba-releases/releases/download/2.9.0-0/micromamba-linux-64' \
    366cd9cd8be14df1ab8ed50352a82111082a36686b2d389fdb79a92c3fafb3e3 "$remchat_mamba"
chmod u+x -- "$remchat_mamba"
if [[ ! -x "$remchat_prefix/bin/python" ]]; then
    if [[ -e "$remchat_prefix" ]]; then
        printf 'Incomplete environment preserved: %s. Move it aside before retrying.\n' "$remchat_prefix" >&2
        exit 1
    fi
    "$remchat_mamba" create --no-rc --yes --prefix "$remchat_prefix" \
        --override-channels --channel conda-forge 'python=3.12' 'tk=8.6.13=xft_*' pip fonts-conda-forge
fi
# The PyPI wheel includes AVIF support; the tested conda Pillow build did not.
# Keep this pinned to the version actually validated, not an unbounded latest.
"$remchat_prefix/bin/python" -m pip install --disable-pip-version-check --force-reinstall --only-binary :all: 'Pillow==12.3.0' 'Pygments==2.19.2' 'SecretStorage==3.5.0' 'jeepney==0.9.0' 'cryptography==50.0.2' 'cffi==2.1.1' 'pycparser==3.11' 'sqlite-vec==0.1.6'
"$remchat_prefix/bin/python" -c 'import sys, tkinter, ctypes.util; from PIL import features; assert sys.version_info >= (3,11); assert tkinter.TkVersion == 8.6, "Expected the pinned Xft Tk 8.6 build"; assert all(ctypes.util.find_library(name) for name in ("X11", "Xft", "fontconfig")), "System X11/Xft/fontconfig libraries are required"; assert features.check("avif"), "Pillow lacks AVIF: reinstall the binary PyPI wheel with --force-reinstall"'
fetch_verified 'https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf' \
    2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b "$remchat_fonts/NotoSansCJKsc-Regular.otf"
fetch_verified 'https://api.github.com/repos/notofonts/noto-cjk/contents/Sans/LICENSE' \
    6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2 "$remchat_fonts/LICENSE-Noto-CJK.txt"
printf '%s\n' 'Front-end setup complete. Run: bash platforms/linux/Start-RemChat.sh' \
    'Requires system X11/Xft/fontconfig and an X11 display. No system packages were installed.' \
    'Pet silhouettes additionally need libxcb/libxcb-shape and Shape 1.1; missing support is logged.' \
    'Linux model servers and TTS training require separate validation.'
if [[ -n "${WSL_DISTRO_NAME:-}" && -n "${WAYLAND_DISPLAY:-}" ]] && ! command -v wl-copy >/dev/null; then
    printf '%s\n' 'WSLg clipboard prerequisite: install the system wl-clipboard package (Ubuntu/Debian: sudo apt install wl-clipboard).' \
        'RemChat will explain this prerequisite instead of using the unsafe WSLg 1.0.71 Tk clipboard path.' \
        'This setup does not install system packages or restart WSLg.'
fi
