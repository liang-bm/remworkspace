"""聊天模型提供商的非敏感配置与安全默认值。

该模块故意不读取或保存 API Key。密钥由 :mod:`secret_store` 负责，
从而避免模型参数导出、日志或配置损坏时意外泄露凭据。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
import json
import re
from urllib.parse import urlparse
from datetime import datetime, timezone
import uuid


PROVIDER_CONFIG_FILE = Path("data") / "model-providers.json"
LOCAL_PROVIDER_ID = "local-qwen"
PROVIDER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class ModelConfig:
    """Public, per-model settings. Unknown token limits are deliberately None."""

    model_id: str
    display_name: str = ""
    context_tokens: int | None = None
    max_output_tokens: int | None = None
    advanced_model_enabled: bool = False
    supports_images: bool = False

    def validate(self) -> "ModelConfig":
        if not isinstance(self.model_id, str) or not self.model_id.strip() or len(self.model_id) > 256:
            raise ValueError("模型 ID 不能为空，且不能超过 256 个字符")
        if any(char.isspace() or ord(char) < 32 for char in self.model_id):
            raise ValueError("模型 ID 不能包含空白或控制字符")
        if not isinstance(self.display_name, str) or len(self.display_name) > 256:
            raise ValueError("模型显示名称必须是 256 字符以内的文本")
        for limit in (self.context_tokens, self.max_output_tokens):
            if limit is not None and (type(limit) is not int or limit <= 0):
                raise ValueError("Token 限制必须是正整数；未知时留空")
        if self.context_tokens and self.max_output_tokens and self.max_output_tokens > self.context_tokens:
            raise ValueError("输出限制不能大于上下文限制")
        if any(type(flag) is not bool for flag in (self.advanced_model_enabled, self.supports_images)):
            raise ValueError("模型能力选项必须是布尔值")
        return self


@dataclass(frozen=True)
class ProviderConfig:
    """一个 OpenAI Chat Completions 兼容提供商的可公开配置。"""

    provider_id: str
    display_name: str
    kind: str
    base_url: str
    endpoint_path: str
    model: str
    structured_output: str = "json_object"
    requires_api_key: bool = True
    models_path: str = "/models"
    advanced_model_enabled: bool = False
    supports_images: bool = False
    models: tuple[ModelConfig, ...] = ()
    transport: str = "openai_chat"
    usage_policy: str = "chat"
    preset_id: str = ""
    output_parameter: str = "max_tokens"
    send_sampling: bool = True

    @property
    def configured_models(self) -> tuple[ModelConfig, ...]:
        return self.models or (ModelConfig(self.model, advanced_model_enabled=self.advanced_model_enabled,
                                         supports_images=self.supports_images),)

    @property
    def active_model(self) -> ModelConfig:
        for item in self.configured_models:
            if item.model_id == self.model:
                return item
        raise ValueError("当前模型不在该提供商的模型列表中")

    def for_model(self, model_id: str) -> "ProviderConfig":
        for item in self.configured_models:
            if item.model_id == model_id:
                return replace(self, model=model_id, advanced_model_enabled=item.advanced_model_enabled,
                               supports_images=item.supports_images)
        raise KeyError(f"未配置模型：{model_id}")

    @property
    def chat_block_reason(self) -> str:
        parsed = urlparse(self.base_url)
        host, path = (parsed.hostname or "").lower(), parsed.path.lower()
        coding_endpoint = (host in {"api.kimi.com", "api.kimi.ai"} and path.startswith("/coding")) or (
            host in {"open.bigmodel.cn", "api.z.ai"} and path.startswith("/api/coding")) or (
            host in {"coding.dashscope.aliyuncs.com", "coding-intl.dashscope.aliyuncs.com",
                     "token-plan-cn.xiaomimimo.com", "token-plan-sgp.xiaomimimo.com",
                     "token-plan-ams.xiaomimimo.com"})
        if self.usage_policy == "coding_only" or coding_endpoint:
            return "这是编程套餐专用接口，不能用于 RemChat 普通聊天；请使用通用 API 的地址与密钥。"
        if self.transport != "openai_chat":
            return "当前仅支持 OpenAI Chat Completions；此接口协议尚未接入。"
        return ""

    def ensure_chat_supported(self) -> None:
        self.validate()
        if self.chat_block_reason:
            raise ValueError(self.chat_block_reason)

    @property
    def endpoint_url(self) -> str:
        return self.base_url.rstrip("/") + "/" + self.endpoint_path.lstrip("/")

    @property
    def is_local(self) -> bool:
        return self.kind == "local"

    def validate(self) -> "ProviderConfig":
        for value in (self.provider_id, self.display_name, self.kind, self.base_url,
                      self.endpoint_path, self.model, self.structured_output, self.models_path,
                      self.transport, self.usage_policy, self.preset_id, self.output_parameter):
            if not isinstance(value, str):
                raise ValueError("模型配置文本字段必须是字符串")
        if not PROVIDER_ID_PATTERN.fullmatch(self.provider_id):
            raise ValueError("provider ID 只能包含小写字母、数字、下划线或连字符")
        if self.kind not in {"local", "remote"}:
            raise ValueError("模型提供商 kind 必须是 local 或 remote")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("模型 Base URL 必须是有效的 HTTP/HTTPS 地址")
        local_hosts = {"127.0.0.1", "localhost", "::1"}
        if (
            self.kind == "remote"
            and self.requires_api_key
            and parsed.scheme != "https"
            and parsed.hostname not in local_hosts
        ):
            raise ValueError("携带 API Key 的远程接口必须使用 HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Base URL 不能包含账户、密钥、查询参数或片段")
        if any(char.isspace() for char in self.base_url):
            raise ValueError("Base URL 不能包含空白字符")
        try:
            parsed.port
        except ValueError as error:
            raise ValueError("Base URL 端口无效") from error
        for path in (self.endpoint_path, self.models_path):
            if not path.startswith("/") or path.startswith("//") or any(
                char.isspace() or char in "?#\\" for char in path
            ):
                raise ValueError("接口路径必须以单个 / 开头，不能包含查询参数或空白")
        if not self.display_name.strip() or not self.model.strip():
            raise ValueError("模型提供商名称和模型名称不能为空")
        if self.structured_output not in {"json_schema", "json_object", "prompt"}:
            raise ValueError("结构化输出模式不受支持")
        if self.transport not in {"openai_chat", "anthropic", "openai_responses", "gemini"}:
            raise ValueError("接口协议不受支持")
        if self.usage_policy not in {"chat", "coding_only"}:
            raise ValueError("接口使用范围不受支持")
        if self.output_parameter not in {"max_tokens", "max_completion_tokens"}:
            raise ValueError("输出限制参数不受支持")
        if any(type(flag) is not bool for flag in (self.requires_api_key, self.advanced_model_enabled, self.supports_images, self.send_sampling)):
            raise ValueError("模型能力与认证选项必须是布尔值")
        if not isinstance(self.models, tuple) or any(not isinstance(item, ModelConfig) for item in self.models):
            raise ValueError("模型列表格式无效")
        for item in self.configured_models:
            item.validate()
            if self.is_local and (item.advanced_model_enabled or item.supports_images):
                raise ValueError("内置本地模型不启用高级接入或图像输入")
        ids = [item.model_id for item in self.configured_models]
        if len(ids) != len(set(ids)):
            raise ValueError("同一提供商的模型 ID 不能重复")
        self.active_model
        if self.is_local and self.advanced_model_enabled:
            raise ValueError("内置本地小模型仅用于聊天与监工，不启用高级接入助手")
        if self.is_local and self.supports_images:
            raise ValueError("内置本地模型未配置图像投影，不支持图像输入")
        return self


@dataclass(frozen=True)
class ProviderState:
    """全部提供商与当前启用项的不可变快照。"""

    active_provider_id: str
    providers: tuple[ProviderConfig, ...]

    @property
    def provider_ids(self) -> tuple[str, ...]:
        return tuple(item.provider_id for item in self.providers)

    @property
    def active_provider(self) -> ProviderConfig:
        provider = self.get(self.active_provider_id)
        return provider.for_model(provider.model)

    def get(self, provider_id: str) -> ProviderConfig:
        for item in self.providers:
            if item.provider_id == provider_id:
                return item
        raise KeyError(f"未知模型提供商: {provider_id}")

    def activate(self, provider_id: str, model_id: str | None = None) -> "ProviderState":
        provider = self.get(provider_id)
        provider = provider.for_model(model_id or provider.model)
        provider.ensure_chat_supported()
        return replace(self.replace_provider(provider), active_provider_id=provider_id)

    def replace_provider(self, provider: ProviderConfig) -> "ProviderState":
        provider.validate()
        replaced = False
        output: list[ProviderConfig] = []
        for current in self.providers:
            if current.provider_id == provider.provider_id:
                output.append(provider)
                replaced = True
            else:
                output.append(current)
        if not replaced:
            output.append(provider)
        return replace(self, providers=tuple(output))

    def remove_provider(self, provider_id: str) -> "ProviderState":
        """Deleting the active API returns to local; never remove the local anchor."""
        if provider_id == LOCAL_PROVIDER_ID:
            raise ValueError("不能删除内置本地模型")
        self.get(provider_id)
        active = LOCAL_PROVIDER_ID if self.active_provider_id == provider_id else self.active_provider_id
        return replace(self, active_provider_id=active,
                       providers=tuple(item for item in self.providers if item.provider_id != provider_id)).validate()

    def validate(self) -> "ProviderState":
        if not self.providers:
            raise ValueError("至少需要一个模型提供商")
        ids = self.provider_ids
        if len(ids) != len(set(ids)):
            raise ValueError("模型提供商 ID 不能重复")
        for provider in self.providers:
            provider.validate()
        if self.active_provider_id not in ids:
            raise ValueError("当前启用的模型提供商不存在")
        return self


def default_provider_state(
    *,
    local_base_url: str = "http://127.0.0.1:8080",
    local_model: str = "rem-local",
    endpoint_path: str = "/v1/chat/completions",
) -> ProviderState:
    """返回保守默认值：继续使用已经工作的本地 Qwen。"""

    return ProviderState(
        active_provider_id=LOCAL_PROVIDER_ID,
        providers=(
            ProviderConfig(
                provider_id=LOCAL_PROVIDER_ID,
                display_name="本地 Qwen",
                kind="local",
                base_url=local_base_url,
                endpoint_path=endpoint_path,
                model=local_model,
                structured_output="json_schema",
                requires_api_key=False,
                models_path="/v1/models",
            ),
            ProviderConfig(
                provider_id="openai",
                display_name="OpenAI",
                kind="remote",
                base_url="https://api.openai.com/v1",
                endpoint_path="/chat/completions",
                model="gpt-5-mini",
                structured_output="json_schema",
            ),
            ProviderConfig(
                provider_id="deepseek",
                display_name="DeepSeek",
                kind="remote",
                base_url="https://api.deepseek.com",
                endpoint_path="/chat/completions",
                model="deepseek-v4-flash",
                structured_output="json_object",
                models_path="/models",
            ),
            ProviderConfig(
                provider_id="qwen",
                display_name="Qwen API",
                kind="remote",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                endpoint_path="/chat/completions",
                model="qwen3.7-plus",
                structured_output="json_object",
            ),
            ProviderConfig(
                provider_id="custom",
                display_name="自定义兼容接口",
                kind="remote",
                base_url="https://example.com/v1",
                endpoint_path="/chat/completions",
                model="your-model",
                structured_output="json_object",
            ),
        ),
    )


class ModelProviderRepository:
    """以原子写入方式保存模型提供商配置。"""

    def __init__(
        self,
        root: Path,
        *,
        local_base_url: str = "http://127.0.0.1:8080",
        local_model: str = "rem-local",
        endpoint_path: str = "/v1/chat/completions",
    ) -> None:
        self.path = root / PROVIDER_CONFIG_FILE
        self._defaults = default_provider_state(
            local_base_url=local_base_url,
            local_model=local_model,
            endpoint_path=endpoint_path,
        )

    def load(self) -> ProviderState:
        if not self.path.is_file():
            return self._defaults
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("version", 1) not in {1, 2}:
                raise ValueError("提供商配置版本不受支持")
            providers = tuple(
                _provider_from_mapping(item) for item in raw["providers"]
            )
            state = ProviderState(
                active_provider_id=str(raw["active_provider_id"]),
                providers=providers,
            )
            return state.validate()
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            # 配置损坏不应让桌面宠物无法启动；安全回退到原有本地链路。
            return self._defaults

    def save(self, state: ProviderState) -> Path:
        state.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {
            "version": 2,
            "active_provider_id": state.active_provider_id,
            "providers": [asdict(item) for item in state.providers],
        }
        serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if self.path.is_file() and self.path.read_text(encoding="utf-8") != serialized:
            backup = self.path.parent / "backups" / "model-providers"
            backup.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            (backup / f"{stamp}.json").write_bytes(self.path.read_bytes())
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(self.path)
        return self.path


def new_provider_id() -> str:
    return "custom-" + uuid.uuid4().hex[:12]


def export_provider_config(provider: ProviderConfig) -> str:
    """Share only validated public settings; recipient must confirm model quality anew."""
    safe = replace(provider.validate(), advanced_model_enabled=False, supports_images=False,
                   models=tuple(replace(item, advanced_model_enabled=False, supports_images=False)
                                for item in provider.configured_models))
    return json.dumps({"format": "remchat-provider", "version": 2,
                       "provider": asdict(safe)}, ensure_ascii=False, indent=2) + "\n"


def import_provider_config(text: str) -> ProviderConfig:
    """Import our explicit format, never CLI credentials or an active-provider instruction."""
    if len(text.encode("utf-8")) > 64 * 1024:
        raise ValueError("提供商配置不能超过 64 KB")
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("format") != "remchat-provider" or raw.get("version") not in {1, 2}:
        raise ValueError("仅支持 RemChat 提供商配置格式；不会读取其他 CLI 的密钥")
    item = raw.get("provider")
    if not isinstance(item, dict):
        raise ValueError("配置缺少 provider 对象")
    allowed = set(ProviderConfig.__dataclass_fields__)
    if set(item) - allowed:
        raise ValueError("配置包含未知字段或敏感字段；请使用不含密钥的 RemChat 导出")
    try:
        source = _provider_from_mapping(item).validate()
    except TypeError as error:
        raise ValueError("提供商配置字段缺失或类型错误") from error
    if source.is_local:
        raise ValueError("内置本地模型使用本机 TOML 配置，不支持从文件覆盖")
    return replace(source, provider_id=new_provider_id(), advanced_model_enabled=False, supports_images=False,
                   models=tuple(replace(model, advanced_model_enabled=False, supports_images=False)
                                for model in source.configured_models))


def _provider_from_mapping(item: dict) -> ProviderConfig:
    """Read v1/v2 public schemas, rejecting nested credential/unknown fields too."""
    if not isinstance(item, dict) or set(item) - set(ProviderConfig.__dataclass_fields__):
        raise ValueError("提供商配置包含未知或敏感字段")
    values = dict(item)
    models = values.get("models", ())
    if not isinstance(models, (list, tuple)):
        raise ValueError("模型列表必须是数组")
    parsed = []
    for model in models:
        if not isinstance(model, dict) or set(model) - set(ModelConfig.__dataclass_fields__):
            raise ValueError("模型配置包含未知或敏感字段")
        parsed.append(ModelConfig(**model).validate())
    values["models"] = tuple(parsed)
    return ProviderConfig(**values).validate()
