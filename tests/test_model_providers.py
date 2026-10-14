from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest

from remcompanion.model_providers import (
    LOCAL_PROVIDER_ID,
    ModelProviderRepository,
    ProviderConfig,
    default_provider_state,
)


class ModelProviderRepositoryTests(unittest.TestCase):
    def test_missing_file_keeps_existing_local_qwen_as_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ModelProviderRepository(Path(directory))

            state = repository.load()

        self.assertEqual(LOCAL_PROVIDER_ID, state.active_provider_id)
        self.assertEqual("local", state.active_provider.kind)
        self.assertIn("openai", state.provider_ids)
        self.assertIn("deepseek", state.provider_ids)
        self.assertIn("qwen", state.provider_ids)
        self.assertIn("custom", state.provider_ids)

    def test_round_trip_persists_non_secret_fields_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = ModelProviderRepository(root)
            state = default_provider_state()
            updated = state.replace_provider(
                ProviderConfig(
                    provider_id="custom",
                    display_name="公司代理",
                    kind="remote",
                    base_url="https://llm.example.com/v1",
                    endpoint_path="/chat/completions",
                    model="my-model",
                    structured_output="json_object",
                    requires_api_key=True,
                )
            ).activate("custom")

            repository.save(updated)
            loaded = repository.load()
            raw = json.loads(
                (root / "data" / "model-providers.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual("custom", loaded.active_provider_id)
        self.assertEqual("my-model", loaded.active_provider.model)
        serialized = json.dumps(raw).lower()
        self.assertNotIn("api_key_value", serialized)
        self.assertNotIn("secret_value", serialized)
        self.assertNotIn("sk-", serialized)

    def test_invalid_remote_base_url_is_rejected(self) -> None:
        provider = ProviderConfig(
            provider_id="custom",
            display_name="错误配置",
            kind="remote",
            base_url="file:///tmp/model",
            endpoint_path="/chat/completions",
            model="demo",
            structured_output="json_object",
            requires_api_key=True,
        )

        with self.assertRaisesRegex(ValueError, "HTTP"):
            provider.validate()

    def test_remote_api_key_cannot_be_sent_over_plain_http(self) -> None:
        provider = ProviderConfig(
            provider_id="custom",
            display_name="不安全接口",
            kind="remote",
            base_url="http://api.example.com/v1",
            endpoint_path="/chat/completions",
            model="demo",
            structured_output="json_object",
            requires_api_key=True,
        )

        with self.assertRaisesRegex(ValueError, "HTTPS"):
            provider.validate()

    def test_corrupt_file_falls_back_to_safe_local_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "data" / "model-providers.json"
            path.parent.mkdir(parents=True)
            path.write_text("{not-json", encoding="utf-8")

            state = ModelProviderRepository(root).load()

        self.assertEqual(LOCAL_PROVIDER_ID, state.active_provider_id)


if __name__ == "__main__":
    unittest.main()
