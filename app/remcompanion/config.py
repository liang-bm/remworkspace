"""配置读取与项目路径管理。

控制中心故意只依赖 Python 标准库，使用户在尚未安装训练环境时也能启动下载界面。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import sys
import tomllib
from typing import Any

from .platforms import native_module


@dataclass(frozen=True)
class DatasetConfig:
    source_dirs: tuple[Path, ...]
    output_dir: Path
    sample_rate: int
    channels: int
    lead_padding_ms: int
    tail_padding_ms: int
    minimum_duration_seconds: float
    maximum_duration_seconds: float
    validation_ratio: float
    random_seed: int

    @property
    def source_dir(self) -> Path:
        """兼容旧调用方；新代码应遍历 ``source_dirs``。"""

        return self.source_dirs[0]


@dataclass(frozen=True)
class TrainingConfig:
    version: str
    experiment_name: str
    gpu_index: str
    use_fp16: bool
    sovits_batch_size: int
    sovits_epochs: int
    sovits_save_every_epoch: int
    sovits_gradient_checkpointing: bool
    gpt_batch_size: int
    gpt_epochs: int
    gpt_save_every_epoch: int
    gpt_use_dpo: bool


@dataclass(frozen=True)
class ServiceConfig:
    tts_host: str
    tts_port: int
    llm_host: str
    llm_port: int
    llm_context: int
    llm_gpu_layers: int


@dataclass(frozen=True)
class TTSInferenceConfig:
    """悬浮对话框与 CLI 共用的稳定 TTS 推理参数。"""

    reference_audio: Path
    reference_text: str
    reference_language: str
    default_text_language: str
    text_split_method: str
    seed: int
    top_k: int
    top_p: float
    temperature: float
    speed_factor: float
    repetition_penalty: float


@dataclass(frozen=True)
class TTSProfileConfig:
    """一种朗读语言对应的参考音频和文本前端配置。"""

    text_language: str
    reference_audio: Path
    reference_text: str
    reference_language: str
    text_split_method: str


@dataclass(frozen=True)
class LLMConfig:
    """统一的本地大模型接口参数。"""

    model: str
    endpoint_path: str
    auto_start: bool
    temperature: float
    top_p: float
    max_tokens: int
    history_messages: int
    memory_items: int
    timeout_seconds: int
    system_prompt: str


@dataclass(frozen=True)
class CompanionConfig:
    """悬浮对话框的行为配置。"""

    default_speech_mode: str
    autoplay: bool
    follow_pet: bool
    window_width: int
    window_height: int
    follow_interval_ms: int


@dataclass(frozen=True)
class AppConfig:
    root: Path
    speaker: str
    language: str
    dataset: DatasetConfig
    training: TrainingConfig
    services: ServiceConfig
    tts_inference: TTSInferenceConfig
    tts_profiles: dict[str, TTSProfileConfig]
    tts_emotion_profiles: dict[str, dict[str, TTSProfileConfig]]
    llm: LLMConfig
    companion: CompanionConfig
    downloads: dict[str, Any]

    @property
    def app_dir(self) -> Path:
        return self.root / "app"

    @property
    def gpt_sovits_dir(self) -> Path:
        return self.root / "engines" / "GPT-SoVITS"

    @property
    def gpt_python(self) -> Path:
        return native_module("runtime").environment_python(self.root, "gpt-sovits")

    @property
    def ffmpeg(self) -> Path:
        return native_module("runtime").engine_executable(self.root, "ffmpeg", "ffmpeg")

    @property
    def llama_server(self) -> Path:
        return native_module("runtime").engine_executable(self.root, "llama.cpp", "llama-server")

    @property
    def qwen_model(self) -> Path:
        return self.root / "models" / "llm" / str(self.downloads["qwen_filename"])

    @property
    def dataset_manifest(self) -> Path:
        return self.dataset.output_dir / "manifests" / "rem-ja-all.list"

    @property
    def dataset_segments(self) -> Path:
        return self.dataset.output_dir / "segments"

    @property
    def experiment_dir(self) -> Path:
        return self.root / "data" / "training" / self.training.experiment_name

    @property
    def fast_langdetect_model(self) -> Path:
        """GPT-SoVITS/split-lang 推理阶段使用的完整 FastText 语言模型。"""

        return (
            self.gpt_sovits_dir
            / "GPT_SoVITS"
            / "pretrained_models"
            / "fast_langdetect"
            / "lid.176.bin"
        )


def _discover_root() -> Path:
    """优先使用 REM_HOME，再从 EXE 邻近目录或包路径寻找项目配置。"""

    explicit = os.environ.get("REM_HOME")
    if explicit:
        return Path(explicit).resolve()

    # PyInstaller onedir 版位于 ``项目根/desktop-app/RemCompanion.exe``。
    # 模型和配置仍保存在项目根，避免把数 GB 权重复制进 EXE。
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        for candidate in (executable_dir, executable_dir.parent):
            if (candidate / "config" / "rem_companion.toml").exists():
                return candidate

    for parent in Path(__file__).resolve().parents:
        if (parent / "config" / "rem_companion.toml").exists():
            return parent
    raise FileNotFoundError("无法定位 config/rem_companion.toml，请通过一键启动脚本运行。")


def _project_path(root: Path, value: object) -> Path:
    """相对配置兼容 Windows 分隔符；WSL 不把 F: 当作文件夹名。"""
    return native_module("runtime").project_path(root, value)


def load_config(config_path: Path | None = None) -> AppConfig:
    root = _discover_root()
    path = config_path or root / "config" / "rem_companion.toml"
    with path.open("rb") as stream:
        raw = tomllib.load(stream)

    configured_root = Path(raw["project"]["root"])
    # 部署后的实际目录优先于配置中的默认目录，便于复制到别的盘符进行测试。
    if (root / "config" / "rem_companion.toml").resolve() == path.resolve():
        configured_root = root

    dataset_raw = raw["dataset"]
    configured_sources = dataset_raw.get("source_dirs")
    if configured_sources is None:
        # 兼容早期仅支持一个素材目录的配置文件。
        configured_sources = [dataset_raw["source_dir"]]
    if not isinstance(configured_sources, list) or not configured_sources:
        raise ValueError("dataset.source_dirs 必须是非空目录列表")
    from .resource_vault import resolve_source_directory
    dataset = DatasetConfig(
        source_dirs=tuple(resolve_source_directory(configured_root, item) for item in configured_sources),
        output_dir=_project_path(configured_root, dataset_raw["output_dir"]),
        sample_rate=int(dataset_raw["sample_rate"]),
        channels=int(dataset_raw["channels"]),
        lead_padding_ms=int(dataset_raw["lead_padding_ms"]),
        tail_padding_ms=int(dataset_raw["tail_padding_ms"]),
        minimum_duration_seconds=float(dataset_raw["minimum_duration_seconds"]),
        maximum_duration_seconds=float(dataset_raw["maximum_duration_seconds"]),
        validation_ratio=float(dataset_raw["validation_ratio"]),
        random_seed=int(dataset_raw["random_seed"]),
    )

    training_raw = raw["training"]
    training = TrainingConfig(
        version=str(training_raw["version"]),
        experiment_name=str(training_raw["experiment_name"]),
        gpu_index=str(training_raw["gpu_index"]),
        use_fp16=bool(training_raw["use_fp16"]),
        sovits_batch_size=int(training_raw["sovits_batch_size"]),
        sovits_epochs=int(training_raw["sovits_epochs"]),
        sovits_save_every_epoch=int(training_raw["sovits_save_every_epoch"]),
        sovits_gradient_checkpointing=bool(training_raw["sovits_gradient_checkpointing"]),
        gpt_batch_size=int(training_raw["gpt_batch_size"]),
        gpt_epochs=int(training_raw["gpt_epochs"]),
        gpt_save_every_epoch=int(training_raw["gpt_save_every_epoch"]),
        gpt_use_dpo=bool(training_raw["gpt_use_dpo"]),
    )

    service_raw = raw["services"]
    services = ServiceConfig(
        tts_host=str(service_raw["tts_host"]),
        tts_port=int(service_raw["tts_port"]),
        llm_host=str(service_raw["llm_host"]),
        llm_port=int(service_raw["llm_port"]),
        llm_context=int(service_raw["llm_context"]),
        llm_gpu_layers=int(service_raw["llm_gpu_layers"]),
    )

    tts_raw = raw["tts_inference"]
    text_split_method = str(tts_raw["text_split_method"])
    if text_split_method not in {"cut0", "cut1", "cut2", "cut3", "cut4", "cut5"}:
        raise ValueError(f"不支持的 TTS 分句策略: {text_split_method}")
    tts_inference = TTSInferenceConfig(
        reference_audio=_project_path(configured_root, tts_raw["reference_audio"]),
        reference_text=str(tts_raw["reference_text"]),
        reference_language=str(tts_raw["reference_language"]),
        default_text_language=str(tts_raw["default_text_language"]),
        text_split_method=text_split_method,
        seed=int(tts_raw["seed"]),
        top_k=int(tts_raw["top_k"]),
        top_p=float(tts_raw["top_p"]),
        temperature=float(tts_raw["temperature"]),
        speed_factor=float(tts_raw["speed_factor"]),
        repetition_penalty=float(tts_raw["repetition_penalty"]),
    )

    profiles: dict[str, TTSProfileConfig] = {}
    for mode, profile_raw in raw.get("tts_profiles", {}).items():
        profile_split_method = str(
            profile_raw.get("text_split_method", text_split_method)
        )
        if profile_split_method not in {"cut0", "cut1", "cut2", "cut3", "cut4", "cut5"}:
            raise ValueError(
                f"不支持的 {mode} TTS 分句策略: {profile_split_method}"
            )
        profiles[str(mode)] = TTSProfileConfig(
            text_language=str(profile_raw["text_language"]),
            reference_audio=_project_path(configured_root, profile_raw["reference_audio"]),
            reference_text=str(profile_raw["reference_text"]),
            reference_language=str(profile_raw.get("reference_language", "ja")),
            text_split_method=profile_split_method,
        )
    if not profiles:
        # 兼容尚未增加 tts_profiles 的旧配置文件。
        profiles[tts_inference.default_text_language] = TTSProfileConfig(
            text_language=tts_inference.default_text_language,
            reference_audio=tts_inference.reference_audio,
            reference_text=tts_inference.reference_text,
            reference_language=tts_inference.reference_language,
            text_split_method=tts_inference.text_split_method,
        )

    # 情绪参考音频按“朗读语言 -> 情绪”分层配置。未配置某种情绪时，
    # TTS 客户端会回退到对应语言的基础 profile，保证旧配置和普通回复稳定。
    emotion_profiles: dict[str, dict[str, TTSProfileConfig]] = {}
    for mode, emotions_raw in raw.get("tts_emotion_profiles", {}).items():
        normalized_mode = str(mode).lower()
        base_profile = profiles.get(normalized_mode)
        if base_profile is None:
            raise ValueError(
                f"tts_emotion_profiles.{mode} 没有对应的基础 TTS profile"
            )
        if not isinstance(emotions_raw, dict):
            raise ValueError(
                f"tts_emotion_profiles.{mode} 必须是情绪配置表"
            )
        mode_profiles: dict[str, TTSProfileConfig] = {}
        for emotion, emotion_raw in emotions_raw.items():
            if not isinstance(emotion_raw, dict):
                raise ValueError(
                    f"tts_emotion_profiles.{mode}.{emotion} 必须是配置表"
                )
            normalized_emotion = str(emotion).lower().replace("-", "_")
            emotion_split_method = str(
                emotion_raw.get(
                    "text_split_method",
                    base_profile.text_split_method,
                )
            )
            if emotion_split_method not in {
                "cut0",
                "cut1",
                "cut2",
                "cut3",
                "cut4",
                "cut5",
            }:
                raise ValueError(
                    f"不支持的 {mode}/{emotion} TTS 分句策略: "
                    f"{emotion_split_method}"
                )
            mode_profiles[normalized_emotion] = TTSProfileConfig(
                text_language=str(
                    emotion_raw.get(
                        "text_language",
                        base_profile.text_language,
                    )
                ),
                reference_audio=(
                    _project_path(configured_root, emotion_raw["reference_audio"])
                ),
                reference_text=str(emotion_raw["reference_text"]),
                reference_language=str(
                    emotion_raw.get(
                        "reference_language",
                        base_profile.reference_language,
                    )
                ),
                text_split_method=emotion_split_method,
            )
        emotion_profiles[normalized_mode] = mode_profiles

    llm_raw = raw.get("llm", {})
    llm = LLMConfig(
        model=str(llm_raw.get("model", "rem-local")),
        endpoint_path=str(llm_raw.get("endpoint_path", "/v1/chat/completions")),
        auto_start=bool(llm_raw.get("auto_start", True)),
        temperature=float(llm_raw.get("temperature", 0.75)),
        top_p=float(llm_raw.get("top_p", 0.9)),
        max_tokens=int(llm_raw.get("max_tokens", 420)),
        history_messages=int(llm_raw.get("history_messages", 12)),
        memory_items=int(llm_raw.get("memory_items", 8)),
        timeout_seconds=int(llm_raw.get("timeout_seconds", 120)),
        system_prompt=str(llm_raw.get("system_prompt", "")).strip(),
    )

    companion_raw = raw.get("companion", {})
    default_speech_mode = str(
        companion_raw.get("default_speech_mode", "ja")
    ).lower()
    if default_speech_mode not in profiles:
        raise ValueError(
            f"companion.default_speech_mode 未配置对应 TTS profile: "
            f"{default_speech_mode}"
        )
    companion = CompanionConfig(
        default_speech_mode=default_speech_mode,
        autoplay=bool(companion_raw.get("autoplay", True)),
        follow_pet=bool(companion_raw.get("follow_pet", True)),
        window_width=max(360, int(companion_raw.get("window_width", 430))),
        window_height=max(280, int(companion_raw.get("window_height", 342))),
        follow_interval_ms=max(
            40, int(companion_raw.get("follow_interval_ms", 80))
        ),
    )

    return AppConfig(
        root=configured_root,
        speaker=str(raw["project"]["speaker"]),
        language=str(raw["project"]["language"]),
        dataset=dataset,
        training=training,
        services=services,
        tts_inference=tts_inference,
        tts_profiles=profiles,
        tts_emotion_profiles=emotion_profiles,
        llm=llm,
        companion=companion,
        downloads=native_module("runtime").download_settings(dict(raw["downloads"])),
    )


def ensure_project_directories(config: AppConfig) -> None:
    """创建程序使用的派生目录，但绝不创建或修改原始语音目录。"""

    relative_dirs = (
        "assets",
        "cache/huggingface",
        "cache/pip",
        "cache/torch",
        "cache/uv",
        "data/audio-cache",
        "data/training",
        "data/voice-dataset/manifests",
        "data/voice-dataset/reports",
        "data/voice-dataset/references",
        "data/voice-dataset/segments",
        "engines/ffmpeg",
        "engines/llama.cpp",
        "logs",
        "models/llm",
        "models/tts/rem/gpt",
        "models/tts/rem/sovits",
        "runtime",
        "tests",
        "tmp/downloads",
    )
    for relative in relative_dirs:
        (config.root / relative).mkdir(parents=True, exist_ok=True)
