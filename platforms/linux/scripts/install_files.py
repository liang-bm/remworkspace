"""Linux installer file ownership and reversible writes (Python 3.8+ stdlib)."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import uuid


def fingerprint(path):
    if path.is_symlink():
        return {"kind": "link", "target": os.readlink(path)}
    if path.is_file():
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return {"kind": "file", "sha256": digest.hexdigest()}
    if path.exists():
        raise ValueError("Not a regular file: " + str(path))
    return None


def reject_linked_parents(path):
    for parent in [path] + list(path.parents):
        if parent.is_symlink():
            raise ValueError("Linked installation path: " + str(parent))


def program_path(root, relative):
    parts = PurePosixPath(relative).parts
    allowed = {
        "Install.sh", "Uninstall.sh", "release.json", "public-release-review.json", "local-build-review.json",
        "README.md", "README.en.md", "README.ja.md", "LICENSE", "config/release.json",
    }
    if (not parts or ".." in parts or "." in parts or relative != PurePosixPath(relative).as_posix()
            or (parts[0] not in {"desktop-app", "installer"} and relative not in allowed)):
        raise ValueError("Unowned program path: " + str(relative))
    path = root.joinpath(*parts)
    reject_linked_parents(path.parent)
    return path


def copy_file(source, destination):
    """Copy bytes or link text, never follow a leaf link into another directory."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        destination.symlink_to(os.readlink(source))
    else:
        shutil.copy2(source, destination)
        destination.chmod(0o755 if source.stat().st_mode & 0o111 else 0o644)


def atomic_copy(source, destination):
    reject_linked_parents(destination.parent)
    temporary = destination.with_name(".remchat-new-" + uuid.uuid4().hex)
    try:
        copy_file(source, temporary)
        os.replace(str(temporary), str(destination))
    finally:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()


def atomic_json(path, value):
    reject_linked_parents(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".remchat-json-" + uuid.uuid4().hex)
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()


class Transaction:
    """Back up exact changed files. Never recursively remove an installation root.

    A surviving pending journal means an interrupted transaction; a subsequent
    installer refuses to proceed until its local backup has been reviewed.
    """
    def __init__(self, root):
        self.root = root
        self.control = root / ".remchat-install"
        reject_linked_parents(self.control)
        self.pending = self.control / "pending.json"
        if self.pending.exists() or self.pending.is_symlink():
            raise ValueError("Interrupted installation preserved; inspect " + str(self.pending))
        self.backup = self.control / "backups" / uuid.uuid4().hex
        reject_linked_parents(self.backup)
        self.backup.mkdir(parents=True)
        self.entries = []
        self.planned_paths = set()
        self.applied_paths = set()
        self.created_dirs = set()
        self._journal()

    def _journal(self):
        atomic_json(self.pending, {"schema": 1, "root": str(self.root), "backup": str(self.backup), "entries": self.entries})

    def prepare(self, paths):
        """Persist all prior states before the first change, in one journal write.

        Rewriting an ever-growing journal per file was quadratic and noticeably
        slow on WSL/DrvFS. The whole write set is known before installation.
        """
        for path in paths:
            if str(path) in self.planned_paths:
                continue
            self._save_prior(path)
            self.planned_paths.add(str(path))
        self._journal()

    def _save_prior(self, path):
        reject_linked_parents(path.parent)
        prior = fingerprint(path)
        index = len(self.entries)
        saved = self.backup / str(index)
        if prior is not None:
            copy_file(path, saved)
        parent = path.parent
        while not parent.exists():
            self.created_dirs.add(parent)
            parent = parent.parent
        self.entries.append({"path": str(path), "prior": prior, "saved": str(saved)})

    def _track(self, path):
        if str(path) not in self.planned_paths:
            raise ValueError("Write was not in the persisted installation plan: " + str(path))
        self.applied_paths.add(str(path))

    def copy(self, source, path):
        self._track(path)
        atomic_copy(source, path)

    def write(self, path, content):
        self._track(path)
        source = self.backup / ("new-" + str(len(self.entries)))
        source.write_text(content, encoding="utf-8")
        source.chmod(0o644)
        atomic_copy(source, path)

    def remove(self, path):
        self._track(path)
        path.unlink()

    def finish(self):
        atomic_json(self.backup / "transaction.json", {"status": "committed", "root": str(self.root), "entries": self.entries})
        self.pending.unlink()

    def rollback(self):
        for entry in reversed(self.entries):
            if entry["path"] not in self.applied_paths:
                continue
            path = Path(entry["path"])
            reject_linked_parents(path.parent)
            if entry["prior"] is None:
                if path.exists() or path.is_symlink():
                    path.unlink()
            else:
                atomic_copy(Path(entry["saved"]), path)
        for directory in sorted(self.created_dirs, key=lambda item: len(item.parts), reverse=True):
            try:
                directory.rmdir()  # empty only; unknown user contents are retained
            except OSError:
                pass
        atomic_json(self.backup / "transaction.json", {"status": "rolled-back", "root": str(self.root), "entries": self.entries})
        self.pending.unlink()
