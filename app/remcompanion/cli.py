"""控制中心对应的命令行入口。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .config import ensure_project_directories, load_config
from .dataset import prepare_dataset
from .dialogue import DialogueService
from .downloader import download_components
from .environment import format_doctor_results, run_doctor
from .gptsovits import prepare_official_features, start_tts_api, train_models
from .launcher import start_companion_overlay
from .llm_client import ensure_llm_server, llm_base_url
from .memory import initialize_memory_database
from .tts_client import synthesize_to_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rem Companion 控制工具")
    parser.add_argument(
        "command",
        choices=(
            "doctor",
            "download",
            "prepare",
            "features",
            "train",
            "start-tts",
            "start-llm",
            "speak",
            "chat",
            "companion",
            "all",
        ),
    )
    parser.add_argument("--text", help="speak/chat 命令使用的文本")
    parser.add_argument("--language", default=None, help="文本语言，例如 zh、ja")
    parser.add_argument(
        "--speech-mode",
        choices=("zh", "ja"),
        default=None,
        help="chat 的语音模式；显示回答始终为中文",
    )
    parser.add_argument(
        "--no-audio",
        action="store_true",
        help="chat 只生成中文回答，不调用 TTS",
    )
    parser.add_argument("--output", default=None, help="输出 WAV 路径")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config()
    ensure_project_directories(config)
    database = initialize_memory_database(config.root / "data" / "memory.sqlite3")
    database.close()

    if args.command == "doctor":
        print(format_doctor_results(run_doctor(config)))
    elif args.command == "download":
        download_components(config)
    elif args.command == "prepare":
        prepare_dataset(config)
    elif args.command == "features":
        # 单独重建并验收 GPT-SoVITS 特征，便于在正式训练前排除数据问题。
        prepare_official_features(config)
    elif args.command == "train":
        train_models(config)
    elif args.command == "start-tts":
        start_tts_api(config)
    elif args.command == "start-llm":
        ensure_llm_server(config)
        print(llm_base_url(config))
    elif args.command == "speak":
        if not args.text:
            parser.error("speak 命令必须提供 --text")
        output = (
            Path(args.output)
            if args.output
            else config.root / "data" / "audio-cache" / "rem-last.wav"
        )
        result = synthesize_to_file(
            config,
            args.text,
            output,
            text_language=args.language,
        )
        print(result)
    elif args.command == "chat":
        if not args.text:
            parser.error("chat 命令必须提供 --text")
        result = DialogueService(config).respond(
            args.text,
            speech_mode=args.speech_mode,
            synthesize_audio=not args.no_audio,
        )
        print(result.reply_zh)
        if result.audio_path is not None:
            print(result.audio_path)
        if result.audio_error:
            print(f"[语音警告] {result.audio_error}", file=sys.stderr)
    elif args.command == "companion":
        start_companion_overlay(config)
    elif args.command == "all":
        download_components(config)
        prepare_dataset(config)
        train_models(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
