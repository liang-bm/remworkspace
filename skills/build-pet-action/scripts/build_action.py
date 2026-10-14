"""CLI wrapper for deterministic action frame construction."""

from __future__ import annotations

from pathlib import Path
import argparse
import json

from action_frames import BuildOptions, build_frames
from validate_action import validate_frames


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--columns", required=True, type=int)
    parser.add_argument("--rows", required=True, type=int)
    parser.add_argument("--label-strip-px", type=int, default=0)
    parser.add_argument(
        "--background-mode",
        choices=("corner", "alpha", "none"),
        default="corner",
    )
    parser.add_argument("--background-tolerance", type=int, default=28)
    parser.add_argument("--crop-padding", type=int, default=4)
    parser.add_argument(
        "--sequence",
        choices=("once", "loop", "pingpong", "mirror_second_pass"),
        default="once",
    )
    parser.add_argument("--frame-duration-ms", type=int, default=140)
    args = parser.parse_args()
    result = build_frames(
        BuildOptions(
            source=args.source,
            output_dir=args.output,
            action_name=args.name,
            columns=args.columns,
            rows=args.rows,
            label_strip_px=args.label_strip_px,
            background_mode=args.background_mode,
            background_tolerance=args.background_tolerance,
            crop_padding=args.crop_padding,
            sequence_mode=args.sequence,
            frame_duration_ms=args.frame_duration_ms,
        )
    )
    report = validate_frames(args.output)
    print(
        json.dumps(
            {
                "frames": [str(path) for path in result.frames],
                "manifest": str(result.manifest),
                "contact_sheet": str(result.contact_sheet),
                "backup": str(result.backup) if result.backup else None,
                "validation": report.to_dict(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
