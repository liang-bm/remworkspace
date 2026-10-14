"""Local, original-Markdown diary storage and explicit interchange.

No models, uploads, content inference or irreversible deletion. Saves use a
revision check so another window cannot quietly overwrite an edited entry.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from uuid import uuid4


MAX_BODY_BYTES = 2 * 1024 * 1024
MOODS = ("", "开心", "平静", "疲惫", "低落", "期待")
_HEADER = "<!-- RemChatDiary "


class DiaryError(ValueError):
    """An entry/import failed validation without replacing existing data."""


class DiaryConflict(DiaryError):
    """The expected revision no longer matches the stored entry."""


@dataclass(frozen=True)
class DiaryEntry:
    id: str
    day: str
    title: str
    body: str
    tags: tuple[str, ...] = ()
    mood: str = ""
    created_at: str = ""
    updated_at: str = ""
    deleted_at: str | None = None
    revision: int = 0
    rich: str = ""


def _day(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise DiaryError("日期请填写 YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise DiaryError("这个日期不存在") from error
    return value


def normalize_tags(values) -> tuple[str, ...]:
    if isinstance(values, str):
        values = re.split(r"[,，\s]+", values)
    if not isinstance(values, (list, tuple)):
        raise DiaryError("标签应为文本列表")
    result = []
    for value in values:
        if not isinstance(value, str):
            raise DiaryError("标签应为文本")
        value = value.strip().lstrip("#")
        if not value:
            continue
        if len(value) > 48 or any(ord(character) < 32 for character in value):
            raise DiaryError("单个标签最多 48 字，不能包含控制字符")
        if value.casefold() not in {tag.casefold() for tag in result}:
            result.append(value)
    if len(result) > 32:
        raise DiaryError("每篇日记最多 32 个标签")
    return tuple(result)


def _fields(day, title, body, tags, mood):
    day = _day(day)
    if not isinstance(title, str) or len(title) > 240 or "\n" in title or "\r" in title:
        raise DiaryError("标题最多 240 字，不能换行")
    if not isinstance(body, str):
        raise DiaryError("日记正文应为文本")
    if len(body.encode("utf-8")) > MAX_BODY_BYTES or "\x00" in body:
        raise DiaryError("正文最多 2 MiB，不能含空字符")
    if mood not in MOODS:
        raise DiaryError("不支持这个心情值")
    return day, title.strip(), body, normalize_tags(tags), mood


def _stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def _linkish(path: Path) -> bool:
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        return True
    try:
        return bool(getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400)
    except FileNotFoundError:
        return False


def _safe_file(path: Path) -> None:
    """Do not follow a substituted diary database/output link."""
    if _linkish(path) or (path.exists() and not path.is_file()):
        raise OSError("目标不是普通文件，已停止写入")


class DiaryStore:
    def __init__(self, project_root: Path):
        root = Path(project_root).absolute()
        self.root = root.resolve()
        self._closed = False
        self._anchor = None
        self.path = root / "data" / "plaza" / "diary.sqlite3"
        # Only the computed diary ancestors are checked. The user-selected
        # project root itself is allowed to be a legitimate mounted volume.
        for parent in (root / "data", root / "data/plaza"):
            if _linkish(parent) or (parent.exists() and not parent.is_dir()) or not parent.resolve().is_relative_to(self.root):
                raise OSError("日记目录不是普通目录，已停止写入")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise DiaryError("日记库版本较新；请更新软件后再打开")
            # WAL gives streaming exports a consistent read snapshot without
            # holding up edits. It applies only to this independent diary DB.
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""CREATE TABLE IF NOT EXISTS diary_entries (
                id TEXT PRIMARY KEY, day TEXT NOT NULL, title TEXT NOT NULL,
                body TEXT NOT NULL, tags TEXT NOT NULL, mood TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                deleted_at TEXT, revision INTEGER NOT NULL)""")
            connection.execute("CREATE INDEX IF NOT EXISTS diary_day ON diary_entries(day, deleted_at)")
            if 'rich' not in {row[1] for row in connection.execute('PRAGMA table_info(diary_entries)')}:
                connection.execute("ALTER TABLE diary_entries ADD COLUMN rich TEXT NOT NULL DEFAULT ''")
            connection.execute('CREATE TABLE IF NOT EXISTS diary_templates (id TEXT PRIMARY KEY, name TEXT NOT NULL, body TEXT NOT NULL, weekdays TEXT NOT NULL, tasks TEXT NOT NULL, enabled INTEGER NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS diary_daily_tasks (day TEXT NOT NULL, id TEXT NOT NULL, text TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL, PRIMARY KEY(day,id))')
            connection.execute("PRAGMA user_version=2")
        # Keep the WAL environment open for this store's lifetime. Otherwise
        # the last short operation tears down/rebuilds -wal/-shm concurrently
        # with another connection and creates spurious BUSY errors.
        self._anchor = sqlite3.connect(str(self.path), timeout=0.0)
        self._anchor.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()

    @contextmanager
    def _connect(self):
        # One connection per operation: workers never lend a connection or
        # a Python mutex to Tk. SQLite transactions own concurrency; a writer
        # lock reports quickly instead of freezing the main UI indefinitely.
        if self._closed:
            raise DiaryError("日记库已关闭")
        for parent in (self.path.parent, self.path.parent.parent):
            if _linkish(parent) or not parent.resolve().is_relative_to(self.root):
                raise OSError("日记目录已被替换为链接，已停止写入")
        _safe_file(self.path)
        for suffix in ("-wal", "-shm", "-journal"):
            _safe_file(self.path.with_name(self.path.name + suffix))
        connection = sqlite3.connect(str(self.path), timeout=0.0)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def close(self):
        self._closed = True
        if self._anchor is not None:
            self._anchor.close()
            self._anchor = None

    @staticmethod
    def _entry(row) -> DiaryEntry:
        try:
            tags = json.loads(row["tags"])
            if not isinstance(tags, list):
                raise DiaryError("日记库中的标签格式损坏")
            _fields(row["day"], row["title"], row["body"], tags, row["mood"])
            if not isinstance(row["revision"], int) or row["revision"] < 1:
                raise DiaryError("日记库中的版本信息损坏")
            from .journal_document import validate_document
            validate_document(row['rich'])
        except (ValueError, TypeError, UnicodeError) as error:
            raise DiaryError("日记条目无法读取；原记录未改动：" + str(error)) from error
        return DiaryEntry(**{**dict(row), "tags": tuple(tags)})

    def get(self, entry_id: str) -> DiaryEntry | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM diary_entries WHERE id=?", (entry_id,)).fetchone()
        return self._entry(row) if row else None

    def save(self, *, day: str, title: str, body: str, tags=(), mood: str = "",
             entry_id: str | None = None, expected_revision: int = 0, rich: str = "",
             created_at: str | None = None) -> DiaryEntry:
        day, title, body, tags, mood = _fields(day, title, body, tags, mood)
        from .journal_document import validate_document
        validate_document(rich)
        stamp = _stamp()
        if created_at is not None:
            if entry_id is not None or not isinstance(created_at,str) or len(created_at)>40:
                raise DiaryError('导入记录的时间不合法')
            try:created_at=datetime.fromisoformat(created_at).isoformat(timespec='microseconds')
            except ValueError:raise DiaryError('导入记录的时间不合法') from None
        with self._connect() as connection:
            if entry_id is None:
                entry_id = uuid4().hex
                connection.execute("INSERT INTO diary_entries (id,day,title,body,tags,mood,created_at,updated_at,deleted_at,revision,rich) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                   (entry_id, day, title, body, json.dumps(tags, ensure_ascii=False), mood,
                                    created_at or stamp, stamp, None, 1, rich))
            else:
                changed = connection.execute("""UPDATE diary_entries SET day=?, title=?, body=?,
                    tags=?, mood=?, updated_at=?, rich=?, revision=revision+1
                    WHERE id=? AND revision=? AND deleted_at IS NULL""",
                    (day, title, body, json.dumps(tags, ensure_ascii=False), mood, stamp, rich,
                     entry_id, expected_revision)).rowcount
                if changed != 1:
                    raise DiaryConflict("这篇日记已在其他窗口修改或移入回收站；当前草稿仍保留，可导出后重新打开")
            row = connection.execute("SELECT * FROM diary_entries WHERE id=?", (entry_id,)).fetchone()
        return self._entry(row)

    def set_deleted(self, entry_id: str, *, deleted: bool, expected_revision: int) -> DiaryEntry:
        with self._connect() as connection:
            changed = connection.execute("""UPDATE diary_entries SET deleted_at=?, updated_at=?,
                revision=revision+1 WHERE id=? AND revision=?""",
                (_stamp() if deleted else None, _stamp(), entry_id, expected_revision)).rowcount
            if changed != 1:
                raise DiaryConflict("日记已被其他窗口修改，请重新读取列表")
            row = connection.execute("SELECT * FROM diary_entries WHERE id=?", (entry_id,)).fetchone()
        return self._entry(row)

    def query(self, *, text: str = "", day: str | None = None, tag: str = "",
              mood: str = "", deleted: bool = False, limit: int | None = 5000,
              summaries: bool = False, cancelled=None) -> list[DiaryEntry]:
        if day:
            _day(day)
        clauses = ["deleted_at IS NOT NULL" if deleted else "deleted_at IS NULL"]
        arguments = []
        if day:
            clauses.append("day=?")
            arguments.append(day)
        if text.strip():
            # instr is literal (not wildcard LIKE), so %/_ remain searchable.
            clauses.append("(instr(lower(title),lower(?)) OR instr(lower(body),lower(?)) OR instr(day,?))")
            arguments.extend((text.strip(),) * 3)
        if mood:
            clauses.append("mood=?")
            arguments.append(mood)
        # Tags are bounded and exact matching follows query; no fragile JSON
        # substring match where 'work' unexpectedly includes 'homework'.
        with self._connect() as connection:
            if cancelled:
                connection.set_progress_handler(lambda: int(bool(cancelled())), 1000)
            columns = "id,day,title,'' AS body,tags,mood,created_at,updated_at,deleted_at,revision,'' AS rich" if summaries else "*"
            rows = connection.execute("SELECT " + columns + " FROM diary_entries WHERE " + " AND ".join(clauses)
                                      + " ORDER BY day DESC, updated_at DESC", arguments)
            result = []
            for row in rows:
                if cancelled and cancelled():
                    raise DiaryError("日记读取已停止")
                entry = self._entry(row)
                if not tag or tag.casefold() in {value.casefold() for value in entry.tags}:
                    result.append(entry)
                if limit is not None and len(result) >= max(1, min(int(limit), 5000)):
                    break
        return result

    def search(self, text, *, tag='', deleted=False, limit=200, cancelled=None):
        """Stream candidates in a worker; no schema migration or UI-thread scan."""
        from .journal_search import find_entries
        def candidates():
            for entry in self.iter_entries(deleted=deleted, cancelled=cancelled):
                if not tag or tag.casefold() in {value.casefold() for value in entry.tags}:
                    yield entry
        return find_entries(candidates(), text, limit=limit, cancelled=cancelled or (lambda: False))

    def iter_entries(self, *, deleted: bool = False, cancelled=None):
        """Stream an export snapshot, at most one complete body in this iterator."""
        with self._connect() as connection:
            if cancelled:
                connection.set_progress_handler(lambda: int(bool(cancelled())), 1000)
            rows = connection.execute("SELECT * FROM diary_entries WHERE deleted_at "
                                      + ("IS NOT NULL" if deleted else "IS NULL")
                                      + " ORDER BY day DESC, updated_at DESC")
            for row in rows:
                if cancelled and cancelled():
                    raise DiaryError("日记导出已停止")
                yield self._entry(row)

    def tags(self, *, deleted: bool = False) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT tags FROM diary_entries WHERE deleted_at "
                                      + ("IS NOT NULL" if deleted else "IS NULL"))
            values = {value for row in rows for value in json.loads(row[0])}
        return tuple(sorted(values, key=str.casefold))

    def month_counts(self, year: int, month: int, *, deleted: bool = False) -> dict[str, int]:
        date(year, month, 1)
        with self._connect() as connection:
            rows = connection.execute("SELECT day, count(*) FROM diary_entries WHERE day LIKE ? AND deleted_at "
                                      + ("IS NOT NULL" if deleted else "IS NULL") + " GROUP BY day",
                                      (f"{year:04d}-{month:02d}-%",))
            return dict(rows)

    def count(self, *, deleted: bool = False) -> int:
        with self._connect() as connection:
            return connection.execute("SELECT count(*) FROM diary_entries WHERE deleted_at "
                                      + ("IS NOT NULL" if deleted else "IS NULL")).fetchone()[0]

    def templates(self):
        with self._connect() as c: rows=c.execute('SELECT * FROM diary_templates ORDER BY name').fetchall()
        return [{**dict(r),'weekdays':json.loads(r['weekdays']),'tasks':json.loads(r['tasks'])} for r in rows]

    def save_template(self, *, name, body='', weekdays=(), tasks=(), template_id=None, enabled=True):
        if not isinstance(name,str) or not name.strip() or len(name)>80: raise DiaryError('模板名称需为 1–80 字')
        if not isinstance(body,str) or len(body.encode('utf-8'))>65536: raise DiaryError('模板正文过长')
        if not isinstance(weekdays,(tuple,list)) or any(type(d) is not int or not 0<=d<7 for d in weekdays): raise DiaryError('请选择有效星期')
        if not isinstance(tasks,(tuple,list)) or len(tasks)>64 or any(not isinstance(t,str) or not t.strip() or len(t)>240 for t in tasks): raise DiaryError('待办每项最多 240 字，最多 64 项')
        template_id=template_id or uuid4().hex
        with self._connect() as c:
            c.execute('INSERT INTO diary_templates VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,body=excluded.body,weekdays=excluded.weekdays,tasks=excluded.tasks,enabled=excluded.enabled',
                (template_id,name.strip(),body,json.dumps(sorted(set(weekdays))),json.dumps(list(tasks),ensure_ascii=False),int(bool(enabled))))
        return template_id

    def delete_template(self, template_id):
        with self._connect() as c: c.execute('DELETE FROM diary_templates WHERE id=?',(template_id,))

    def daily_tasks(self, day, *, materialize=True):
        _day(day)
        if materialize:
            weekday=date.fromisoformat(day).weekday(); templates=self.templates()
            with self._connect() as c:
                for template in templates:
                    if template['enabled'] and weekday in template['weekdays']:
                        for index,text in enumerate(template['tasks']):
                            c.execute('INSERT OR IGNORE INTO diary_daily_tasks VALUES (?,?,?,?,?)',
                                (day,template['id']+':'+str(index),text,0,template['id']))
        with self._connect() as c:
            return [dict(r) for r in c.execute('SELECT * FROM diary_daily_tasks WHERE day=? ORDER BY rowid',(day,))]

    def add_task(self, day, text):
        _day(day)
        if not isinstance(text,str) or not text.strip() or len(text)>240: raise DiaryError('待办需为 1–240 字')
        with self._connect() as c: c.execute('INSERT INTO diary_daily_tasks VALUES (?,?,?,?,?)',(day,uuid4().hex,text.strip(),0,''))

    def check_task(self, day, task_id, done):
        _day(day)
        with self._connect() as c: c.execute('UPDATE diary_daily_tasks SET done=? WHERE day=? AND id=?',(int(bool(done)),day,task_id))

    def import_markdown(self, path: Path) -> DiaryEntry:
        path = Path(path)
        if _linkish(path) or not path.is_file():
            raise DiaryError("请选择一个普通 Markdown 文件")
        with path.open("rb") as stream:
            raw = stream.read(MAX_BODY_BYTES + 16385)
        if len(raw) > MAX_BODY_BYTES + 16384:
            raise DiaryError("导入文件超过 2 MiB 正文限制")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeError as error:
            raise DiaryError("文件不是 UTF-8 文本，请先转换编码") from error
        metadata = {}
        body = text
        if text.startswith(_HEADER):
            first, separator, body = text.partition("\n")
            if not separator or not first.endswith(" -->") or len(first) > 16384:
                raise DiaryError("RemChat 日记元数据不完整，原文件未改动")
            try:
                metadata = json.loads(first[len(_HEADER):-4])
            except ValueError as error:
                raise DiaryError("RemChat 日记元数据无法读取，原文件未改动") from error
            if not isinstance(metadata, dict) or metadata.get("version") != 1:
                raise DiaryError("不支持这个日记文件版本")
            if "tags" in metadata and not isinstance(metadata["tags"], list):
                raise DiaryError("日记元数据中的标签应为列表")
        filename_date = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[ _.-]|$)", path.stem)
        day = metadata.get("day", filename_date.group(1) if filename_date else date.today().isoformat())
        title = metadata.get("title", path.stem)
        return self.save(day=day, title=title, body=body,
                         tags=metadata.get("tags", ()), mood=metadata.get("mood", ""))


def markdown_text(entry: DiaryEntry) -> str:
    """The body after this one metadata line is byte-for-byte original UTF-8."""
    _fields(entry.day, entry.title, entry.body, entry.tags, entry.mood)
    metadata = {"version": 1, "day": entry.day, "title": entry.title,
                "tags": list(entry.tags), "mood": entry.mood}
    value = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
    # Avoid HTML comment terminators while retaining JSON semantics.
    value = value.replace("<", "\\u003c").replace(">", "\\u003e")
    return _HEADER + value + " -->\n" + entry.body


def export_markdown(entry: DiaryEntry, path: Path, *, overwrite: bool = False) -> Path:
    """Atomic explicit export; do not implicitly replace an existing file."""
    path = Path(path).absolute()
    _safe_file(path)
    if _linkish(path.parent) or not path.parent.is_dir():
        raise OSError("导出目录不是普通目录")
    if path.exists() and not overwrite:
        raise FileExistsError("导出文件已经存在，请选择新文件名")
    raw = markdown_text(entry).encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=".remchat-diary-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _safe_file(path)
        if overwrite:
            os.replace(temporary_path, path)
        else:
            # Same-directory hard link publishes a complete file atomically
            # and fails rather than replacing a racing destination. The
            # temporary link is removed below, not the published file.
            os.link(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return path


def export_collection(entries: Iterable[DiaryEntry], directory: Path, *, cancelled=None) -> Path:
    """Create a fresh export folder; never collide with the user's files."""
    directory = Path(directory).absolute()
    if _linkish(directory) or not directory.is_dir():
        raise OSError("导出目录不是普通目录")
    destination = directory / ("RemChatDiary-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8])
    destination.mkdir()
    for entry in entries:
        if cancelled and cancelled():
            raise DiaryError("导出已停止；新文件夹中的已完成文件仍保留")
        title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", entry.title).strip(" .")[:60] or "日记"
        export_markdown(entry, destination / f"{entry.day}-{title}-{entry.id[:8]}.md")
    return destination
