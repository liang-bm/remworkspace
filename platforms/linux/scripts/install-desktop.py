"""Per-user Linux install/upgrade/uninstall; Python 3.8+, no sudo or downloads."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys

from install_files import Transaction, fingerprint, program_path, reject_linked_parents
from instance import acquire_single_instance


def install_root(value):
    path = Path(os.path.abspath(os.path.expanduser(value)))
    reject_linked_parents(path)
    # A program installation must never own a home, filesystem or mount root.
    forbidden = {Path("/"), Path.home(), Path("/home"), Path("/mnt"), Path("/tmp"), Path("/usr"), Path("/opt")}
    if path in forbidden or len(path.parts) < 4 or re.fullmatch(r"/mnt/[A-Za-z]", str(path)):
        raise ValueError("Choose a dedicated application directory, not a broad root")
    return path


def installation_id(root):
    return hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:20]


def desktop_location(root):
    value = os.environ.get("XDG_DATA_HOME", "")
    base = Path(value) if value and Path(value).is_absolute() else Path.home() / ".local/share"
    location = base / "applications" / ("io.remchat." + installation_id(root) + ".desktop")
    reject_linked_parents(location.parent)
    return location


def desktop_text(root, private, include_icon=False):
    executable = str(root / "desktop-app/RemChat")
    if "=" in executable or any(ord(character) < 32 for character in executable):
        raise ValueError("This path cannot be represented by a desktop launcher")
    # Desktop Exec escaping has two layers: key-value then argument quoting.
    escaped = executable.replace("\\", "\\\\\\\\")
    for character in ('"', '`', '$'):
        escaped = escaped.replace(character, "\\\\" + character)
    escaped = escaped.replace("%", "%%")
    command = '"' + escaped + '"'
    if "%" in executable:
        # GLib 2.64 checks argv[0] before expanding %% into %. Keep the literal
        # target as an argument to a system env executable; no shell or env
        # assignments are involved, and -- prevents option interpretation.
        launcher = next((value for value in ("/usr/bin/env", "/bin/env")
                         if os.path.isfile(value) and os.access(value, os.X_OK)), None)
        if launcher is None:
            raise ValueError("A percent-sign installation path needs system env for its desktop launcher; use --no-shortcut or a different path")
        command = '"' + launcher + '" -- ' + command
    path = str(root).replace("\\", "\\\\")
    # Icon is a key-value path, not an Exec argument or shell expression.
    icon = ""
    if include_icon:
        icon_path = str(root / "desktop-app/_internal/remcompanion/assets/icon.png")
        icon = '\nIcon=' + icon_path.replace("\\", "\\\\")
    name = "RemChat Local Build" if private else "RemChat"
    return ('[Desktop Entry]\nType=Application\nVersion=1.0\nName=' + name
            + '\nExec=' + command + '\nPath=' + path + icon
            + '\nTerminal=false\nCategories=Utility;\nStartupNotify=false\n')


def load_state(root):
    path = root / ".remchat-install/state.json"
    reject_linked_parents(path)
    if not path.exists():
        if (root / "desktop-app").exists() or (root / "installer").exists():
            raise ValueError("Unmanaged application exists; choose a new installation directory")
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if (value.get("schema") != 1 or value.get("product") != "RemChat" or value.get("platform") != "linux"
            or value.get("root") != str(root) or value.get("id") != installation_id(root)
            or value.get("status") not in {"installed", "uninstalled"} or not isinstance(value.get("files"), dict)):
        raise ValueError("Installation identity/state is invalid; preserved for review")
    for relative in value["files"]:
        program_path(root, relative)
    return value


def validate_payload(payload):
    reject_linked_parents(payload)
    metadata_path = payload / "release.json"
    reject_linked_parents(metadata_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if (metadata.get("schema") != 1 or metadata.get("platform") != "linux"
            or metadata.get("architecture") != platform.machine()
            or metadata.get("visibility") not in {"public", "private-local"}
            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+([-.][A-Za-z0-9.]+)?", str(metadata.get("version", "")))
            or not isinstance(metadata.get("files"), dict)):
        raise ValueError("Invalid Linux package/version/architecture metadata")
    for relative, expected in metadata["files"].items():
        if relative == "config/rem_companion.toml":
            path = payload / relative
            reject_linked_parents(path)
        else:
            path = program_path(payload, relative)
        if fingerprint(path) != expected:
            raise ValueError("Package file checksum mismatch: " + relative)
        if path.suffix.lower() in {".exe", ".dll", ".pyd"}:
            raise ValueError("Windows runtime is not a Linux installation input")
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            if Path(os.readlink(path)).is_absolute() or payload / "desktop-app" not in resolved.parents or not resolved.is_file():
                raise ValueError("Non-portable package link: " + relative)
    required = {"desktop-app/RemChat", "desktop-app/_internal/base_library.zip", "config/rem_companion.toml",
                "Install.sh", "Uninstall.sh", "installer/install-desktop.py", "installer/install_files.py", "installer/instance.py"}
    if not required.issubset(metadata["files"]):
        raise ValueError("Package is missing installation/runtime inputs")
    executable = payload / "desktop-app/RemChat"
    header = executable.read_bytes()[:20]
    machine = {"x86_64": 62, "aarch64": 183}.get(platform.machine())
    if len(header) < 20 or header[:6] != b"\x7fELF\x02\x01" or int.from_bytes(header[18:20], "little") != machine:
        raise ValueError("Executable is not a supported native 64-bit Linux ELF")
    files = {relative: record for relative, record in metadata["files"].items() if relative != "config/rem_companion.toml"}
    files["release.json"] = fingerprint(metadata_path)
    return metadata, files


def preflight(root, old, new_files):
    previous = old["files"] if old else {}
    for relative in new_files:
        path = program_path(root, relative)
        actual = fingerprint(path)
        if actual is not None and (relative not in previous or actual != previous[relative]):
            raise ValueError("Unowned or modified program file preserved: " + relative)
    for relative in previous:
        program_path(root, relative)
    config = root / "config/rem_companion.toml"
    reject_linked_parents(config)
    if config.exists() and not config.is_file():
        raise ValueError("Configuration is not a regular file")


def run_install(payload, root, shortcut):
    metadata, files = validate_payload(payload)
    if root == payload or root in payload.parents or payload in root.parents:
        raise ValueError("Installer input and destination must be separate directories")
    old = load_state(root)
    if old and old.get("visibility") != metadata["visibility"]:
        raise ValueError("Public and private installations must use separate target directories")
    preflight(root, old, files)
    location = desktop_location(root) if shortcut else None
    # Older bundles without this asset remain installable. Only a checksum-
    # verified ordinary package file can supply the native launcher icon.
    icon_record = files.get("desktop-app/_internal/remcompanion/assets/icon.png", {})
    launcher_text = desktop_text(root, metadata["visibility"] == "private-local",
                                 include_icon=icon_record.get("kind") == "file") if location else None
    if location and fingerprint(location) is not None:
        prior_launcher = old.get("launcher") if old else None
        if not prior_launcher or prior_launcher["path"] != str(location) or fingerprint(location) != prior_launcher["fingerprint"]:
            raise ValueError("Existing desktop launcher preserved: " + str(location))
    elif not shortcut and old and old.get("launcher"):
        raise ValueError("Keep the existing shortcut during upgrade; uninstall it before changing shortcut mode")
    transaction = Transaction(root)
    retained = []
    try:
        planned = [program_path(root, relative) for relative in files]
        planned += [program_path(root, relative) for relative, record in (old["files"] if old else {}).items()
                    if relative not in files and fingerprint(program_path(root, relative)) == record]
        config = root / "config/rem_companion.toml"
        if not config.exists():
            planned.append(config)
        if location:
            planned.append(location)
        planned.append(root / ".remchat-install/state.json")
        transaction.prepare(planned)
        for relative, record in (old["files"] if old else {}).items():
            if relative not in files:
                path = program_path(root, relative)
                actual = fingerprint(path)
                if actual == record:
                    transaction.remove(path)
                elif actual is not None:
                    retained.append(relative)
        for relative in files:
            transaction.copy(payload / relative, program_path(root, relative))
        config = root / "config/rem_companion.toml"
        if not config.exists():
            transaction.copy(payload / "config/rem_companion.toml", config)
        launcher = None
        if location:
            transaction.write(location, launcher_text)
            launcher = {"path": str(location), "fingerprint": fingerprint(location)}
        state = {"schema": 1, "product": "RemChat", "platform": "linux", "root": str(root), "id": installation_id(root),
                 "version": metadata["version"], "visibility": metadata["visibility"], "status": "installed", "files": files, "launcher": launcher}
        transaction.write(root / ".remchat-install/state.json", json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        transaction.finish()
    except BaseException:
        transaction.rollback()
        raise
    return {"status": "installed", "root": str(root), "version": metadata["version"], "backup": str(transaction.backup), "retained": retained}


def run_uninstall(root):
    old = load_state(root)
    if not old or old["status"] != "installed":
        raise ValueError("No managed Linux installation at this path")
    transaction = Transaction(root)
    retained = []
    try:
        planned = [program_path(root, relative) for relative, record in old["files"].items()
                   if fingerprint(program_path(root, relative)) == record]
        launcher = old.get("launcher")
        if launcher:
            location = desktop_location(root)
            if launcher["path"] == str(location) and fingerprint(location) == launcher["fingerprint"]:
                planned.append(location)
        planned.append(root / ".remchat-install/state.json")
        transaction.prepare(planned)
        for relative, record in old["files"].items():
            path = program_path(root, relative)
            actual = fingerprint(path)
            if actual == record:
                transaction.remove(path)
            elif actual is not None:
                retained.append(relative)
        launcher = old.get("launcher")
        if launcher:
            location = desktop_location(root)
            if launcher["path"] == str(location) and fingerprint(location) == launcher["fingerprint"]:
                transaction.remove(location)
            else:
                retained.append("desktop launcher (modified or different XDG_DATA_HOME)")
        old.update(status="uninstalled", files={}, launcher=None)
        transaction.write(root / ".remchat-install/state.json", json.dumps(old, ensure_ascii=False, indent=2) + "\n")
        transaction.finish()
    except BaseException:
        transaction.rollback()
        raise
    # Remove only empty program directories, never root/config/user directories.
    for base in (root / "desktop-app", root / "installer"):
        if base.is_dir() and not base.is_symlink():
            for directory in sorted([base] + [p for p in base.rglob("*") if p.is_dir() and not p.is_symlink()], key=lambda p: len(p.parts), reverse=True):
                try:
                    directory.rmdir()
                except OSError:
                    pass
    return {"status": "uninstalled", "root": str(root), "backup": str(transaction.backup), "retained": retained,
            "user_data": "Configuration, memory, models and other user files remain untouched"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("install", "uninstall"))
    parser.add_argument("--target", required=True, help="Dedicated app directory; never the existing Windows project")
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--no-shortcut", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux" or os.geteuid() == 0:
        raise ValueError("Run as a normal Linux user, not sudo/root")
    root = install_root(args.target)
    handle = acquire_single_instance()
    if handle is None:
        raise ValueError("RemChat or another installer is running; close it before changing program files")
    with closing(handle):
        if args.operation == "install":
            if args.payload is None:
                raise ValueError("--payload is required for installation")
            reject_linked_parents(args.payload.absolute())
            result = run_install(args.payload.resolve(strict=True), root, not args.no_shortcut)
        else:
            result = run_uninstall(root)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, KeyError) as error:
        print("Linux installation failed; existing data preserved: " + str(error), file=sys.stderr)
        sys.exit(1)
