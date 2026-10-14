"""Rem Companion 统一 TTS API 客户端。

悬浮对话框、命令行和后续本地 LLM 都应通过本模块合成语音，避免调用方漏传
已验证的中文分句与采样参数，退回 GPT-SoVITS 的 ``cut5``/随机 seed 默认值。
"""

from __future__ import annotations

from pathlib import Path
from contextlib import nullcontext
import json
import urllib.error
import urllib.request

from .config import AppConfig


def build_tts_payload(
    config: AppConfig,
    text: str,
    *,
    text_language: str | None = None,
    speech_mode: str | None = None,
    emotion: str | None = None,
) -> dict[str, object]:
    """构造确定性的双语 TTS 请求。

    ``speech_mode`` 是悬浮框使用的显式开关；``text_language`` 保留给 CLI
    和旧调用方。两者都未提供时，使用悬浮框配置中的默认模式。
    """

    normalized_text = text.strip()
    if not normalized_text:
        raise ValueError("TTS 文本不能为空")
    inference = config.tts_inference
    selected_mode = (
        speech_mode
        or text_language
        or config.companion.default_speech_mode
    ).lower()
    base_profile = config.tts_profiles.get(selected_mode)
    if base_profile is None:
        supported = ", ".join(sorted(config.tts_profiles))
        raise ValueError(
            f"不支持的朗读模式 {selected_mode!r}；可用模式: {supported}"
        )
    # 情绪路由是可选增强：只有配置过的情绪才覆盖基础参考音频。
    # 这样模型返回未知情绪、旧配置没有情绪表或提醒音频没有情绪时，
    # 都会安全回退到当前已验证的默认声音。
    normalized_emotion = (
        emotion.strip().lower().replace("-", "_")
        if emotion
        else ""
    )
    profile = config.tts_emotion_profiles.get(selected_mode, {}).get(
        normalized_emotion,
        base_profile,
    )
    from .secure_resources import ResourceVault
    if not profile.reference_audio.is_file() and not ResourceVault(config.root).contains(profile.reference_audio):
        raise FileNotFoundError(f"TTS 参考音频不存在: {profile.reference_audio}")

    return {
        "text": normalized_text,
        "text_lang": profile.text_language,
        "ref_audio_path": str(profile.reference_audio),
        "prompt_lang": profile.reference_language,
        "prompt_text": profile.reference_text,
        "text_split_method": profile.text_split_method,
        "seed": inference.seed,
        "top_k": inference.top_k,
        "top_p": inference.top_p,
        "temperature": inference.temperature,
        "speed_factor": inference.speed_factor,
        "repetition_penalty": inference.repetition_penalty,
        "batch_size": 1,
        "media_type": "wav",
        "streaming_mode": False,
    }


def synthesize_to_file(
    config: AppConfig,
    text: str,
    output: Path,
    *,
    text_language: str | None = None,
    speech_mode: str | None = None,
    emotion: str | None = None,
    timeout_seconds: int = 180,
) -> Path:
    """调用本机 GPT-SoVITS，并原子保存经过 RIFF/WAVE 校验的音频。"""

    payload = build_tts_payload(
        config,
        text,
        text_language=text_language,
        speech_mode=speech_mode,
        emotion=emotion,
    )
    from .secure_resources import ResourceVault
    vault=ResourceVault(config.root)
    headers={"Content-Type": "application/json; charset=utf-8"}
    lease=nullcontext(None)
    if vault.enabled:
        vault.require('inference')
        if config.services.tts_host not in ('127.0.0.1','localhost','::1'):raise PermissionError('受保护声线仅允许本机回环推理')
        token=vault.store.get('protected-tts-session')
        if not token:raise PermissionError('受保护TTS服务尚未启动或凭据不可用')
        headers['Authorization']='Bearer '+token
        lease=vault.lease([Path(str(payload['ref_audio_path']))])
    endpoint = (
        f"http://{config.services.tts_host}:{config.services.tts_port}/tts"
    )
    try:
        with lease as active:
            if active is not None:
                name=Path(str(payload['ref_audio_path'])).absolute().relative_to(vault.root).as_posix()
                payload['ref_audio_path']=str(active.paths[name])
            if vault.enabled:
                from .protected_tts import request as protected_request
                audio,content_type=protected_request(config,token,payload,timeout=timeout_seconds)
            else:
                body=json.dumps(payload,ensure_ascii=False).encode('utf-8')
                request=urllib.request.Request(endpoint,data=body,headers=headers,method='POST')
                with urllib.request.urlopen(request,timeout=timeout_seconds) as response:
                    audio=response.read();content_type=response.headers.get('Content-Type','')
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"TTS API 返回 HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise ConnectionError(f"无法连接 TTS API {endpoint}: {error.reason}") from error

    if not content_type.lower().startswith("audio/"):
        raise RuntimeError(f"TTS API 未返回音频: Content-Type={content_type!r}")
    if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise RuntimeError("TTS API 返回的数据不是有效 WAV")

    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".part")
    partial.write_bytes(audio)
    partial.replace(output)
    return output
