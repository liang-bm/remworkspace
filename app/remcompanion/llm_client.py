"""统一的 llama.cpp 本地大模型服务与 OpenAI 兼容客户端。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .backend_processes import read_process_identity, record_process_identity, stop_owned_process
from .config import AppConfig
from .memory import StoredMemory, StoredMessage
from .model_providers import ProviderConfig
from .process import project_environment
from .platforms import native_module
from .session_telemetry import record_usage


LogCallback = Callable[[str], None]
ALLOWED_EMOTIONS = {
    "neutral",
    "gentle",
    "happy",
    "concern",
    "question",
    "shy",
    "coquettish",
    "surprised",
    "sad",
    "little_angry",
}
ASCII_LETTER_PATTERN = re.compile(r"[A-Za-z]")
KANA_PATTERN = re.compile(r"[\u3040-\u30ff]")
HAN_PATTERN = re.compile(r"[\u3400-\u9fff]")

REPLY_JSON_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "rem_companion_reply",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "reply_zh": {"type": "string"},
                "speech_ja": {"type": "string"},
                "emotion": {
                    "type": "string",
                    "enum": sorted(ALLOWED_EMOTIONS),
                },
                "memories": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "category": {"type": "string"},
                            "importance": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                        },
                        "required": ["content", "category", "importance"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["reply_zh", "speech_ja", "emotion", "memories"],
            "additionalProperties": False,
        },
    },
}


@dataclass(frozen=True)
class LLMReply:
    """一次模型回复的中文展示文本、日语朗读文本和记忆增量。"""

    reply_zh: str
    speech_ja: str
    emotion: str
    memories: list[dict[str, object]]


def llm_base_url(config: AppConfig) -> str:
    return f"http://{config.services.llm_host}:{config.services.llm_port}"


def _port_open(host: str, port: int) -> bool:
    with socket.socket() as client:
        client.settimeout(0.35)
        return client.connect_ex((host, port)) == 0


def is_llm_ready(config: AppConfig) -> bool:
    if not _port_open(config.services.llm_host, config.services.llm_port):
        return False
    request = urllib.request.Request(
        llm_base_url(config) + "/health",
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError):
        return False


def start_llm_server(config: AppConfig, log: LogCallback = print) -> int:
    """按统一配置启动 llama-server；已运行时返回 0。"""

    if is_llm_ready(config):
        log(
            f"[跳过] 本地大模型已在 "
            f"{config.services.llm_host}:{config.services.llm_port} 运行"
        )
        return 0
    if not config.llama_server.is_file():
        raise FileNotFoundError(f"llama-server 不存在: {config.llama_server}")
    if not config.qwen_model.is_file():
        raise FileNotFoundError(f"Qwen 模型不存在: {config.qwen_model}")

    log_file = config.root / "logs" / "llm-server.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(config.llama_server),
        "--model",
        str(config.qwen_model),
        "--alias",
        config.llm.model,
        "--host",
        config.services.llm_host,
        "--port",
        str(config.services.llm_port),
        "--ctx-size",
        str(config.services.llm_context),
        "--n-gpu-layers",
        str(config.services.llm_gpu_layers),
        "--parallel",
        "1",
        "--threads-http",
        "4",
        "--jinja",
        "--reasoning",
        "off",
    ]
    with log_file.open("a", encoding="utf-8") as stream:
        process = subprocess.Popen(
            command,
            cwd=config.llama_server.parent,
            env=project_environment(config.root),
            stdout=stream,
            stderr=subprocess.STDOUT,
            **native_module("processes").detached_process_options(),
        )
    state = config.root / "data" / "llm-server.pid"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        str(process.pid),
        encoding="utf-8",
    )
    try:
        if record_process_identity(config.root, "llama-server", process.pid, config.llama_server) is None:
            log("[提示] 未取得大模型进程身份；退出时不会仅按 PID 终止它")
    except (OSError, ValueError):
        log("[提示] 无法保存大模型进程身份；退出时保留该进程")
    log(
        f"[启动] 本地大模型 PID={process.pid}, 地址 {llm_base_url(config)}"
    )
    return process.pid


def ensure_llm_server(
    config: AppConfig,
    *,
    log: LogCallback = print,
    wait_seconds: float = 90,
    cancel_event: threading.Event | None = None,
) -> int:
    """确保服务可用，并等待模型完成载入。"""

    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("本地大模型启动已取消")
    if is_llm_ready(config):
        return 0
    if not config.llm.auto_start:
        raise ConnectionError(
            f"本地大模型未运行: {llm_base_url(config)}。请在模型设置开启聊天时按需启动，或自行启动服务。"
        )
    pid = start_llm_server(config, log=log)
    try:
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("本地大模型启动已取消")
            if is_llm_ready(config):
                return pid
            time.sleep(0.5)
        raise TimeoutError(
            f"本地大模型在 {wait_seconds:.0f} 秒内没有就绪；"
            f"请检查 logs/llm-server.log"
        )
    finally:
        # 应用可能在 Popen/记录 PID 的短窗口内已经退出。此处仍持有
        # 本轮实际启动的 PID，按出生记录回收，不能等待桌面再次扫描。
        if pid and cancel_event is not None and cancel_event.is_set():
            try:
                identity = read_process_identity(config.root, "llama-server", pid, config.llama_server)
                if stop_owned_process(pid, config.llama_server, expected_identity=identity):
                    log(f"[取消] 已回收初始化中的大模型 PID={pid}")
            except OSError as error:
                log(f"[取消] 大模型进程暂未回收: {error}")


def build_chat_messages(
    config: AppConfig,
    history: Sequence[StoredMessage],
    memories: Sequence[StoredMemory],
    *,
    system_prompt: str | None = None,
    web_search_context: str = "",
) -> list[dict[str, str]]:
    """把人格、长期记忆和近期消息组装为唯一的模型上下文。"""

    output_contract = """
你必须只返回一个 JSON 对象，不要 Markdown，不要思考过程。字段如下：
- reply_zh：展示给用户的简体中文回答；
- speech_ja：与 reply_zh 含义一致、适合自然朗读的现代日语，不要机械直译；
  必须是完整的纯日语句子，至少包含假名，禁止夹杂英文字母；外来词必须写成片假名；
- emotion：neutral、gentle、happy、concern、question、shy、coquettish、
  surprised、sad、little_angry 之一。coquettish 仅用于自然的撒娇、亲昵、
  俏皮请求或回应亲密互动；普通礼貌、关怀和知识解释不要使用；
- memories：仅记录用户本轮明确表达、未来仍有用的稳定事实或偏好。每项包含
  content、category、importance；没有就返回空数组。不要保存密码、令牌或隐私推断。
展示层永远只使用 reply_zh，绝不能把日语或 JSON 暴露给用户。
    """.strip()
    active_prompt = (
        config.llm.system_prompt if system_prompt is None else system_prompt
    )
    system_parts = [
        part for part in (active_prompt, output_contract) if part
    ]
    if memories:
        memory_lines = "\n".join(
            f"- [{item.category}] {item.content}" for item in memories
        )
        # Qwen3.5 的官方 Jinja 聊天模板只允许 system 消息位于整个
        # messages 数组的最开头，并且不接受第二条 system 消息。此前将长期
        # 记忆作为单独的 system 项追加，存在记忆时 llama-server 会直接
        # 返回 HTTP 400（“System message must be at the beginning”）。
        # 将人格、输出契约与记忆合并为唯一的 system 消息，既符合模板约束，
        # 也避免健康端点正常但实际聊天始终失败的假阳性。
        system_parts.append(
            "以下是已确认的长期记忆，仅在相关时自然使用：\n"
            + memory_lines
        )

    # 添加联网搜索上下文
    if web_search_context:
        system_parts.append(
            "以下是联网搜索到的相关信息，请参考这些信息回答用户问题：\n"
            + web_search_context
        )

    messages: list[dict[str, str]] = [
        {"role": "system", "content": "\n\n".join(system_parts)}
    ]
    messages.extend(
        {"role": item.role, "content": item.content}
        for item in history
        if item.role in {"user", "assistant"}
    )
    return messages


def _extract_json_object(content: str) -> dict[str, object]:
    normalized = content.strip()
    if normalized.startswith("```"):
        normalized = normalized.removeprefix("```json").removeprefix("```")
        normalized = normalized.removesuffix("```").strip()
    start = normalized.find("{")
    end = normalized.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型没有返回 JSON 对象")
    parsed = json.loads(normalized[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("模型返回的 JSON 根节点不是对象")
    return parsed


def parse_llm_reply(content: str) -> LLMReply:
    parsed = _extract_json_object(content)
    reply_zh = str(parsed.get("reply_zh", "")).strip()
    speech_ja = str(parsed.get("speech_ja", "")).strip()
    if not reply_zh:
        raise ValueError("模型返回的 reply_zh 为空")
    emotion = str(parsed.get("emotion", "gentle")).lower()
    if emotion not in ALLOWED_EMOTIONS:
        emotion = "gentle"
    raw_memories = parsed.get("memories", [])
    memories: list[dict[str, object]] = []
    if isinstance(raw_memories, list):
        for item in raw_memories:
            if not isinstance(item, dict):
                continue
            content = " ".join(str(item.get("content", "")).strip().split())
            if not content:
                continue
            category = str(item.get("category", "fact")).strip() or "fact"
            try:
                importance = float(item.get("importance", 0.5))
            except (TypeError, ValueError):
                importance = 0.5
            memories.append(
                {
                    "content": content,
                    "category": category,
                    # 小模型偶尔会越过 JSON Schema 的数值边界；解析层与
                    # SQLite 层都夹紧范围，避免异常值继续向下游传播。
                    "importance": min(1.0, max(0.0, importance)),
                }
            )
    return LLMReply(
        reply_zh=reply_zh,
        speech_ja=speech_ja,
        emotion=emotion,
        memories=memories,
    )


def is_clean_japanese_speech(text: str) -> bool:
    """拒绝夹杂英文或主体仍是中文的“日语”朗读稿。

    日语本身会使用汉字，所以不能简单排除 CJK 字符。自然的完整日语句子通常
    会包含助词、活用词尾等假名；要求假名至少占“假名 + 汉字”的四分之一，
    可以挡住“小段日语 + 大段中文”这种本地小模型常见的失败结果。
    """

    normalized = text.strip()
    if not normalized or ASCII_LETTER_PATTERN.search(normalized):
        return False
    kana_count = len(KANA_PATTERN.findall(normalized))
    han_count = len(re.findall(r"[\u3400-\u9fff]", normalized))
    language_characters = kana_count + han_count
    return (
        kana_count >= 2
        and language_characters > 0
        and kana_count / language_characters >= 0.25
    )


def _official_openai_reasoning(provider: ProviderConfig | None) -> bool:
    """只对官方端点的已知推理系列适配，不猜测第三方兼容服务器。"""
    return bool(
        provider is not None
        and not provider.is_local
        and urlparse(provider.endpoint_url).hostname == "api.openai.com"
        and re.match(r"^(?:gpt-5(?:[.-]|$)|o[134](?:-|$))", provider.model)
    )


def _request_content(
    config: AppConfig,
    messages: Sequence[dict[str, object]],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    response_format: dict[str, object] | None = None,
    provider: ProviderConfig | None = None,
    api_key: str | None = None,
    plain_text: bool = False,
) -> str:
    """调用本地或远程 OpenAI 兼容端点并返回模型文本。

    ``provider is None`` 严格保持旧版 llama.cpp 请求体，避免升级后改变已经
    验证稳定的本地 Qwen 行为。远程请求不携带 llama.cpp 私有参数。
    """

    active = provider
    if active is not None:
        active.ensure_chat_supported()
    endpoint = (
        llm_base_url(config) + config.llm.endpoint_path
        if active is None
        else active.endpoint_url
    )
    model = config.llm.model if active is None else active.model
    body_data: dict[str, object] = {
        "model": model,
        "messages": list(messages),
        "temperature": (
            config.llm.temperature if temperature is None else temperature
        ),
        "top_p": config.llm.top_p,
        "max_tokens": config.llm.max_tokens if max_tokens is None else max_tokens,
        "stream": False,
    }
    if active is not None:
        limit = active.active_model.max_output_tokens
        if limit is not None:
            body_data["max_tokens"] = min(int(body_data["max_tokens"]), limit)
        if not active.send_sampling:
            body_data.pop("temperature", None)
            body_data.pop("top_p", None)
        if active.output_parameter == "max_completion_tokens":
            body_data["max_completion_tokens"] = body_data.pop("max_tokens")
    if _official_openai_reasoning(active):
        # GPT-5-mini 等不接受 temperature/top_p；新版本也受 reasoning
        # 设置约束。保守地省略采样参数，使用官方 completion token 字段。
        # 本地 Qwen 与其他兼容接口的历史请求体完全不变。
        body_data.pop("temperature", None)
        body_data.pop("top_p", None)
        if "max_tokens" in body_data:
            body_data["max_completion_tokens"] = body_data.pop("max_tokens")
        if re.fullmatch(r"gpt-5(?:-(?:mini|nano))?(?:-\d{4}-\d{2}-\d{2})?", model):
            # 旧 GPT-5 默认 medium，短聊天预算可能全花在推理上。
            # 仅已知支持的旧系列用 minimal；不提高计费 token 上限，
            # 也不向 pro/未知版本猜测传入 none 或 minimal。
            body_data["reasoning_effort"] = "minimal"
    if not plain_text and (active is None or active.structured_output != "prompt"):
        body_data["response_format"] = response_format or {"type": "json_object"}
    if active is None or active.is_local:
        body_data["chat_template_kwargs"] = {"enable_thinking": False}
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if active is not None and active.requires_api_key:
        if not api_key:
            raise ValueError(f"{active.display_name} 尚未保存 API Key")
    body = json.dumps(body_data, ensure_ascii=False).encode("utf-8")
    # 调试副本包含历史和记忆，不能默认额外落盘，也不能阻断正常请求。
    if os.environ.get("REMCHAT_DEBUG_REQUESTS") == "1":
        try:
            debug_path = config.root / "tmp" / "last_llm_request.json"
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_bytes(body)
        except OSError:
            pass
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers=headers,
    )
    if active is not None and active.requires_api_key:
        # urllib copies ordinary headers into redirected requests. A credential
        # belongs to this configured endpoint, never to a redirect destination.
        request.add_unredirected_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(
            request,
            timeout=config.llm.timeout_seconds,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
            record_usage(payload, model)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        if api_key:
            detail = detail.replace(api_key, "[REDACTED]")
        source = "本地大模型" if active is None or active.is_local else active.display_name
        raise RuntimeError(
            f"{source} 返回 HTTP {error.code}: {detail}"
        ) from error
    except urllib.error.URLError as error:
        source = "本地大模型" if active is None or active.is_local else active.display_name
        raise ConnectionError(
            f"无法连接{source} {endpoint}: {error.reason}"
        ) from error

    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        source = "本地大模型" if active is None or active.is_local else active.display_name
        raise RuntimeError(f"无法解析{source}响应：缺少 choices[0].message.content，请检查接口协议") from error
    if not isinstance(content, str) or not content.strip():
        reason = payload["choices"][0].get("finish_reason", "unknown")
        raise RuntimeError(
            f"模型接口没有返回可见文本（finish_reason={reason}）；"
            "推理模型的输出预算包含思考 token，请检查输出 token 上限。"
        )
    return content


def _plain_chinese_fallback(content: str) -> LLMReply | None:
    """最后兜底：模型若返回可用中文纯文本，就直接显示而不是报 JSON 错误。"""

    normalized = re.sub(
        r"<think>.*?</think>",
        "",
        content,
        flags=re.DOTALL | re.IGNORECASE,
    ).strip()
    normalized = normalized.removeprefix("```").removesuffix("```").strip()
    normalized = re.sub(
        r"^(?:reply_zh|回答)\s*[:：]\s*",
        "",
        normalized,
        flags=re.IGNORECASE,
    ).strip()
    if normalized.lower().startswith("reply_zh"):
        first_line = normalized.splitlines()[0]
        normalized = first_line.split(":", 1)[-1].split("：", 1)[-1].strip()
    han_count = len(HAN_PATTERN.findall(normalized))
    kana_count = len(KANA_PATTERN.findall(normalized))
    if han_count < 2 or kana_count > max(2, han_count // 4):
        return None
    return LLMReply(
        reply_zh=normalized,
        speech_ja="",
        emotion="gentle",
        memories=[],
    )


def _repair_structured_reply(
    config: AppConfig,
    raw_content: str,
    *,
    provider: ProviderConfig | None = None,
    api_key: str | None = None,
) -> LLMReply:
    """把偶发的纯文本或残缺结构转换成严格回复对象。"""

    repair_messages = [
        {
            "role": "system",
            "content": (
                "你是格式修复器。把输入内容整理成简体中文桌面陪伴回复。"
                "只返回符合给定 JSON Schema 的对象；不要解释、不要 Markdown。"
                "缺失的日语可返回空字符串，无法确认的记忆返回空数组。"
            ),
        },
        {
            "role": "user",
            "content": raw_content[:4000],
        },
    ]
    repaired = _request_content(
        config,
        repair_messages,
        temperature=0.10,
        max_tokens=min(320, config.llm.max_tokens),
        response_format=(
            REPLY_JSON_SCHEMA
            if provider is None or provider.structured_output == "json_schema"
            else {"type": "json_object"}
        ),
        provider=provider,
        api_key=api_key,
    )
    return parse_llm_reply(repaired)


def _repair_japanese_speech(
    config: AppConfig,
    reply: LLMReply,
    *,
    provider: ProviderConfig | None = None,
    api_key: str | None = None,
) -> str:
    """仅在朗读稿不合格时，用同一模型端点做一次低温校对。"""

    messages = [
        {
            "role": "system",
            "content": (
                "あなたは日本語ナレーションの翻訳者です。簡体字中国語の原文を、"
                "意味を変えず自然で完全な現代日本語へ翻訳してください。"
                "中国語を残さず、英字を使わず、外来語はカタカナで書いてください。"
                "返答は JSON のみです：{\"speech_ja\":\"...\"}。"
            ),
        },
        {
            "role": "user",
            "content": (
                "例：\n"
                "原文：欢迎回家，我会安静地陪伴你。\n"
                '返答：{"speech_ja":"おかえりなさい。静かにそばにいます。"}\n'
                "原文：好的，我会提醒你早点休息。祝你有个好的睡眠。\n"
                "返答："
                '{"speech_ja":"はい、早めに休むようお声がけしますね。'
                'どうか良い眠りを。"}\n\n'
                "次の原文を翻訳してください：\n"
                + reply.reply_zh
            ),
        },
    ]
    content = _request_content(
        config,
        messages,
        temperature=0.15,
        max_tokens=180,
        provider=provider,
        api_key=api_key,
    )
    parsed = _extract_json_object(content)
    repaired = str(parsed.get("speech_ja", "")).strip()
    return repaired if is_clean_japanese_speech(repaired) else ""


def request_reply(
    config: AppConfig,
    messages: Sequence[dict[str, object]],
    *,
    polish_japanese: bool = False,
    provider: ProviderConfig | None = None,
    api_key: str | None = None,
) -> LLMReply:
    """生成结构化回答，并按需用同一模型专门润色日语朗读稿。

    中文朗读模式不需要额外调用；日语朗读模式则用一次低温翻译换取稳定语义。
    """

    preferred_format = (
        REPLY_JSON_SCHEMA
        if provider is None or provider.structured_output == "json_schema"
        else {"type": "json_object"}
    )
    try:
        raw_content = _request_content(
            config,
            messages,
            response_format=preferred_format,
            provider=provider,
            api_key=api_key,
        )
    except RuntimeError:
        # 仅 json_schema 路径需要兼容降级；原本就是 json_object 时保留原错误。
        # 远程 API 的 HTTP 错误可能是鉴权、限额或计费问题，不能因为
        # 本地 llama.cpp 的历史兼容逻辑而自动重复计费。
        if (
            preferred_format.get("type") != "json_schema"
            or (provider is not None and not provider.is_local)
        ):
            raise
        raw_content = _request_content(
            config,
            messages,
            provider=provider,
            api_key=api_key,
        )

    try:
        reply = parse_llm_reply(raw_content)
    except (ValueError, json.JSONDecodeError):
        try:
            reply = _repair_structured_reply(
                config,
                raw_content,
                provider=provider,
                api_key=api_key,
            )
        except (
            ConnectionError,
            RuntimeError,
            TimeoutError,
            ValueError,
            json.JSONDecodeError,
        ):
            fallback = _plain_chinese_fallback(raw_content)
            if fallback is None:
                raise ValueError("本地模型返回内容不可用，请重新发送。")
            reply = fallback
    initial_is_clean = is_clean_japanese_speech(reply.speech_ja)
    if not polish_japanese and (not reply.speech_ja or initial_is_clean):
        return reply

    try:
        repaired = _repair_japanese_speech(
            config,
            reply,
            provider=provider,
            api_key=api_key,
        )
    except (ConnectionError, RuntimeError, TimeoutError, ValueError, json.JSONDecodeError):
        # 专项润色失败时，已合格的初稿仍可用；混合文本则绝不能送进 TTS。
        repaired = reply.speech_ja if initial_is_clean else ""
    return LLMReply(
        reply_zh=reply.reply_zh,
        speech_ja=repaired,
        emotion=reply.emotion,
        memories=reply.memories,
    )


def test_provider_connection(
    config: AppConfig,
    provider: ProviderConfig,
    *,
    api_key: str | None = None,
) -> str:
    """执行不写入会话或记忆的最小连通性测试。"""

    content = _request_content(
        config,
        [
            {
                "role": "user",
                "content": '只返回 JSON：{"status":"ok"}',
            }
        ],
        temperature=0,
        # 推理模型会将思考计入预算，32 token 不能证明正常聊天连通性。
        # 保留用户配置的预算，不偷偷扩张付费接口的上限。
        max_tokens=config.llm.max_tokens if _official_openai_reasoning(provider) else 32,
        response_format={"type": "json_object"},
        provider=provider,
        api_key=api_key,
    )
    if not content.strip():
        raise RuntimeError("模型接口返回了空内容")
    return content
