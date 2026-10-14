"""Local composer tokens and explicit bounded references; no model or shell calls."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import os
import re
import stat

from .workspace_context import Attachment, ContextSnapshot, read_attachment


@dataclass(frozen=True)
class InputToken:
    start: int
    end: int
    prefix: str
    query: str


def active_token(text: str, cursor: int) -> InputToken | None:
    """Token at the caret, not a URL, email or a token already followed by space."""
    cursor = min(max(0, cursor), len(text))
    match = re.search(r'(?<!\S)([@/])([^\s@/]*|"[^"\n]*)$', text[:cursor])
    # File queries may contain slashes; command queries may not.
    file_match = re.search(r'(?<!\S)@([^\s@]*|"[^"\n]*)$', text[:cursor])
    if file_match and (not match or file_match.start() >= match.start()):
        query = file_match.group(1)
        if query.startswith('"') and query.endswith('"') and len(query) > 1:
            return None
        suffix = text[cursor:]
        if query.startswith('"'):
            closing = suffix.find('"')
            end = cursor + closing + 1 if closing >= 0 else cursor + len(suffix.split('\n', 1)[0])
        else:
            tail = re.match(r'[^\s]*', suffix)
            end = cursor + len(tail.group(0))
        return InputToken(file_match.start(), end, '@', query.lstrip('"'))
    if not match:
        return None
    tail = re.match(r'[^\s/]*', text[cursor:])
    return InputToken(match.start(), cursor + len(tail.group(0)), match.group(1), match.group(2))


def checked_workspace_file(workspace: Path | None, name: str) -> Path:
    if workspace is None:
        raise ValueError("请先选择工作空间，再使用 @ 文件引用")
    relative = Path(name)
    if not name or relative.is_absolute() or relative.drive or '\\' == name[:1] or '..' in relative.parts:
        raise ValueError("@ 只能引用工作空间内的普通文件")
    root = workspace.resolve(strict=True)
    candidate = root / relative
    for item in (candidate, *candidate.parents):
        if item == root:
            break
        metadata = item.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400:
            raise ValueError("@ 文件引用不跟随链接或目录联接")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("@ 只能引用工作空间内的普通文件")
    return resolved


def prepare_references(text: str, context: ContextSnapshot, *, workspace: Path | None,
                       memory, skills, root_path: Path) -> ContextSnapshot:
    """Explicit refs use the same five-item / 12000-char limit as attachments."""
    items = list(context.attachments)
    known = {item.path for item in items}
    skill_references = list(context.skill_references)
    for match in re.finditer(r'(?<!\S)@(?:"([^"\n]+)"|([^\s]+))', text):
        name = match.group(1) or match.group(2)
        if name.startswith('chat:'):
            try:
                identifier = int(name[5:])
            except ValueError as error:
                raise ValueError("会话引用无效，请从 @ 列表重新选择") from error
            key = root_path / 'data' / '.conversation-reference' / str(identifier)
            if key in known:
                continue
            if not memory.conversation_exists(identifier):
                raise ValueError("引用的会话已删除，请重新选择")
            page = memory.history_page(identifier, limit=20)
            if not page.messages:
                raise ValueError("引用会话没有可发送的消息")
            body = '仅引用该会话最近 20 条消息；不是完整会话。\n' if page.has_more else '所选会话消息快照。\n'
            body += '\n\n'.join(f'{item.role}: {item.content}' for item in page.messages)
            item = Attachment(key, f'会话 #{identifier} · 最近 20 条', text=body)
        else:
            key = checked_workspace_file(workspace, name)
            if key in known:
                continue  # Preserve a previously selected file snapshot.
            item = read_attachment(key, workspace=workspace)
        items.append(item)
        known.add(item.path)
    skill_match = re.match(r'^/skill:([a-zA-Z0-9_:-]+)(?=\s|$)', text)
    if skill_match:
        identifier = skill_match.group(1)
        row = skills.read(identifier)
        if not row.get('enabled'):
            raise ValueError("技能尚未启用，请先阅读并在技能库允许参考")
        key = root_path / row['path'] / 'SKILL.md'
        if key not in known:
            items.append(Attachment(key, f"技能 {identifier} · 仅参考，不执行脚本", text=row['text']))
        if identifier not in skill_references:
            skill_references.append(identifier)
    # Preparation also runs for unsent previews. Carry identifiers to the
    # validated worker without publishing telemetry or losing mode constraints.
    return replace(context, attachments=tuple(items), skill_references=tuple(skill_references))


SKIPPED_DIRECTORIES = {'.git', '.svn', 'node_modules', '__pycache__', '.venv', 'venv',
                       'runtime', 'models', 'engines', 'cache', 'releases', 'backups', 'data'}


def workspace_names(workspace: Path, stop, *, limit: int = 1500) -> tuple[str, ...]:
    """Bounded background name-only walk. Never scans until a workspace is chosen."""
    from .workspace_context import TEXT_EXTENSIONS, IMAGE_EXTENSIONS, SECRET_FILENAMES
    root = workspace.resolve(strict=True)
    names = []
    visited = 0
    for folder, dirs, files in os.walk(root, followlinks=False):
        if stop.is_set():
            break
        relative = Path(folder).relative_to(root)
        kept = []
        if len(relative.parts) < 8:
            for name in sorted(dirs):
                visited += 1
                path = Path(folder) / name
                if name.startswith('.') or name.lower() in SKIPPED_DIRECTORIES:
                    continue
                try:
                    if not (getattr(path.lstat(), 'st_file_attributes', 0) & 0x400) and not path.is_symlink():
                        kept.append(name)
                except OSError:
                    continue
        dirs[:] = kept
        for name in sorted(files):
            if stop.is_set():
                return tuple(names)
            visited += 1
            if visited >= limit * 4 or len(names) >= limit:
                return tuple(names)
            path = Path(folder) / name
            lower = name.lower()
            if lower.startswith('.env') or lower in SECRET_FILENAMES or path.suffix.lower() not in TEXT_EXTENSIONS | IMAGE_EXTENSIONS:
                continue
            try:
                if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
                    continue
                label = path.relative_to(root).as_posix()
                if '"' not in label and '\n' not in label:
                    names.append(label)
            except OSError:
                continue
        if visited >= limit * 4:
            break
    return tuple(names)
