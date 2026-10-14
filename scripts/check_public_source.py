"""Check source-only publication without displaying private matches.

Use --tracked to inspect the Git index, including staged contents. Before Git
initialization the same option falls back to a full walk, including hidden and
ignored files. This is a publication guard, not an exhaustive secret detector.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys


MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
PRIVATE_DIRECTORIES = {
    ".aws", ".codex", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".tox", ".venv", ".vscode", "__pycache__", "artifacts", "assets", "audio-cache",
    "backups", "build", "cache", "credentials", "data", "desktop-app",
    "dist", "engines", "logs", "models", "releases", "remchat-private",
    "node_modules", "private", "reports", "resources", "runtime", "sources",
    "tmp", "training", "vault", "venv", "voice-source",
}
TEXT_SUFFIXES = {
    ".c", ".cmd", ".cpp", ".cxx", ".example", ".h", ".ini", ".json",
    ".iss", ".md", ".ps1", ".py", ".sh", ".spec", ".toml", ".txt", ".yaml", ".yml",
}
TEXT_NAMES = {
    ".editorconfig", ".gitattributes", ".gitignore", "authors", "codeowners",
    "contributors", "copying", "license", "makefile", "notice",
}
PRIVATE_FILES = {
    "config/rem_companion.toml", "config/tts_infer_rem.yaml",
}
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
HOME_PATTERN = re.compile(
    r"(?:[A-Z]:[\\/]+Users[\\/]+|/(?:home|Users)/)([A-Z0-9_.-]+)", re.I
)
PLACEHOLDER_USERS = {"example", "user", "username", "your-user", "public"}
CONTENT_RULES = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "api-token": re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b"),
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    "cloud-access-key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "service-token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "jwt-token": re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{16,}\b"),
    "credential-in-url": re.compile(r"https?://[^/\s:@]+:[^/@\s]+@", re.I),
    "private-network-address": re.compile(
        r"\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"
    ),
}
LITERAL_CREDENTIAL = re.compile(
    r"\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|authorization)"
    r"\b[\"']?\s*[:=]\s*([\"'])([^\"'\r\n]{8,})\1", re.I
)
PLACEHOLDER_CREDENTIAL = re.compile(
    r"^(?:YOUR_[A-Z0-9_]+|REPLACE_[A-Z0-9_]+|[A-Z][A-Z0-9_]*_(?:ENV|API_KEY|TOKEN|SECRET|PASSWORD|AUTHORIZATION)|<[^>]+>|\$\{[^}]+\})$"
    r"|(?i:^(?:example|dummy|placeholder|not-a-real)[-_ ])"
)


def path_category(relative: str) -> str | None:
    path = PurePosixPath(relative)
    if (
        path.is_absolute() or "\\" in relative or not path.parts
        or any(part in {".", ".."} for part in path.parts)
        or any(ord(char) < 32 or ord(char) == 127 for char in relative)
    ):
        return "invalid-path"
    lower = relative.lower()
    if any(part.lower() in PRIVATE_DIRECTORIES or part.lower() == ".git" for part in path.parts[:-1]):
        return "private-directory"
    if lower.startswith("app/remcompanion/assets/") or lower.startswith(
        ("docs/task-logs/", "docs/plans/", "docs/engineering/", "docs/superpowers/")
    ):
        return "private-resource-or-record"
    name = path.name.lower()
    if lower in PRIVATE_FILES or (name.startswith(".env") and name != ".env.example"):
        return "live-configuration"
    if name.startswith(("id_rsa", "id_ed25519")) or name in {"secrets.json", "credentials.json"}:
        return "credential-file"
    if path.suffix.lower() not in TEXT_SUFFIXES and name not in TEXT_NAMES:
        return "non-source-file"
    return None


def content_categories(relative: str, raw: bytes) -> set[str]:
    if len(raw) > MAX_FILE_BYTES:
        return {"oversized-source-file"}
    if b"\0" in raw:
        return {"binary-content"}
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError:
        return {"non-utf8-content"}
    categories = {name for name, pattern in CONTENT_RULES.items() if pattern.search(text)}
    for match in EMAIL_PATTERN.finditer(text):
        domain = match.group(0).rsplit("@", 1)[1].lower()
        if domain not in {"users.noreply.github.com", "example.com", "example.org", "example.net"}:
            categories.add("personal-email")
    if any(match.group(1).lower() not in PLACEHOLDER_USERS for match in HOME_PATTERN.finditer(text)):
        categories.add("personal-home-path")
    if any(not PLACEHOLDER_CREDENTIAL.search(match.group(2)) for match in LITERAL_CREDENTIAL.finditer(text)):
        categories.add("literal-credential")
    if relative.lower().endswith(".py"):
        try:
            ast.parse(text, filename=relative)
        except (SyntaxError, ValueError):
            categories.add("python-syntax")
    return categories


def display_path(relative: str) -> str:
    """A filename can itself contain a credential; do not disclose it either."""
    relative = EMAIL_PATTERN.sub("[redacted-email]", relative)
    for pattern in CONTENT_RULES.values():
        relative = pattern.sub("[redacted]", relative)
    return relative


def is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def walk_error(error: OSError) -> None:
    raise error


def walk_entries(root: Path):
    """Do not follow links or read private directories, even if Git ignores them."""
    for base, directories, files in os.walk(root, topdown=True, followlinks=False, onerror=walk_error):
        for name in sorted(directories):
            path = Path(base) / name
            relative = path.relative_to(root).as_posix()
            if relative == ".git":
                directories.remove(name)
                continue
            category = None
            if is_link(path):
                category = "symlink-or-reparse-point"
            elif name.lower() in PRIVATE_DIRECTORIES or name.lower() == ".git":
                category = "private-directory"
            elif relative.lower() == "app/remcompanion/assets":
                category = "private-resource-or-record"
            if category:
                directories.remove(name)
                yield relative, None, category
        for name in sorted(files):
            path = Path(base) / name
            relative = path.relative_to(root).as_posix()
            if relative == ".git":  # Git worktree metadata is not a publication file.
                continue
            category = path_category(relative)
            if is_link(path):
                category = "symlink-or-reparse-point"
            elif not stat.S_ISREG(path.lstat().st_mode):
                category = "non-regular-file"
            elif path.stat().st_size > MAX_FILE_BYTES:
                category = "oversized-source-file"
            yield relative, None if category else path.read_bytes(), category


def git_output(root: Path, *arguments: str, data: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(root), *arguments],
        input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=True,
    )
    return result.stdout


def tracked_entries(root: Path):
    """Read staged blobs, not possibly different working-tree file contents."""
    records = git_output(root, "ls-files", "--stage", "-z").split(b"\0")
    accepted = []
    for record in records:
        if not record:
            continue
        header, name = record.split(b"\t", 1)
        mode, oid, stage = header.split()
        try:
            relative = name.decode("utf-8")
        except UnicodeError:
            yield "[undecodable path]", None, "invalid-path"
            continue
        category = path_category(relative)
        if stage != b"0":
            category = "unmerged-index"
        elif mode == b"120000":
            category = "symlink-or-reparse-point"
        elif mode not in {b"100644", b"100755"}:
            category = "non-source-git-object"
        if not category:
            path = root / relative
            try:
                if any(is_link(parent) for parent in (path, *path.parents) if parent != root and root in parent.parents):
                    category = "symlink-or-reparse-point"
            except OSError:
                category = "missing-or-unreadable-worktree-file"
        if category:
            yield relative, None, category
        else:
            accepted.append((relative, oid))
    if not accepted:
        yield "[index]", None, "empty-source-index"
        return
    requests = b"\n".join(oid for _, oid in accepted) + b"\n"
    metadata = git_output(root, "cat-file", "--batch-check", data=requests).splitlines()
    safe = []
    total = 0
    if len(metadata) != len(accepted):
        raise ValueError("Invalid Git object metadata")
    for (relative, oid), line in zip(accepted, metadata):
        actual_oid, kind, size_text = line.split()
        size = int(size_text)
        if actual_oid != oid or kind != b"blob" or size < 0:
            raise ValueError("Invalid Git source object")
        if size > MAX_FILE_BYTES:
            yield relative, None, "oversized-source-file"
        else:
            total += size
            safe.append((relative, oid, size))
    if total > MAX_TOTAL_BYTES:
        yield "[index]", None, "oversized-source-tree"
        return
    blobs = git_output(root, "cat-file", "--batch", data=b"\n".join(oid for _, oid, _ in safe) + b"\n") if safe else b""
    offset = 0
    for relative, oid, size in safe:
        end = blobs.find(b"\n", offset)
        if end < 0 or blobs[offset:end] != oid + b" blob " + str(size).encode("ascii"):
            raise ValueError("Invalid Git blob header")
        offset = end + 1
        raw = blobs[offset:offset + size]
        offset += size
        if len(raw) != size or blobs[offset:offset + 1] != b"\n":
            raise ValueError("Invalid Git blob length")
        offset += 1
        yield relative, raw, None
    if offset != len(blobs):
        raise ValueError("Unexpected Git blob data")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--tracked", action="store_true", help="Scan staged Git objects; walk before Git initialization.")
    args = parser.parse_args(argv)
    findings = set()
    count = total = 0
    mode = "walk"
    try:
        if is_link(args.root):
            findings.add(("[root]", "symlink-or-reparse-point"))
        else:
            root = args.root.resolve(strict=True)
            mode = "index" if args.tracked and (root / ".git").exists() else "walk"
            entries = tracked_entries(root) if mode == "index" else walk_entries(root)
            for relative, raw, category in entries:
                count += 1
                if category:
                    findings.add((relative, category))
                    continue
                total += len(raw)
                if total > MAX_TOTAL_BYTES:
                    findings.add(("[tree]", "oversized-source-tree"))
                    break
                findings.update((relative, item) for item in content_categories(relative, raw))
    except (OSError, ValueError, subprocess.SubprocessError):
        findings.add(("[tree]", "source-scan-incomplete"))
    if findings:
        for relative, category in sorted(findings):
            print(json.dumps({"path": display_path(relative), "category": category}, ensure_ascii=True, sort_keys=True))
        print(f"Public-source gate failed: {len(findings)} finding(s). No match contents were displayed.")
        return 1
    if count == 0:
        print(json.dumps({"path": "[tree]", "category": "empty-source-tree"}))
        return 1
    print(f"Public-source gate passed: {count} source files checked ({mode}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
