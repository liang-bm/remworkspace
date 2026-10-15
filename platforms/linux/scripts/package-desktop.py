"""Create a Linux archive with standalone installation tools and checksums."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import runpy
import shutil
import sys
import tarfile
import tempfile

from install_files import fingerprint, reject_linked_parents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument("version")
    parser.add_argument("output", type=Path)
    parser.add_argument("--private-local", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux" or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+([-.][A-Za-z0-9.]+)?", args.version):
        raise ValueError("Native Linux and a valid release version are required")
    payload = args.payload.absolute()
    reject_linked_parents(payload)
    for name in ("data", "models", "runtime", "engines", "background", "rem-voice", "rem-voice2", "resources"):
        if (payload / name).exists() or (payload / name).is_symlink():
            raise ValueError("User content is not a package input: " + name)
    scripts = Path(__file__).resolve().parent
    validate = runpy.run_path(str(scripts / "validate-release.py"))["validate"]
    validate(payload, private=args.private_local)
    if not (payload / "desktop-app/_internal/base_library.zip").is_file():
        raise ValueError("Missing frozen base library")
    output = args.output.absolute()
    reject_linked_parents(output)
    suffix = "-local" if args.private_local else ""
    artifact = output / ("RemChat-" + args.version + "-linux-" + platform.machine() + suffix + ".tar.gz")
    checksum = artifact.with_name(artifact.name + ".sha256")
    if artifact.exists() or artifact.is_symlink() or checksum.exists() or checksum.is_symlink():
        raise ValueError("Existing release artifact/checksum preserved: " + str(artifact))
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".remchat-package-", dir=str(output)) as temporary:
        stage = Path(temporary)
        shutil.copytree(payload / "desktop-app", stage / "desktop-app", symlinks=True)
        if not (payload / "licenses").is_dir() or (payload / "licenses").is_symlink():
            raise ValueError("Missing third-party notices; run scripts/collect_runtime_licenses.py with the build Python")
        shutil.copytree(payload / "licenses", stage / "licenses")
        (stage / "config").mkdir()
        shutil.copy2(payload / "config/rem_companion.toml", stage / "config/rem_companion.toml")
        # Program-owned public download metadata is separate from user settings.
        # validate() binds it to the review hash before and after copying.
        if (payload / "config/release.json").is_file():
            shutil.copy2(payload / "config/release.json", stage / "config/release.json")
        installer = stage / "installer"
        installer.mkdir()
        for name in ("install-desktop.py", "install_files.py"):
            shutil.copy2(scripts / name, installer / name)
        # One native IPC source, copied as a package input rather than maintained twice.
        project = scripts.parents[2]
        for filename in ("README.md", "README.en.md", "README.ja.md", "LICENSE"):
            shutil.copy2(project / filename, stage / filename)
        shutil.copy2(project / "app/remcompanion/platforms/linux/instance.py", installer / "instance.py")
        shutil.copy2(scripts.parent / "Install-Desktop.sh", stage / "Install.sh")
        shutil.copy2(scripts.parent / "Uninstall-Desktop.sh", stage / "Uninstall.sh")
        (stage / "Install.sh").chmod(0o755)
        (stage / "Uninstall.sh").chmod(0o755)
        review = "local-build-review.json" if args.private_local else "public-release-review.json"
        shutil.copy2(payload / review, stage / review)
        validate(stage, private=args.private_local)  # verify the actual copied bytes/links before archiving
        files = {path.relative_to(stage).as_posix(): fingerprint(path)
                 for path in sorted(stage.rglob("*")) if path.is_file() or path.is_symlink()}
        metadata = {"schema": 1, "platform": "linux", "architecture": platform.machine(), "version": args.version,
                    "visibility": "private-local" if args.private_local else "public", "files": files}
        (stage / "release.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        archive = stage / "artifact.tmp"
        with tarfile.open(str(archive), "w:gz", dereference=False) as stream:
            for path in sorted(stage.iterdir()):
                if path != archive:
                    stream.add(str(path), arcname=path.name)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        # x/exclusive creation also protects another builder winning after preflight.
        with artifact.open("xb") as destination, archive.open("rb") as source:
            shutil.copyfileobj(source, destination)
        with checksum.open("x", encoding="ascii") as destination:
            destination.write(digest + "  " + artifact.name + "\n")
    print(str(artifact))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print("Linux packaging failed: " + str(error), file=sys.stderr)
        sys.exit(1)
