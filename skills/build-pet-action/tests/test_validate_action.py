from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from validate_action import validate_frames  # noqa: E402


class ValidateActionTests(unittest.TestCase):
    def test_valid_sequence_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, offset in enumerate((0, 1, 0), start=1):
                frame = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
                ImageDraw.Draw(frame).rectangle(
                    (20 + offset, 12, 43 + offset, 59),
                    fill=(120, 180, 240, 255),
                )
                frame.save(root / f"idle_{index:03d}.png")

            report = validate_frames(root)

        self.assertTrue(report.ok, report.issues)
        self.assertEqual(3, report.frame_count)

    def test_border_clipping_and_canvas_mismatch_are_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            ImageDraw.Draw(first).rectangle((0, 8, 30, 60), fill="white")
            first.save(root / "bad_001.png")
            Image.new("RGBA", (60, 64), "white").save(root / "bad_002.png")

            report = validate_frames(root)

        self.assertFalse(report.ok)
        joined = "\n".join(issue.message for issue in report.issues)
        self.assertIn("画布尺寸", joined)
        self.assertIn("边界", joined)


if __name__ == "__main__":
    unittest.main()
