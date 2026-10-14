from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from remcompanion.secret_store import FileSecretStore


class ReversibleTestProtector:
    """测试替身：验证落盘内容不是明文，不模拟真实密码学强度。"""

    def protect(self, plaintext: bytes) -> bytes:
        return b"protected:" + plaintext[::-1]

    def unprotect(self, ciphertext: bytes) -> bytes:
        prefix = b"protected:"
        if not ciphertext.startswith(prefix):
            raise ValueError("invalid test ciphertext")
        return ciphertext[len(prefix) :][::-1]


class FileSecretStoreTests(unittest.TestCase):
    def test_secret_round_trip_never_writes_plaintext(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = FileSecretStore(root, ReversibleTestProtector())

            store.set("openai", "sk-sensitive-token")
            secret_path = root / "data" / "credentials" / "openai.bin"

            self.assertEqual("sk-sensitive-token", store.get("openai"))
            self.assertNotIn(b"sk-sensitive-token", secret_path.read_bytes())

            store.delete("openai")
            self.assertIsNone(store.get("openai"))

    def test_provider_id_cannot_escape_credentials_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FileSecretStore(
                Path(directory),
                ReversibleTestProtector(),
            )

            with self.assertRaisesRegex(ValueError, "provider"):
                store.set("../outside", "secret")


if __name__ == "__main__":
    unittest.main()
