"""Static QA for generated pet action frames."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import argparse
import json

from PIL import Image


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    frame: str
    message: str


@dataclass(frozen=True)
class ValidationReport:
    frame_count: int
    canvas: tuple[int, int] | None
    issues: tuple[ValidationIssue, ...]

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "frame_count": self.frame_count,
            "canvas": list(self.canvas) if self.canvas else None,
            "issues": [asdict(issue) for issue in self.issues],
        }


def _border_has_alpha(image: Image.Image) -> bool:
    alpha = image.getchannel("A")
    width, height = image.size
    coordinates = (
        [(x, 0) for x in range(width)]
        + [(x, height - 1) for x in range(width)]
        + [(0, y) for y in range(height)]
        + [(width - 1, y) for y in range(height)]
    )
    return any(alpha.getpixel(point) > 0 for point in coordinates)


def validate_frames(directory: Path) -> ValidationReport:
    paths = sorted(
        path
        for path in directory.glob("*.png")
        if path.name != "contact-sheet.png"
    )
    issues: list[ValidationIssue] = []
    if not paths:
        issues.append(
            ValidationIssue("error", "no-frames", "", "没有找到 PNG 动作帧")
        )
        return ValidationReport(0, None, tuple(issues))

    expected_size: tuple[int, int] | None = None
    areas: list[int] = []
    feet: list[int] = []
    for path in paths:
        try:
            image = Image.open(path).convert("RGBA")
        except OSError as error:
            issues.append(
                ValidationIssue(
                    "error",
                    "invalid-image",
                    path.name,
                    f"无法读取图片：{error}",
                )
            )
            continue
        if expected_size is None:
            expected_size = image.size
        elif image.size != expected_size:
            issues.append(
                ValidationIssue(
                    "error",
                    "canvas-mismatch",
                    path.name,
                    f"画布尺寸 {image.size} 与首帧 {expected_size} 不一致",
                )
            )
        bbox = image.getchannel("A").getbbox()
        if bbox is None:
            issues.append(
                ValidationIssue(
                    "error",
                    "empty-frame",
                    path.name,
                    "动作帧完全透明",
                )
            )
            continue
        # ``tobytes`` is stable across older/newer Pillow and avoids the
        # Image.getdata deprecation introduced in Pillow 12.
        areas.append(sum(1 for value in image.getchannel("A").tobytes() if value))
        feet.append(bbox[3])
        if _border_has_alpha(image):
            issues.append(
                ValidationIssue(
                    "error",
                    "border-clipping",
                    path.name,
                    "前景接触画布边界，可能存在裁切或缺少透明留白",
                )
            )

    for index in range(1, len(areas)):
        small = max(1, min(areas[index - 1], areas[index]))
        large = max(areas[index - 1], areas[index])
        if large / small > 1.85:
            issues.append(
                ValidationIssue(
                    "warning",
                    "area-jump",
                    paths[index].name,
                    "相邻帧前景面积突变，检查是否漏切动作小配图或角色",
                )
            )
    if feet and max(feet) - min(feet) > 8:
        issues.append(
            ValidationIssue(
                "warning",
                "foot-drift",
                "",
                f"脚底锚点漂移 {max(feet) - min(feet)} px，播放时可能上下抖动",
            )
        )
    return ValidationReport(len(paths), expected_size, tuple(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frames", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = validate_frames(args.frames)
    payload = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    print(payload)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload + "\n", encoding="utf-8")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
