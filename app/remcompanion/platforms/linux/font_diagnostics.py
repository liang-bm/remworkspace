"""Explicit Linux font diagnostics; no model calls or global font installation."""

from __future__ import annotations

import argparse
import json
import locale
import os
from pathlib import Path
import sys

from .fonts import configure_fonts


SAMPLE = "雷姆设置聊天語音記憶廣場こんにちは日本語"
FONT_SPECS = {
    "body": ("Microsoft YaHei UI", 12),
    "display": ("Yu Gothic UI Semibold", 17),
    "latin": ("Segoe UI", 12),
    "code": ("Cascadia Mono", 12),
    "sans-serif": ("sans-serif", 12),
    "monospace": ("monospace", 12),
    "default": "TkDefaultFont",
    "text": "TkTextFont",
    "menu": "TkMenuFont",
    "fixed": "TkFixedFont",
}


def collect_font_diagnostics(root: Path, sample: str = SAMPLE) -> dict:
    """Inspect only this interpreter, not an existing/frozen GUI's pixels.

    The existing font configurator may refresh the application's own cache;
    caller-supplied FONTCONFIG_FILE remains authoritative. No user text,
    memories, operating-system fonts or unknown processes are read/changed.
    """
    report = {
        "scope": "this interpreter's character font selection; not pixel verification or a claim that another GUI is fixed",
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "filesystem_encoding": sys.getfilesystemencoding(),
        "locale_encoding": locale.getpreferredencoding(False),
        "root": str(root.resolve()),
        "environment": {name: os.environ.get(name) for name in ("LANG", "LC_ALL", "DISPLAY", "FONTCONFIG_FILE")},
        "issues": [],
        "fonts": {},
    }
    if sys.platform != "linux":
        report["issues"].append("This diagnostic entry is Linux-only; Windows fonts are unchanged.")
        report["collected"] = False
        return report
    try:
        configured = configure_fonts(root)
        report["fontconfig"] = {
            "application_configured": configured,
            "file": os.environ.get("FONTCONFIG_FILE"),
            "source": "application-local" if configured else "caller-controlled or system",
        }
    except OSError as error:
        report["issues"].append(f"Application-local font setup failed: {error}")
    tk_root = None
    try:
        import tkinter as tk
        import _tkinter

        tk_root = tk.Tk()
        from .windowing import configure_tk_scaling
        configure_tk_scaling(tk_root)
        tk_root.withdraw()
        report["tk"] = {
            "version": str(tk_root.tk.call("package", "provide", "Tk")),
            "window_system": str(tk_root.tk.call("tk", "windowingsystem")),
            "scaling":float(tk_root.tk.call('tk','scaling')),
            "xft_dpi":getattr(tk_root,'_remchat_xft_dpi',None),
            "extension": _tkinter.__file__,
            "tcl_library": str(tk_root.tk.call("info", "library")),
        }
        families = tk_root.tk.splitlist(tk_root.tk.call("font", "families"))
        report["noto_cjk_visible"] = "Noto Sans CJK SC" in families
        if not report["noto_cjk_visible"]:
            report["issues"].append("Bundled Noto CJK is not visible in this interpreter; inspect the selected environment/configuration. Other system fonts may still support CJK.")
        characters = tuple(dict.fromkeys(char for char in sample if char.isprintable() and not char.isspace()))
        for role, font in FONT_SPECS.items():
            primary = str(tk_root.tk.call("font", "actual", font, "-family"))
            glyphs = []
            for character in characters:
                glyphs.append({
                    "character": character,
                    "codepoint": f"U+{ord(character):04X}",
                    "family": str(tk_root.tk.call("font", "actual", font, "-family", "--", character)),
                    "width": int(tk_root.tk.call("font", "measure", font, character)),
                })
            report["fonts"][role] = {"primary_family": primary, "characters": glyphs}
        report["collected"] = True
    except (ImportError, RuntimeError) as error:
        report["issues"].append(f"Tk/font diagnostics unavailable: {error}")
        report["collected"] = False
    except Exception as error:
        # Includes TclError from missing display or unsupported font commands.
        # A partial report is useful; it must never be presented as completed.
        report["issues"].append(f"{type(error).__name__}: {error}")
        report["collected"] = False
    finally:
        if tk_root is not None:
            tk_root.destroy()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    if os.environ.get("REM_HOME"):
        default_root = Path(os.environ["REM_HOME"])
    elif getattr(sys, "frozen", False):
        default_root = Path(sys.executable).resolve().parent.parent
    else:
        default_root = Path(__file__).resolve().parents[4]
    parser.add_argument("--root", type=Path, default=default_root)
    parser.add_argument("--sample", default=SAMPLE, help="explicit diagnostic text; never taken from chat history")
    options = parser.parse_args(argv)
    report = collect_font_diagnostics(options.root, options.sample)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # Zero means a diagnostic was collected, not that pixels are verified.
    return 0 if report["collected"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
