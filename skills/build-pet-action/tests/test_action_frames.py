from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from action_frames import BuildOptions, build_frames  # noqa: E402
from validate_action import validate_frames  # noqa: E402


class ActionFrameBuildTests(unittest.TestCase):
    def _make_sheet(self, path: Path) -> None:
        sheet = Image.new("RGB", (120, 70), "black")
        draw = ImageDraw.Draw(sheet)
        # 两格中的角色主体。
        draw.rectangle((18, 18, 42, 58), fill="#8FC5F5")
        draw.rectangle((78, 18, 102, 58), fill="#8FC5F5")
        # 与主体分离的动作小配图，必须保留。
        draw.ellipse((5, 5, 13, 13), fill="white")
        draw.ellipse((107, 6, 116, 15), fill="white")
        # 底部标签条不应进入输出帧。
        draw.rectangle((0, 60, 119, 69), fill="white")
        draw.text((26, 60), "1", fill="black")
        draw.text((86, 60), "2", fill="black")
        sheet.save(path)

    def test_build_preserves_detached_effects_and_stable_canvas(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sheet.png"
            output = root / "frames"
            self._make_sheet(source)

            result = build_frames(
                BuildOptions(
                    source=source,
                    output_dir=output,
                    action_name="coquettish",
                    columns=2,
                    rows=1,
                    label_strip_px=10,
                    background_mode="corner",
                    background_tolerance=20,
                )
            )

            frames = [Image.open(path).convert("RGBA") for path in result.frames]
            self.assertEqual(2, len(frames))
            self.assertEqual(frames[0].size, frames[1].size)
            self.assertEqual(0, frames[0].getpixel((0, frames[0].height - 1))[3])
            # 左上角小配图属于前景，透明背景清理后仍有像素。
            self.assertGreater(
                sum(
                    1
                    for y in range(frames[0].height)
                    for x in range(frames[0].width)
                    if (
                        frames[0].getpixel((x, y))[3] > 0
                        and frames[0].getpixel((x, y))[:3]
                        == (255, 255, 255)
                    )
                ),
                0,
            )
            self.assertTrue(validate_frames(output).ok)

    def test_mirror_second_pass_adds_symmetric_frames(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sheet.png"
            self._make_sheet(source)

            result = build_frames(
                BuildOptions(
                    source=source,
                    output_dir=root / "frames",
                    action_name="happy",
                    columns=2,
                    rows=1,
                    label_strip_px=10,
                    sequence_mode="mirror_second_pass",
                )
            )

            self.assertEqual(4, len(result.frames))
            first = Image.open(result.frames[0]).convert("RGBA")
            mirrored = Image.open(result.frames[2]).convert("RGBA")
            self.assertEqual(first.transpose(Image.Transpose.FLIP_LEFT_RIGHT).tobytes(), mirrored.tobytes())


if __name__ == "__main__":
    unittest.main()
