"""SQLite 记忆数据库。

SQLite 是唯一真源；未来的向量索引只保存可重新生成的数据，不反向覆盖原始记忆。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import json
import re
import sqlite3
from typing import Any


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('system', 'user', 'assistant')),
    content TEXT NOT NULL,
    emotion TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    content TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'fact',
    importance REAL NOT NULL DEFAULT 0.5 CHECK(importance BETWEEN 0 AND 1),
    pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0, 1)),
    source_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    last_accessed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    from_message_id INTEGER,
    to_message_id INTEGER,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS voice_refs (
    id INTEGER PRIMARY KEY,
    emotion TEXT NOT NULL,
    audio_path TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'ja',
    quality_score REAL NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    UNIQUE(emotion, audio_path)
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_time
ON messages(conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_id
ON messages(conversation_id, id);

CREATE INDEX IF NOT EXISTS idx_memories_priority
ON memories(pinned DESC, importance DESC, updated_at DESC);
"""


def initialize_memory_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)

    # trigram 对连续中文检索更友好；旧版 SQLite 不支持时回退到 unicode61。
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
            "USING fts5(content, category, content='memories', content_rowid='id', tokenize='trigram')"
        )
    except sqlite3.OperationalError:
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
            "USING fts5(content, category, content='memories', content_rowid='id', tokenize='unicode61')"
        )

    connection.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
          INSERT INTO memory_fts(rowid, content, category)
          VALUES (new.id, new.content, new.category);
        END;
        CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
          INSERT INTO memory_fts(memory_fts, rowid, content, category)
          VALUES ('delete', old.id, old.content, old.category);
        END;
        CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
          INSERT INTO memory_fts(memory_fts, rowid, content, category)
          VALUES ('delete', old.id, old.content, old.category);
          INSERT INTO memory_fts(rowid, content, category)
          VALUES (new.id, new.content, new.category);
        END;
        """
    )
    connection.commit()
    return connection


@dataclass(frozen=True)
class StoredMessage:
    """提供给大模型的最小历史消息结构。"""

    id: int
    role: str
    content: str
    emotion: str | None


@dataclass(frozen=True)
class HistoryPage:
    """UI history, oldest first; independent of the model's context limit."""

    messages: tuple[StoredMessage, ...]
    has_more: bool


@dataclass(frozen=True)
class StoredMemory:
    """一条可长期召回的用户事实或偏好。"""

    id: int
    content: str
    category: str
    importance: float
    pinned: bool


def _memory_edit_where(memory_id: int, expected: StoredMemory | None) -> tuple[str, tuple]:
    """Compare editable fields in the write itself; timestamps are only seconds."""
    if expected is None:
        return "id=?", (memory_id,)
    if expected.id != memory_id:
        raise ValueError("记忆基线与所选 ID 不一致")
    return "id=? AND content=? AND category=? AND pinned=?", (
        memory_id, expected.content, expected.category, int(expected.pinned),
    )


@dataclass(frozen=True)
class MemoryStats:
    """供界面展示的对话历史与长期记忆计数。"""

    message_count: int
    memory_count: int


@dataclass(frozen=True)
class StoredConversation:
    """主聊天端侧栏需要的轻量会话摘要。"""

    id: int
    title: str
    message_count: int


@dataclass(frozen=True)
class MemoryImportPlan:
    """把 VS Code 中的 JSON 应用回 SQLite 前后的变更计数。"""

    create: int
    update: int
    delete: int


SENSITIVE_MEMORY_MARKERS = (
    "密码",
    "口令",
    "令牌",
    "token",
    "api key",
    "apikey",
    "密钥",
    "身份证",
    "银行卡",
)
PREFERENCE_PATTERN = re.compile(
    r"我(?:很|非常|特别|比较|更)?"
    r"(?P<verb>喜欢|偏爱|不喜欢|讨厌)"
    r"(?P<value>[^。！？!?\n]{1,100})"
)
PERSONAL_FACT_PATTERN = re.compile(
    r"我的(?P<name>[^，。！？!?\n]{1,16})(?:是|叫)"
    r"(?P<value>[^，。！？!?\n]{1,80})"
)
REMEMBER_PATTERN = re.compile(
    r"(?:请|麻烦你|帮我)?(?:记住|记得)"
    r"[：:，,\s]*(?P<value>[^。！？!?\n]{2,160})"
)


def _memory_key(content: str) -> str:
    """生成轻量去重键，合并“喜欢…”与“用户喜欢…”等同义前缀差异。"""

    normalized = " ".join(content.strip().split()).lower()
    normalized = re.sub(
        r"^(?:用户)?(?:明确要求记住)?[：:\s]*",
        "",
        normalized,
    )
    return re.sub(r"[\s，,。！？!?：:；;、\"'“”‘’]", "", normalized)


def extract_explicit_memories(text: str) -> list[dict[str, Any]]:
    """确定性提取用户明确表达的偏好、个人事实或“记住”指令。

    本函数只处理高置信度句型，补足小模型偶发漏掉 ``memories`` 字段的情况；
    普通寒暄和短期状态不会进入长期记忆。
    """

    normalized = " ".join(text.strip().split())
    lowered = normalized.lower()
    if not normalized or any(marker in lowered for marker in SENSITIVE_MEMORY_MARKERS):
        return []

    items: list[dict[str, Any]] = []
    for match in PREFERENCE_PATTERN.finditer(normalized):
        verb = match.group("verb")
        value = match.group("value").strip(" ，,：:")
        if not value:
            continue
        positive = verb in {"喜欢", "偏爱"}
        items.append(
            {
                "content": f"用户{'喜欢' if positive else '不喜欢'}{value}",
                "category": "preference",
                "importance": 0.82,
            }
        )
    for match in PERSONAL_FACT_PATTERN.finditer(normalized):
        name = match.group("name").strip()
        value = match.group("value").strip()
        if name and value:
            items.append(
                {
                    "content": f"用户的{name}是{value}",
                    "category": "fact",
                    "importance": 0.78,
                }
            )
    if not items:
        match = REMEMBER_PATTERN.search(normalized)
        if match is not None:
            value = match.group("value").strip(" ，,：:")
            if value:
                items.append(
                    {
                        "content": f"用户明确要求记住：{value}",
                        "category": "explicit",
                        "importance": 0.90,
                    }
                )
    return items


class ConversationMemory:
    """线程安全的 SQLite 会话与长期记忆仓库。

    每个公开方法都创建独立连接，因此 Tk 主线程和后台推理线程不会共享
    SQLite 连接对象。数据库仍是唯一真源，未来增加向量索引时无需迁移原文。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        connection = initialize_memory_database(path)
        connection.close()

    @contextmanager
    def _connection(self):
        """提交成功事务并始终关闭当前线程使用的连接。"""

        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def get_or_create_conversation(self, title: str = "桌面陪伴") -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT id FROM conversations ORDER BY updated_at DESC, id DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                return int(row["id"])
            cursor = connection.execute(
                "INSERT INTO conversations(title) VALUES (?)",
                (title,),
            )
            return int(cursor.lastrowid)

    def create_conversation(self, title: str = "新对话") -> int:
        """显式创建一段新会话；长期记忆仍跨会话共享。"""

        normalized = title.strip() or "新对话"
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO conversations(title) VALUES (?)",
                (normalized[:40],),
            )
            return int(cursor.lastrowid)

    def list_conversations(self, *, limit: int = 20) -> list[StoredConversation]:
        """按最近使用顺序返回侧栏会话，不加载完整消息正文。"""

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    c.id,
                    c.title,
                    count(m.id) AS message_count
                FROM conversations c
                LEFT JOIN messages m ON m.conversation_id=c.id
                GROUP BY c.id
                ORDER BY c.updated_at DESC, c.id DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return [
            StoredConversation(
                id=int(row["id"]),
                title=str(row["title"]) or "未命名对话",
                message_count=int(row["message_count"]),
            )
            for row in rows
        ]

    def conversation_exists(self, conversation_id: int) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM conversations WHERE id=?",
                (conversation_id,),
            ).fetchone()
        return row is not None

    def delete_conversation(self, conversation_id: int) -> bool:
        """删除指定会话及其消息；长期记忆是独立数据，不随会话删除。

        数据库已启用外键约束，``messages`` 和会话关联的临时引用会通过
        ``ON DELETE`` 规则清理。返回值用于区分“已删除”和“目标已不存在”。
        """

        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM conversations WHERE id=?",
                (conversation_id,),
            )
            return cursor.rowcount > 0

    def add_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        *,
        emotion: str | None = None,
    ) -> int:
        normalized = content.strip()
        if role not in {"system", "user", "assistant"}:
            raise ValueError(f"不支持的消息角色: {role}")
        if not normalized:
            raise ValueError("消息内容不能为空")
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO messages(conversation_id, role, content, emotion)
                VALUES (?, ?, ?, ?)
                """,
                (conversation_id, role, normalized, emotion),
            )
            connection.execute(
                """
                UPDATE conversations
                SET updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (conversation_id,),
            )
            return int(cursor.lastrowid)

    def recent_messages(
        self,
        conversation_id: int,
        *,
        limit: int,
    ) -> list[StoredMessage]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, role, content, emotion
                FROM (
                    SELECT id, role, content, emotion, created_at
                    FROM messages
                    WHERE conversation_id=?
                    ORDER BY id DESC
                    LIMIT ?
                )
                ORDER BY id ASC
                """,
                (conversation_id, max(1, limit)),
            ).fetchall()
        return [
            StoredMessage(
                id=int(row["id"]),
                role=str(row["role"]),
                content=str(row["content"]),
                emotion=None if row["emotion"] is None else str(row["emotion"]),
            )
            for row in rows
        ]

    def history_page(
        self,
        conversation_id: int,
        *,
        before_id: int | None = None,
        limit: int = 80,
    ) -> HistoryPage:
        """Read one earlier UI page without OFFSET or changing model history.

        One extra row proves whether another page exists. A stable ID cursor
        prevents new replies or deleted rows from shifting page boundaries.
        """
        if type(conversation_id) is not int or conversation_id < 1:
            raise ValueError("conversation_id must be a positive integer")
        if before_id is not None and (type(before_id) is not int or before_id < 1):
            raise ValueError("before_id must be a positive integer")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("history page size must be between 1 and 200")
        cursor_clause = " AND id < ?" if before_id is not None else ""
        parameters = [conversation_id]
        if before_id is not None:
            parameters.append(before_id)
        parameters.append(limit + 1)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, role, content, emotion FROM messages "
                "WHERE conversation_id=? AND role IN ('user', 'assistant')"
                + cursor_clause + " ORDER BY id DESC LIMIT ?",
                parameters,
            ).fetchall()
        messages = tuple(
            StoredMessage(int(row["id"]), str(row["role"]), str(row["content"]), row["emotion"])
            for row in reversed(rows[:limit])
        )
        return HistoryPage(messages, has_more=len(rows) > limit)

    def relevant_memories(
        self,
        query: str,
        *,
        limit: int,
    ) -> list[StoredMemory]:
        """优先召回固定记忆，再尝试使用 FTS 匹配当前输入。

        FTS 查询可能包含引号等控制字符，因此失败时安全退回重要度排序。
        """

        normalized = " ".join(query.strip().split())
        rows: list[sqlite3.Row] = []
        safe_query = normalized.replace('"', " ")
        semantic_ids=[]
        try:
            from .semantic_memory import SemanticMemory
            semantic_ids=SemanticMemory(self.path).rank(normalized,limit)
        except (OSError,ValueError,ImportError,sqlite3.Error):
            # Optional local service failure cannot corrupt the authoritative
            # store or silently redirect private memory to a cloud provider.
            import logging
            logging.getLogger(__name__).warning('本地语义记忆暂不可用，使用FTS5召回；没有上传云端')
        with self._connection() as connection:
            if normalized:
                try:
                    rows = connection.execute(
                        """
                        SELECT m.id, m.content, m.category, m.importance, m.pinned
                        FROM memory_fts f
                        JOIN memories m ON m.id=f.rowid
                        WHERE memory_fts MATCH ?
                        ORDER BY m.pinned DESC, bm25(memory_fts),
                                 m.importance DESC, m.updated_at DESC
                        LIMIT ?
                        """,
                        (f'"{safe_query}"', max(1, limit)),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            for memory_id in semantic_ids:
                if len(rows)>=limit:break
                if any(int(row['id'])==memory_id for row in rows):continue
                match=connection.execute('SELECT id,content,category,importance,pinned FROM memories WHERE id=?',(memory_id,)).fetchone()
                if match is not None:rows.append(match)
            if len(rows) < limit:
                existing_ids = {int(row["id"]) for row in rows}
                fallback = connection.execute(
                    """
                    SELECT id, content, category, importance, pinned
                    FROM memories
                    ORDER BY pinned DESC, importance DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (max(1, limit * 2),),
                ).fetchall()
                for row in fallback:
                    if int(row["id"]) not in existing_ids:
                        rows.append(row)
                    if len(rows) >= limit:
                        break
        return [
            StoredMemory(
                id=int(row["id"]),
                content=str(row["content"]),
                category=str(row["category"]),
                importance=float(row["importance"]),
                pinned=bool(row["pinned"]),
            )
            for row in rows[:limit]
        ]

    @staticmethod
    def _normalize_memory_item(item: dict[str, Any]) -> tuple[str, str, float] | None:
        """规范化记忆项，返回 (content, category, importance) 或 None（跳过空内容）。"""
        content = " ".join(str(item.get("content", "")).strip().split())
        if not content:
            return None
        category = str(item.get("category", "fact")).strip() or "fact"
        try:
            importance = float(item.get("importance", 0.5))
        except (TypeError, ValueError):
            importance = 0.5
        importance = min(1.0, max(0.0, importance))
        return content, category, importance

    def store_extracted_memories(
        self,
        items: list[dict[str, Any]],
        *,
        source_message_id: int | None,
    ) -> int:
        """保存模型在本轮明确抽取的长期事实，并按原文去重。"""

        stored = 0
        with self._connection() as connection:
            for item in items:
                normalized = self._normalize_memory_item(item)
                if normalized is None:
                    continue
                content, category, importance = normalized
                duplicate = connection.execute(
                    "SELECT id FROM memories WHERE content=? LIMIT 1",
                    (content,),
                ).fetchone()
                if duplicate is not None:
                    connection.execute(
                        """
                        UPDATE memories
                        SET importance=MAX(importance, ?),
                            category=?,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (importance, category, int(duplicate["id"])),
                    )
                    continue
                connection.execute(
                    """
                    INSERT INTO memories(
                        content, category, importance, source_message_id
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (content, category, importance, source_message_id),
                )
                stored += 1
        return stored

    @staticmethod
    def _store_items(
        connection: sqlite3.Connection,
        items: list[dict[str, Any]],
        *,
        source_message_id: int | None,
    ) -> int:
        """在调用方事务中保存记忆，供原子对话写入复用。"""

        stored = 0
        for item in items:
            normalized = ConversationMemory._normalize_memory_item(item)
            if normalized is None:
                continue
            content, category, importance = normalized
            key = _memory_key(content)
            duplicate = next(
                (
                    row
                    for row in connection.execute(
                        "SELECT id, content FROM memories ORDER BY id DESC"
                    ).fetchall()
                    if _memory_key(str(row["content"])) == key
                ),
                None,
            )
            if duplicate is not None:
                connection.execute(
                    """
                    UPDATE memories
                    SET importance=MAX(importance, ?),
                        category=?,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (importance, category, int(duplicate["id"])),
                )
                continue
            connection.execute(
                """
                INSERT INTO memories(
                    content, category, importance, source_message_id
                ) VALUES (?, ?, ?, ?)
                """,
                (content, category, importance, source_message_id),
            )
            stored += 1
        return stored

    def record_exchange(
        self,
        conversation_id: int,
        user_content: str,
        assistant_content: str,
        *,
        emotion: str | None,
        extracted_memories: list[dict[str, Any]],
    ) -> tuple[int, MemoryStats]:
        """成功生成回答后，用一个事务写入用户、助手和长期记忆。

        这样模型格式错误或网络失败不会留下只有用户半句话的残缺轮次。
        """

        user_text = user_content.strip()
        assistant_text = assistant_content.strip()
        if not user_text or not assistant_text:
            raise ValueError("对话两端内容均不能为空")
        deterministic = extract_explicit_memories(user_text)
        # 确定性结果带有统一“用户…”主语，先写入可保持记忆列表可读。
        # 模型已有结构化结果时不再叠加正则兜底，否则“我喜欢……”与
        # “用户偏好……”会作为两条语义重复记忆写入。
        if extracted_memories:
            all_memories = []
            for item in extracted_memories:
                normalized_item = dict(item)
                content = str(normalized_item.get("content", "")).strip()
                if content.startswith("我"):
                    content = "用户" + content[1:]
                elif content and not content.startswith("用户"):
                    content = "用户" + content
                normalized_item["content"] = content
                all_memories.append(normalized_item)
        else:
            all_memories = deterministic

        with self._connection() as connection:
            user_cursor = connection.execute(
                """
                INSERT INTO messages(conversation_id, role, content)
                VALUES (?, 'user', ?)
                """,
                (conversation_id, user_text),
            )
            user_message_id = int(user_cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO messages(conversation_id, role, content, emotion)
                VALUES (?, 'assistant', ?, ?)
                """,
                (conversation_id, assistant_text, emotion),
            )
            memories_added = self._store_items(
                connection,
                all_memories,
                source_message_id=user_message_id,
            )
            connection.execute(
                """
                UPDATE conversations
                SET
                    title=CASE
                        WHEN title IN ('新对话', '桌面陪伴')
                        THEN ?
                        ELSE title
                    END,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (user_text[:24], conversation_id),
            )
            row = connection.execute(
                """
                SELECT
                    (SELECT count(*) FROM messages) AS message_count,
                    (SELECT count(*) FROM memories) AS memory_count
                """
            ).fetchone()
        stats = MemoryStats(
            message_count=int(row["message_count"]),
            memory_count=int(row["memory_count"]),
        )
        return memories_added, stats

    def stats(self) -> MemoryStats:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT
                    (SELECT count(*) FROM messages) AS message_count,
                    (SELECT count(*) FROM memories) AS memory_count
                """
            ).fetchone()
        return MemoryStats(
            message_count=int(row["message_count"]) if row is not None else 0,
            memory_count=int(row["memory_count"]) if row is not None else 0,
        )

    def memory_count(self) -> int:
        return self.stats().memory_count

    def list_memories(self, *, category: str = "", query: str = "") -> list[StoredMemory]:
        """供管理界面分类查看；不会改变召回的访问时间或优先级。"""
        conditions = []
        arguments: list[object] = []
        if category:
            conditions.append("category=?")
            arguments.append(category)
        if query.strip():
            conditions.append("content LIKE ?")
            arguments.append(f"%{query.strip()}%")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, content, category, importance, pinned FROM memories" + where
                + " ORDER BY pinned DESC, category, updated_at DESC, id DESC",
                arguments,
            ).fetchall()
        return [StoredMemory(int(row["id"]), str(row["content"]), str(row["category"]),
                             float(row["importance"]), bool(row["pinned"])) for row in rows]

    def backup(self, destination: Path) -> Path:
        """SQLite 在线备份包含 WAL 中已提交的数据，比复制 .sqlite3 更可靠。"""
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                # sqlite3 的 with 只提交事务，不关闭连接；Windows 会锁住备份。
                target.close()
        return destination

    def update_memory(self, memory_id: int, *, content: str, category: str, pinned: bool,
                      expected: StoredMemory | None = None) -> None:
        text = content.strip()
        group = category.strip()
        if not text or not group:
            raise ValueError("记忆内容与分类不能为空")
        if len(group) > 64:
            raise ValueError("分类名称不能超过 64 个字符")
        where, parameters = _memory_edit_where(memory_id, expected)
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE memories SET content=?, category=?, pinned=?, updated_at=CURRENT_TIMESTAMP WHERE " + where,
                (text, group, int(pinned), *parameters),
            )
            if not cursor.rowcount:
                raise ValueError("这条记忆已改变或不存在，未覆盖。请重新读取选中记忆。" if expected is not None else "这条记忆已不存在，请刷新列表")

    def delete_memory(self, memory_id: int, *, expected: StoredMemory | None = None) -> bool:
        """只删除选中的一条长期记忆，不删除聊天或其他分类。"""
        where, parameters = _memory_edit_where(memory_id, expected)
        with self._connection() as connection:
            return bool(connection.execute("DELETE FROM memories WHERE " + where, parameters).rowcount)

    def export_memory_editor(self, path: Path) -> int:
        """导出长期记忆为可由 VS Code 安全编辑的 UTF-8 JSON。"""

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, content, category, importance, pinned
                FROM memories
                ORDER BY pinned DESC, importance DESC, id ASC
                """
            ).fetchall()
        payload = {
            "format": 1,
            "_说明": (
                "可修改 content/category/importance/pinned；删除条目即删除记忆，"
                "新增条目请省略 id。保存后回到 Rem Companion 点击“应用 JSON”。"
            ),
            "memories": [
                {
                    "id": int(row["id"]),
                    "content": str(row["content"]),
                    "category": str(row["category"]),
                    "importance": float(row["importance"]),
                    "pinned": bool(row["pinned"]),
                }
                for row in rows
            ],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        return len(rows)

    def _read_memory_editor(
        self,
        path: Path,
    ) -> list[dict[str, Any]]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ValueError("请先点击“VS Code 编辑”导出记忆文件") from error
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"记忆 JSON 无法读取: {error}") from error
        if not isinstance(payload, dict) or not isinstance(
            payload.get("memories"),
            list,
        ):
            raise ValueError("记忆 JSON 必须包含 memories 数组")
        items: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        for index, raw in enumerate(payload["memories"], start=1):
            if not isinstance(raw, dict):
                raise ValueError(f"第 {index} 条记忆不是对象")
            content = str(raw.get("content", "")).strip()
            category = str(raw.get("category", "fact")).strip() or "fact"
            try:
                importance = float(raw.get("importance", 0.5))
            except (TypeError, ValueError) as error:
                raise ValueError(f"第 {index} 条 importance 不是数字") from error
            if not content:
                raise ValueError(f"第 {index} 条记忆内容为空")
            if not 0 <= importance <= 1:
                raise ValueError(f"第 {index} 条 importance 必须在 0 到 1 之间")
            pinned = raw.get("pinned", False)
            if not isinstance(pinned, bool):
                raise ValueError(f"第 {index} 条 pinned 必须是 true 或 false")
            item_id = raw.get("id")
            if item_id is not None:
                try:
                    item_id = int(item_id)
                except (TypeError, ValueError) as error:
                    raise ValueError(f"第 {index} 条 id 不是整数") from error
                if item_id in seen_ids:
                    raise ValueError(f"记忆 id 重复: {item_id}")
                seen_ids.add(item_id)
            items.append(
                {
                    "id": item_id,
                    "content": content,
                    "category": category[:40],
                    "importance": importance,
                    "pinned": 1 if pinned else 0,
                }
            )
        return items

    def preview_memory_import(self, path: Path) -> MemoryImportPlan:
        items = self._read_memory_editor(path)
        with self._connection() as connection:
            existing_ids = {
                int(row["id"])
                for row in connection.execute("SELECT id FROM memories")
            }
        incoming_ids = {
            int(item["id"]) for item in items if item["id"] is not None
        }
        unknown = incoming_ids - existing_ids
        if unknown:
            raise ValueError(f"JSON 包含不存在的记忆 id: {sorted(unknown)}")
        return MemoryImportPlan(
            create=sum(item["id"] is None for item in items),
            update=len(incoming_ids),
            delete=len(existing_ids - incoming_ids),
        )

    def apply_memory_import(self, path: Path) -> MemoryImportPlan:
        items = self._read_memory_editor(path)
        plan = self.preview_memory_import(path)
        incoming_ids = {
            int(item["id"]) for item in items if item["id"] is not None
        }
        with self._connection() as connection:
            existing_ids = {
                int(row["id"])
                for row in connection.execute("SELECT id FROM memories")
            }
            for item in items:
                if item["id"] is None:
                    connection.execute(
                        """
                        INSERT INTO memories(
                            content, category, importance, pinned
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            item["content"],
                            item["category"],
                            item["importance"],
                            item["pinned"],
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE memories
                        SET content=?, category=?, importance=?, pinned=?,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (
                            item["content"],
                            item["category"],
                            item["importance"],
                            item["pinned"],
                            item["id"],
                        ),
                    )
            for memory_id in existing_ids - incoming_ids:
                connection.execute(
                    "DELETE FROM memories WHERE id=?",
                    (memory_id,),
                )
        return plan
