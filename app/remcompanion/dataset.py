"""将 WAV/SRT 原始素材转换成 GPT-SoVITS 训练集。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
import json
import random
import re
import shutil
import subprocess
import uuid
import wave

from .config import AppConfig


@dataclass(frozen=True)
class SubtitleCue:
    index: int
    start_seconds: float
    end_seconds: float
    text: str


@dataclass(frozen=True)
class SegmentRecord:
    path: str
    speaker: str
    language: str
    text: str
    emotion: str
    source_wav: str
    source_srt: str
    cue_index: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    corrections: tuple[str, ...]

    def list_line(self) -> str:
        # GPT-SoVITS 的官方标注格式为 path|speaker|language|text。
        return f"{self.path}|{self.speaker}|{self.language}|{self.text}"


_TIME_PATTERN = re.compile(r"^(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2}),(?P<ms>\d{3})$")
_BLOCK_PATTERN = re.compile(
    r"(?ms)^\s*(?P<index>\d+)\s*\r?\n"
    r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*"
    r"(?P<end>\d{2}:\d{2}:\d{2},\d{3})[^\r\n]*\r?\n"
    r"(?P<text>.*?)(?=\r?\n\s*\r?\n|\Z)"
)
_EMOTION_ALIASES = {
    # 第二批素材原文件使用了 netural；在读取阶段纠正，保持原始文件只读。
    "netural": "neutral",
}


def parse_timestamp(value: str) -> float:
    match = _TIME_PATTERN.match(value.strip())
    if not match:
        raise ValueError(f"无效 SRT 时间戳: {value!r}")
    return (
        int(match["h"]) * 3600
        + int(match["m"]) * 60
        + int(match["s"])
        + int(match["ms"]) / 1000.0
    )


def parse_srt(path: Path) -> list[SubtitleCue]:
    """严格按 UTF-8 读取 SRT，并支持一条字幕包含多行台词。"""

    text = path.read_text(encoding="utf-8-sig")
    cues: list[SubtitleCue] = []
    for match in _BLOCK_PATTERN.finditer(text):
        start = parse_timestamp(match["start"])
        end = parse_timestamp(match["end"])
        cue_text = re.sub(r"\s+", " ", match["text"]).strip()
        if not cue_text:
            raise ValueError(f"{path.name} 第 {match['index']} 条字幕为空")
        if end <= start:
            raise ValueError(f"{path.name} 第 {match['index']} 条字幕结束时间不大于开始时间")
        cues.append(
            SubtitleCue(
                index=int(match["index"]),
                start_seconds=start,
                end_seconds=end,
                text=cue_text,
            )
        )
    if not cues:
        raise ValueError(f"没有从 {path} 解析出字幕")
    return cues


def canonical_stem(path: Path) -> str:
    """消除 `(Vocals)` 重复后缀，用于匹配命名不一致的 WAV/SRT。"""

    stem = re.sub(r"(?i)_?\(vocals\)", "", path.stem)
    return re.sub(r"_+", "_", stem).strip("_").lower()


def extract_emotion(path: Path) -> str:
    clean = canonical_stem(path)
    match = re.match(r"^(?:\d+_)+(?P<label>[a-z]+)\d*$", clean)
    label = match["label"] if match else "unclassified"
    return _EMOTION_ALIASES.get(label, label)


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as source:
        return source.getnframes() / float(source.getframerate())


def _safe_remove_tree(path: Path, project_root: Path) -> None:
    resolved = path.resolve()
    root = project_root.resolve()
    if root not in resolved.parents:
        raise RuntimeError(f"拒绝删除项目目录之外的路径: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def _run_ffmpeg(
    ffmpeg: Path,
    source: Path,
    destination: Path,
    *,
    start: float,
    end: float,
    sample_rate: int,
    channels: int,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-ss",
        f"{start:.3f}",
        "-to",
        f"{end:.3f}",
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg 切片失败: {source.name}\n{result.stderr}")


def _split_train_valid(
    records: list[SegmentRecord], *, ratio: float, seed: int
) -> tuple[list[SegmentRecord], list[SegmentRecord]]:
    """按情绪分层，并将完全相同的文本固定在同一个集合中。"""

    grouped: dict[tuple[str, str], list[SegmentRecord]] = defaultdict(list)
    for record in records:
        grouped[(record.emotion, record.text)].append(record)

    by_emotion: dict[str, list[list[SegmentRecord]]] = defaultdict(list)
    for (emotion, _), group in grouped.items():
        by_emotion[emotion].append(group)

    rng = random.Random(seed)
    train: list[SegmentRecord] = []
    valid: list[SegmentRecord] = []
    for emotion_groups in by_emotion.values():
        rng.shuffle(emotion_groups)
        target = round(sum(len(group) for group in emotion_groups) * ratio)
        valid_count = 0
        for group in emotion_groups:
            # 单样本情绪不强制划入验证集，避免训练集中完全缺少该情绪。
            if target > 0 and valid_count < target and len(emotion_groups) > 1:
                valid.extend(group)
                valid_count += len(group)
            else:
                train.extend(group)
    return train, valid


def _collect_source_pairs(source_dirs: tuple[Path, ...]) -> list[tuple[Path, Path, Path]]:
    """逐目录配对 WAV/SRT，允许不同素材目录出现相同的文件名。

    返回值中的三项依次为素材目录、WAV 和 SRT。配对范围限定在各自目录，
    因此新增素材包不会错误覆盖旧素材包中的同名文件。
    """

    pairs: list[tuple[Path, Path, Path]] = []
    for source_dir in source_dirs:
        if not source_dir.is_dir():
            raise FileNotFoundError(f"原始语音目录不存在: {source_dir}")

        wav_by_key: dict[str, Path] = {}
        srt_by_key: dict[str, Path] = {}
        for path, target, kind in (
            *((path, wav_by_key, "WAV") for path in sorted(source_dir.glob("*.wav"))),
            *((path, srt_by_key, "SRT") for path in sorted(source_dir.glob("*.srt"))),
        ):
            key = canonical_stem(path)
            if key in target:
                raise ValueError(
                    f"{source_dir} 中规范化后出现重复 {kind} 名称: "
                    f"{target[key].name} / {path.name}"
                )
            target[key] = path

        wav_keys = set(wav_by_key)
        srt_keys = set(srt_by_key)
        if not wav_keys or not srt_keys:
            raise ValueError(f"原始语音目录中没有完整 WAV/SRT 素材: {source_dir}")
        if wav_keys != srt_keys:
            missing_srt = sorted(wav_keys - srt_keys)
            missing_wav = sorted(srt_keys - wav_keys)
            raise ValueError(
                f"{source_dir} 中 WAV/SRT 未完整配对；"
                f"缺 SRT={missing_srt}，缺 WAV={missing_wav}"
            )

        for key in sorted(srt_by_key):
            pairs.append((source_dir, wav_by_key[key], srt_by_key[key]))
    return pairs


def _source_reference(path: Path, project_root: Path) -> str:
    """报告中优先记录相对项目路径，兼容项目外的自定义素材目录。"""

    try:
        return str(path.resolve().relative_to(project_root.resolve()))
    except ValueError:
        return str(path.resolve())


def prepare_dataset(config: AppConfig, log=print) -> dict[str, object]:
    from .resource_vault import training_sources
    with training_sources(config) as readable:
        locations = dict(zip(readable.dataset.source_dirs, config.dataset.source_dirs))
        return _prepare_dataset(readable, log, source_locations=locations)


def _prepare_dataset(config: AppConfig, log=print, *, source_locations=None) -> dict[str, object]:
    def source_reference(path):
        for materialized, original in (source_locations or {}).items():
            if path == materialized or materialized in path.parents:
                return _source_reference(original / path.relative_to(materialized), config.root)
        return _source_reference(path, config.root)

    if not config.ffmpeg.exists():
        raise FileNotFoundError(f"尚未安装 FFmpeg: {config.ffmpeg}")

    source_pairs = _collect_source_pairs(config.dataset.source_dirs)
    source_summaries = []
    for source_dir in config.dataset.source_dirs:
        source_summaries.append(
            {
                "path": source_reference(source_dir),
                "wav_count": len(list(source_dir.glob("*.wav"))),
                "srt_count": len(list(source_dir.glob("*.srt"))),
            }
        )

    build_root = config.root / "tmp" / f"dataset-build-{uuid.uuid4().hex[:8]}"
    build_segments = build_root / "segments"
    build_manifests = build_root / "manifests"
    build_reports = build_root / "reports"
    build_manifests.mkdir(parents=True, exist_ok=True)
    build_reports.mkdir(parents=True, exist_ok=True)

    records: list[SegmentRecord] = []
    report: dict[str, object] = {
        "source_directories": source_summaries,
        "source_wav_count": len(source_pairs),
        "source_srt_count": len(source_pairs),
        "segments": 0,
        "skipped_short": 0,
        "skipped_long": 0,
        "corrections": [],
        "emotion_counts": {},
    }
    emotion_counts: dict[str, int] = defaultdict(int)

    try:
        serial = 0
        for source_dir, wav, srt in source_pairs:
            source_wav = source_reference(wav)
            source_srt = source_reference(srt)
            duration = wav_duration(wav)
            emotion = extract_emotion(wav)
            cues = parse_srt(srt)
            for cue in cues:
                corrections: list[str] = []
                start = max(0.0, cue.start_seconds - config.dataset.lead_padding_ms / 1000.0)
                end = min(duration, cue.end_seconds + config.dataset.tail_padding_ms / 1000.0)
                if cue.end_seconds > duration:
                    correction = (
                        f"{source_srt}#{cue.index}: 字幕结束 {cue.end_seconds:.3f}s "
                        f"超过音频 {duration:.3f}s，已截断"
                    )
                    corrections.append(correction)
                    report["corrections"].append(correction)  # type: ignore[union-attr]

                segment_duration = end - start
                if segment_duration < config.dataset.minimum_duration_seconds:
                    report["skipped_short"] = int(report["skipped_short"]) + 1
                    log(f"[跳过过短] {source_srt}#{cue.index} {segment_duration:.2f}s")
                    continue
                if segment_duration > config.dataset.maximum_duration_seconds:
                    report["skipped_long"] = int(report["skipped_long"]) + 1
                    log(f"[跳过过长] {source_srt}#{cue.index} {segment_duration:.2f}s")
                    continue

                serial += 1
                filename = f"rem_{emotion}_{serial:04d}.wav"
                build_path = build_segments / emotion / filename
                final_path = config.dataset_segments / emotion / filename
                log(f"[{serial:03d}] {source_wav}#{cue.index} -> {emotion}/{filename}")
                _run_ffmpeg(
                    config.ffmpeg,
                    wav,
                    build_path,
                    start=start,
                    end=end,
                    sample_rate=config.dataset.sample_rate,
                    channels=config.dataset.channels,
                )
                records.append(
                    SegmentRecord(
                        path=str(final_path),
                        speaker=config.speaker,
                        language=config.language,
                        text=cue.text,
                        emotion=emotion,
                        source_wav=source_wav,
                        source_srt=source_srt,
                        cue_index=cue.index,
                        start_seconds=round(start, 3),
                        end_seconds=round(end, 3),
                        duration_seconds=round(segment_duration, 3),
                        corrections=tuple(corrections),
                    )
                )
                emotion_counts[emotion] += 1

        if not records:
            raise RuntimeError("没有生成任何有效训练片段")

        train, valid = _split_train_valid(
            records,
            ratio=config.dataset.validation_ratio,
            seed=config.dataset.random_seed,
        )
        manifest_sets = {
            "rem-ja-all.list": records,
            "rem-ja-train.list": train,
            "rem-ja-valid.list": valid,
        }
        for filename, subset in manifest_sets.items():
            (build_manifests / filename).write_text(
                "\n".join(record.list_line() for record in subset) + "\n",
                encoding="utf-8",
            )

        report["segments"] = len(records)
        report["training_segments"] = len(train)
        report["validation_segments"] = len(valid)
        report["emotion_counts"] = dict(sorted(emotion_counts.items()))
        report["records"] = [asdict(record) for record in records]
        (build_reports / "dataset-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        final_segments = config.dataset.output_dir / "segments"
        final_manifests = config.dataset.output_dir / "manifests"
        final_reports = config.dataset.output_dir / "reports"
        _safe_remove_tree(final_segments, config.root)
        _safe_remove_tree(final_manifests, config.root)
        final_reports.mkdir(parents=True, exist_ok=True)
        shutil.move(str(build_segments), str(final_segments))
        shutil.move(str(build_manifests), str(final_manifests))
        shutil.copy2(build_reports / "dataset-report.json", final_reports / "dataset-report.json")
        log(
            f"数据集完成：{len(records)} 条，训练 {len(train)} 条，"
            f"验证 {len(valid)} 条。"
        )
        return report
    finally:
        if build_root.exists():
            _safe_remove_tree(build_root, config.root)
