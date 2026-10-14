from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile
import unittest

from PIL import Image


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from install_action import install_action  # noqa: E402


class InstallActionTests(unittest.TestCase):
    def _frame_directory(self, root: Path) -> Path:
        source = root / "source"
        source.mkdir()
        frame = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
        for x in range(14, 34):
            for y in range(8, 43):
                frame.putpixel((x, y), (120, 180, 240, 255))
        frame.save(source / "wave_001.png")
        return source

    def test_install_stages_then_replaces_target_and_updates_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._frame_directory(root)
            hook = root / "hook.json"
            hook.write_text(
                json.dumps(
                    {
                        "asset_root": "assets/actions",
                        "registry_json": "data/pet-actions.json",
                    }
                ),
                encoding="utf-8",
            )

            installed = install_action(
                project_root=root,
                action_name="wave",
                frames_dir=source,
                hook_path=hook,
            )

            self.assertTrue((installed / "wave_001.png").is_file())
            registry = json.loads(
                (root / "data" / "pet-actions.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("assets/actions/wave", registry["wave"]["path"])

    def test_hook_cannot_escape_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._frame_directory(root)
            hook = root / "hook.json"
            hook.write_text(
                json.dumps({"asset_root": "../outside"}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "项目目录"):
                install_action(
                    project_root=root,
                    action_name="wave",
                    frames_dir=source,
                    hook_path=hook,
                )


if __name__ == "__main__":
    unittest.main()
