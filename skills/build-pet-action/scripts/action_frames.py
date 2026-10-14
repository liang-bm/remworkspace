"""Deterministic sprite-sheet processing for desktop pet actions."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import json
import re
import shutil
import uuid

from PIL import Image


ACTION_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class BuildOptions:
    """All deterministic inputs required to turn one sheet into frames."""

    source: Path
    output_dir: Path
    action_name: str
    columns: int
    rows: int
    label_strip_px: int = 0
    background_mode: str = "corner"
    background_tolerance: int = 28
    crop_padding: int = 4
    sequence_mode: str = "once"
    frame_duration_ms: int = 140

    def validate(self) -> "BuildOptions":
        if not ACTION_NAME.fullmatch(self.action_name):
            raise ValueError("action name must be lowercase kebab/snake case")
        if self.columns < 1 or self.rows < 1:
            raise ValueError("columns and rows must be positive")
        if self.label_strip_px < 0 or self.crop_padding < 0:
            raise ValueError("strip and padding values cannot be negative")
        if not 0 <= self.background_tolerance <= 255:
            raise ValueError("background tolerance must be between 0 and 255")
        if self.background_mode not in {"corner", "alpha", "none"}:
            raise ValueError("background mode must be corner, alpha, or none")
        if self.sequence_mode not in {
            "once",
            "loop",
            "pingpong",
            "mirror_second_pass",
        }:
            raise ValueError("unsupported sequence mode")
        if self.frame_duration_ms < 20:
            raise ValueError("frame duration must be at least 20 ms")
        return self


@dataclass(frozen=True)
class BuildResult:
    frames: tuple[Path, ...]
    manifest: Path
    contact_sheet: Path
    backup: Path | None


def _near(first: tuple[int, ...], second: tuple[int, ...], tolerance: int) -> bool:
    return max(abs(first[index] - second[index]) for index in range(3)) <= tolerance


def remove_connected_corner_background(
    image: Image.Image,
    *,
    tolerance: int,
) -> Image.Image:
    """Remove only corner-connected background pixels.

    A color-key over the entire image would erase black outlines and internal
    shadows. Flooding from the cell border removes the sheet background while
    retaining detached foreground effects such as hearts, puffs, and sparkles.
    """

    output = image.convert("RGBA")
    pixels = output.load()
    width, height = output.size
    corner_samples = (
        pixels[0, 0],
        pixels[width - 1, 0],
        pixels[0, height - 1],
        pixels[width - 1, height - 1],
    )
    target = tuple(
        round(sum(sample[channel] for sample in corner_samples) / 4)
        for channel in range(4)
    )
    queue: deque[tuple[int, int]] = deque()
    visited: set[tuple[int, int]] = set()
    for x in range(width):
        queue.append((x, 0))
        queue.append((x, height - 1))
    for y in range(height):
        queue.append((0, y))
        queue.append((width - 1, y))

    while queue:
        x, y = queue.popleft()
        if (x, y) in visited:
            continue
        visited.add((x, y))
        pixel = pixels[x, y]
        if pixel[3] == 0 or not _near(pixel, target, tolerance):
            continue
        pixels[x, y] = (pixel[0], pixel[1], pixel[2], 0)
        if x > 0:
            queue.append((x - 1, y))
        if x + 1 < width:
            queue.append((x + 1, y))
        if y > 0:
            queue.append((x, y - 1))
        if y + 1 < height:
            queue.append((x, y + 1))
    return output


def _split_sheet(options: BuildOptions) -> list[Image.Image]:
    source = Image.open(options.source).convert("RGBA")
    usable_height = source.height - options.label_strip_px
    if usable_height <= 0:
        raise ValueError("label strip consumes the entire image")
    if source.width % options.columns or usable_height % options.rows:
        raise ValueError(
            "sprite sheet dimensions are not divisible by the configured grid"
        )
    cell_width = source.width // options.columns
    cell_height = usable_height // options.rows
    frames: list[Image.Image] = []
    for row in range(options.rows):
        for column in range(options.columns):
            cell = source.crop(
                (
                    column * cell_width,
                    row * cell_height,
                    (column + 1) * cell_width,
                    (row + 1) * cell_height,
                )
            )
            if options.background_mode == "corner":
                cell = remove_connected_corner_background(
                    cell,
                    tolerance=options.background_tolerance,
                )
            elif options.background_mode == "alpha":
                cell = cell.convert("RGBA")
            frames.append(cell)
    return frames


def _stable_union_crop(
    frames: list[Image.Image],
    *,
    padding: int,
) -> list[Image.Image]:
    """Crop every frame to the same sheet-coordinate box.

    Per-frame trimming causes visible jitter. The union of all alpha bounds
    preserves a stable canvas and also includes effects that appear only in a
    subset of frames.
    """

    bounds = [frame.getchannel("A").getbbox() for frame in frames]
    visible = [box for box in bounds if box is not None]
    if not visible:
        raise ValueError("all generated frames are transparent")
    left = max(0, min(box[0] for box in visible) - padding)
    top = max(0, min(box[1] for box in visible) - padding)
    right = min(frames[0].width, max(box[2] for box in visible) + padding)
    bottom = min(frames[0].height, max(box[3] for box in visible) + padding)
    return [frame.crop((left, top, right, bottom)) for frame in frames]


def _apply_sequence(
    frames: list[Image.Image],
    sequence_mode: str,
) -> list[Image.Image]:
    if sequence_mode == "pingpong" and len(frames) > 2:
        return frames + list(reversed(frames[1:-1]))
    if sequence_mode == "mirror_second_pass":
        mirrored = [
            frame.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            for frame in frames
        ]
        return frames + mirrored
    return frames


def _write_contact_sheet(frames: list[Image.Image], destination: Path) -> None:
    columns = min(6, len(frames))
    rows = (len(frames) + columns - 1) // columns
    cell_width = max(frame.width for frame in frames)
    cell_height = max(frame.height for frame in frames)
    sheet = Image.new(
        "RGBA",
        (columns * cell_width, rows * cell_height),
        (245, 247, 250, 255),
    )
    for index, frame in enumerate(frames):
        x = (index % columns) * cell_width
        y = (index // columns) * cell_height
        sheet.alpha_composite(frame, (x, y))
    sheet.save(destination)


def build_frames(options: BuildOptions) -> BuildResult:
    """Build to a staging directory, then atomically replace the output."""

    options.validate()
    if not options.source.is_file():
        raise FileNotFoundError(options.source)
    frames = _apply_sequence(
        _stable_union_crop(
            _split_sheet(options),
            padding=options.crop_padding,
        ),
        options.sequence_mode,
    )
    parent = options.output_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".{options.output_dir.name}-staging-{uuid.uuid4().hex[:8]}"
    staging.mkdir()
    frame_paths: list[Path] = []
    try:
        for index, frame in enumerate(frames, start=1):
            path = staging / f"{options.action_name}_{index:03d}.png"
            frame.save(path)
            frame_paths.append(path)
        manifest = staging / "action.json"
        manifest.write_text(
            json.dumps(
                {
                    "version": 1,
                    "name": options.action_name,
                    "frame_count": len(frames),
                    "frame_duration_ms": options.frame_duration_ms,
                    "sequence_mode": options.sequence_mode,
                    "canvas": list(frames[0].size),
                    "source": options.source.name,
                    "build_options": {
                        key: str(value) if isinstance(value, Path) else value
                        for key, value in asdict(options).items()
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        contact_sheet = staging / "contact-sheet.png"
        _write_contact_sheet(frames, contact_sheet)

        backup: Path | None = None
        if options.output_dir.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = parent / f"{options.output_dir.name}.backup-{stamp}"
            options.output_dir.replace(backup)
        staging.replace(options.output_dir)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return BuildResult(
        frames=tuple(
            options.output_dir / path.name for path in frame_paths
        ),
        manifest=options.output_dir / "action.json",
        contact_sheet=options.output_dir / "contact-sheet.png",
        backup=backup,
    )
