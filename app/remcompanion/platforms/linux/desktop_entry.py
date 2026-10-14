"""Linux desktop boot and explicit frozen-runtime diagnostics."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments[:1] == ["--diagnose-fonts"]:
        from remcompanion.platforms.linux.font_diagnostics import main as diagnose

        return diagnose(arguments[1:])
    if sys.platform != "linux":
        print("This entry is Linux-only; use the independent Windows launcher.", file=sys.stderr)
        return 2
    try:
        from remcompanion.desktop import main as run_desktop

        run_desktop()
    except Exception:
        # Preserve the incumbent crash log and native startup-error path.
        # This shared helper does not launch the GUI when imported.
        from remcompanion.desktop_entry import _write_crash_log
        from remcompanion.platforms import native_module

        path = _write_crash_log()
        native_module("windowing").show_startup_error(path)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
