"""Validate a Linux public payload before tar touches the output directory (Python 3.8+)."""
import hashlib
import json
from pathlib import Path
import sys


def validate(root, private=False):
    bundle = root / "desktop-app"
    config = root / "config/rem_companion.toml"
    if root.is_symlink() or bundle.is_symlink() or config.parent.is_symlink() or config.is_symlink():
        raise ValueError("Linked release roots/configuration are not supported")
    bundle_root = bundle.resolve(strict=True)
    for path in bundle.rglob("*"):
        if path.suffix.lower() in {".exe", ".dll", ".pyd"}:
            raise ValueError(f"Windows runtime found in Linux payload: {path}")
        if path.is_symlink():
            # PyInstaller uses relative links for shared libraries. Only links
            # resolving to a real file inside this exact bundle are portable.
            import os
            if Path(os.readlink(path)).is_absolute():
                raise ValueError(f"Absolute package link: {path}")
            target = path.resolve(strict=True)
            if bundle_root not in target.parents or not target.is_file():
                raise ValueError(f"Escaping or non-file package link: {path}")
        elif not (path.is_file() or path.is_dir()):
            raise ValueError(f"Special filesystem node in package: {path}")
    review_path = root / ("local-build-review.json" if private else "public-release-review.json")
    if review_path.is_symlink():
        raise ValueError("Linked release review is not supported")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(config.read_bytes()).hexdigest()
    if private:
        exe_digest = hashlib.sha256((bundle / "RemChat").read_bytes()).hexdigest()
        approved = (review.get("schema") == 1 and review.get("local_only") is True and review.get("reviewer")
                    and str(review.get("config_sha256", "")).lower() == digest
                    and str(review.get("exe_sha256", "")).lower() == exe_digest)
    else:
        approved = (review.get("schema") == 1 and review.get("config_public") is True
            and review.get("assets_redistributable") is True and review.get("reviewer")
            and str(review.get("config_sha256", "")).lower() == digest)
    if not approved:
        raise ValueError("Unapproved configuration/assets or configuration hash mismatch")


if __name__ == "__main__":
    try:
        validate(Path(sys.argv[1]), private="--private-local" in sys.argv[2:])
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Linux release validation failed: {error}", file=sys.stderr)
        sys.exit(1)
