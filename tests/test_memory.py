from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest

from remcompanion.memory import (
    ConversationMemory,
    extract_explicit_memories,
    initialize_memory_database,
)


class MemoryDatabaseTests(unittest.TestCase):
    def test_schema_and_fts_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "memory.sqlite3"
            connection = initialize_memory_database(database_path)
            connection.execute(
                "INSERT INTO memories(content, category, importance) VALUES (?, ?, ?)",
                ("用户喜欢蓝色和安静的环境", "preference", 0.8),
            )
            connection.commit()
            count = connection.execute("SELECT count(*) FROM memory_fts").fetchone()[0]
            connection.close()

        self.assertEqual(1, count)

    def test_conversation_history_and_memory_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationMemory(Path(directory) / "memory.sqlite3")
            conversation_id = store.get_or_create_conversation()
            user_id = store.add_message(
                conversation_id,
                "user",
                "我喜欢蓝色。",
            )
            store.add_message(
                conversation_id,
                "assistant",
                "雷姆记住了。",
                emotion="gentle",
            )
            item = {
                "content": "用户喜欢蓝色",
                "category": "preference",
                "importance": 0.8,
            }
            self.assertEqual(
                1,
                store.store_extracted_memories([item], source_message_id=user_id),
            )
            self.assertEqual(
                0,
                store.store_extracted_memories([item], source_message_id=user_id),
            )
            history = store.recent_messages(conversation_id, limit=4)
            memories = store.relevant_memories("蓝色", limit=4)
            memory_count = store.memory_count()

        self.assertEqual(["user", "assistant"], [item.role for item in history])
        self.assertEqual("用户喜欢蓝色", memories[0].content)
        self.assertEqual(1, memory_count)

    def test_record_exchange_is_atomic_and_extracts_explicit_preference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationMemory(Path(directory) / "memory.sqlite3")
            conversation_id = store.get_or_create_conversation()
            added, stats = store.record_exchange(
                conversation_id,
                "请记住，我喜欢安静的陪伴。",
                "雷姆记住了。",
                emotion="gentle",
                extracted_memories=[
                    {
                        "content": "喜欢安静的陪伴",
                        "category": "preference",
                        "importance": 0.8,
                    }
                ],
            )
            history = store.recent_messages(conversation_id, limit=4)
            memories = store.relevant_memories("安静", limit=4)

        self.assertEqual(1, added)
        self.assertEqual(2, stats.message_count)
        self.assertEqual(1, stats.memory_count)
        self.assertEqual(["user", "assistant"], [item.role for item in history])
        self.assertEqual("用户喜欢安静的陪伴", memories[0].content)

    def test_conversation_list_and_first_message_title(self) -> None:
        """ChatBox 侧栏按最近顺序列出会话，并用首句替换“新对话”。"""

        with tempfile.TemporaryDirectory() as directory:
            store = ConversationMemory(Path(directory) / "memory.sqlite3")
            first = store.create_conversation()
            store.record_exchange(
                first,
                "今天想聊一聊新的桌面伴侣",
                "雷姆会认真听着。",
                emotion="gentle",
                extracted_memories=[],
            )
            second = store.create_conversation("第二段会话")
            conversations = store.list_conversations(limit=10)
            first_exists = store.conversation_exists(first)

        self.assertEqual(second, conversations[0].id)
        self.assertEqual(0, conversations[0].message_count)
        self.assertEqual("今天想聊一聊新的桌面伴侣", conversations[1].title)
        self.assertEqual(2, conversations[1].message_count)
        self.assertTrue(first_exists)

    def test_delete_conversation_cascades_messages_but_keeps_memories(self) -> None:
        """右键删除只移除目标会话，不能误删跨会话共享的长期记忆。"""

        with tempfile.TemporaryDirectory() as directory:
            store = ConversationMemory(Path(directory) / "memory.sqlite3")
            conversation_id = store.create_conversation("待删除")
            store.record_exchange(
                conversation_id,
                "请记住我喜欢蓝色",
                "雷姆记住了。",
                emotion="gentle",
                extracted_memories=[
                    {
                        "content": "用户喜欢蓝色",
                        "category": "preference",
                        "importance": 0.8,
                    }
                ],
            )

            deleted = store.delete_conversation(conversation_id)
            deleted_again = store.delete_conversation(conversation_id)

            self.assertTrue(deleted)
            self.assertFalse(deleted_again)
            self.assertFalse(store.conversation_exists(conversation_id))
            self.assertEqual([], store.recent_messages(conversation_id, limit=10))
            self.assertEqual(1, store.memory_count())

    def test_model_memories_take_precedence_over_regex_fallback(self) -> None:
        """同一偏好不能被模型结果与正则兜底各保存一次。"""

        with tempfile.TemporaryDirectory() as directory:
            store = ConversationMemory(Path(directory) / "memory.sqlite3")
            conversation_id = store.create_conversation()
            added, stats = store.record_exchange(
                conversation_id,
                "请记住：我喜欢简洁、直接的中文回复。",
                "好的，我会保持简洁。",
                emotion="neutral",
                extracted_memories=[
                    {
                        "content": "用户偏好简洁、直接的中文回复。",
                        "category": "communication_preference",
                        "importance": 1.0,
                    }
                ],
            )

        self.assertEqual(1, added)
        self.assertEqual(1, stats.memory_count)

    def test_explicit_memory_ignores_sensitive_secrets(self) -> None:
        self.assertEqual(
            [],
            extract_explicit_memories("请记住我的密码是 example-not-a-real-password"),
        )

    def test_memory_editor_preview_and_apply(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ConversationMemory(root / "memory.sqlite3")
            conversation_id = store.get_or_create_conversation()
            store.record_exchange(
                conversation_id,
                "我喜欢蓝色，也请记住我喜欢安静。",
                "雷姆记住了。",
                emotion="gentle",
                extracted_memories=[],
            )
            editor = root / "memory-editor.json"
            store.export_memory_editor(editor)
            payload = json.loads(editor.read_text(encoding="utf-8"))
            existing = payload["memories"][0]
            existing["content"] = "用户喜欢深蓝色"
            existing["pinned"] = True
            payload["memories"].append(
                {
                    "content": "用户希望回答简洁",
                    "category": "preference",
                    "importance": 0.75,
                    "pinned": False,
                }
            )
            editor.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )

            plan = store.preview_memory_import(editor)
            applied = store.apply_memory_import(editor)
            memories = store.relevant_memories("深蓝色 回答简洁", limit=10)

        self.assertEqual(1, plan.create)
        self.assertGreaterEqual(plan.update, 1)
        self.assertEqual(plan, applied)
        self.assertEqual(2, len(memories))
        self.assertTrue(any(item.pinned for item in memories))

    def test_memory_editor_rejects_non_boolean_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ConversationMemory(root / "memory.sqlite3")
            editor = root / "memory-editor.json"
            editor.write_text(
                json.dumps(
                    {
                        "memories": [
                            {
                                "content": "测试",
                                "category": "fact",
                                "importance": 0.5,
                                "pinned": "false",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "pinned"):
                store.preview_memory_import(editor)


if __name__ == "__main__":
    unittest.main()
