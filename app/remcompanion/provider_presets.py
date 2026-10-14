"""Opt-in public API presets; no credentials, implicit connections, or access bypass."""

from dataclasses import dataclass

from .model_providers import ModelConfig, ProviderConfig, new_provider_id


@dataclass(frozen=True)
class ProviderPreset:
    preset_id: str
    name: str
    base_url: str
    models: tuple[ModelConfig, ...]
    docs_url: str
    note: str = ""
    structured_output: str = "json_object"
    transport: str = "openai_chat"
    usage_policy: str = "chat"
    output_parameter: str = "max_tokens"
    send_sampling: bool = False
    endpoint_path: str = "/chat/completions"
    models_path: str = "/models"

    def create(self) -> ProviderConfig:
        return ProviderConfig(
            provider_id=new_provider_id(), display_name=self.name, kind="remote",
            base_url=self.base_url, endpoint_path=self.endpoint_path, model=self.models[0].model_id,
            structured_output=self.structured_output, models=self.models, transport=self.transport,
            usage_policy=self.usage_policy, preset_id=self.preset_id,
            output_parameter=self.output_parameter, send_sampling=self.send_sampling,
            models_path=self.models_path,
        ).validate()


# Numeric defaults appear only where official text specifies exact integers.
# An absent limit is not unlimited; the platform's actual limits still apply.
_PRESET_REGISTRY = (
    ProviderPreset("custom", "自定义兼容接口", "https://example.com/v1", (ModelConfig("your-model"),), "",
                   send_sampling=True),
    ProviderPreset("openai", "OpenAI", "https://api.openai.com/v1", (ModelConfig("gpt-5-mini"),),
                   "https://platform.openai.com/docs/api-reference/chat", structured_output="json_schema",
                   output_parameter="max_completion_tokens"),
    ProviderPreset("anthropic", "Anthropic · Claude", "https://api.anthropic.com/v1",
                   (ModelConfig("your-model"),), "https://docs.anthropic.com/en/api/messages",
                   "Anthropic 协议尚未接入。", structured_output="prompt", transport="anthropic",
                   endpoint_path="/messages"),
    ProviderPreset("gemini", "Google · Gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
                   (ModelConfig("your-model"),), "https://ai.google.dev/gemini-api/docs/openai",
                   "使用 Gemini API 密钥与兼容接口；模型 ID 请按账号填写。", structured_output="prompt"),
    ProviderPreset("kimi", "Kimi", "https://api.moonshot.cn/v1", (ModelConfig("kimi-k3", "Kimi K3"),),
                   "https://platform.kimi.com/docs/get-api-key"),
    ProviderPreset("kimi-coding", "Kimi For Coding", "https://api.kimi.com/coding/v1",
                   (ModelConfig("kimi-for-coding"),), "https://www.kimi.com/code/docs/kimi-code/faq.html",
                   "编程套餐专用；当前 RemChat 不接入。", transport="anthropic", usage_policy="coding_only"),
    ProviderPreset("qwen", "千问 / Qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1",
                   (ModelConfig("qwen-plus", "Qwen Plus"), ModelConfig("qwen-turbo", "Qwen Turbo")),
                   "https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope",
                   "密钥须与地域、工作空间及模型权限一致。"),
    ProviderPreset("deepseek", "DeepSeek", "https://api.deepseek.com",
                   (ModelConfig("deepseek-flash", "DeepSeek Flash", 1048576, 393216),
                    ModelConfig("deepseek-v4-pro", "DeepSeek V4 Pro", 1048576, 393216)),
                   "https://api-docs.deepseek.com/api/list-models/"),
    ProviderPreset("minimax", "MiniMax", "https://api.minimax.cn/v1",
                   (ModelConfig("MiniMax-M2.7", context_tokens=204800), ModelConfig("MiniMax-M3", context_tokens=1000000)),
                   "https://platform.minimax.cn/docs/api-reference/text-openai-api",
                   "通用 API；不使用编程套餐专属模型。", structured_output="prompt",
                   output_parameter="max_completion_tokens"),
    ProviderPreset("minimax-global", "MiniMax International", "https://api.minimax.io/v1",
                   (ModelConfig("MiniMax-M2.7", context_tokens=204800),),
                   "https://platform.minimax.io/docs/api-reference/text-openai-api", structured_output="prompt",
                   output_parameter="max_completion_tokens"),
    ProviderPreset("modelscope", "ModelScope 魔搭", "https://api-inference.modelscope.cn/v1",
                   (ModelConfig("Qwen/Qwen3.5-27B"),), "https://modelscope.cn/models/Qwen/Qwen3.5-27B",
                   "模型可用性和额度取决于账号；可手动填写平台完整模型 ID。", structured_output="prompt"),
    ProviderPreset("mimo", "Xiaomi MiMo", "https://api.xiaomimimo.com/v1",
                   (ModelConfig("mimo-v2.6-pro", "MiMo V2.6 Pro"), ModelConfig("mimo-v2.6-flash", "MiMo V2.6 Flash")),
                   "https://mimo.mi.com/docs/en-US/quick-start/usage-guide/text-generation/structured-output",
                   output_parameter="max_completion_tokens"),
    ProviderPreset("mimo-token-plan", "Xiaomi MiMo · Token Plan", "https://token-plan-cn.xiaomimimo.com/v1",
                   (ModelConfig("mimo-v2.6-pro"), ModelConfig("mimo-v2.6-flash")),
                   "https://mimo.mi.com/docs/en-US/tokenplan/Token%20Plan/subscription",
                   "编程套餐专用；当前 RemChat 不接入。", usage_policy="coding_only"),
    ProviderPreset("zhipu", "Zhipu GLM", "https://open.bigmodel.cn/api/paas/v4",
                   (ModelConfig("your-model"),), "https://docs.bigmodel.cn/cn/best-practice/case/ai-search-engine",
                   "使用通用 API 密钥；模型 ID 请按账号权限填写，不使用 Coding Plan 地址。", structured_output="prompt"),
    ProviderPreset("siliconflow", "SiliconFlow 硅基流动", "https://api.siliconflow.cn/v1",
                   (ModelConfig("deepseek-ai/DeepSeek-V4-Flash"),),
                   "https://docs.siliconflow.cn/docs/api/chat-completions-post"),
    ProviderPreset("kimi-global", "Kimi Global", "https://api.moonshot.ai/v1",
                   (ModelConfig("kimi-k3", "Kimi K3"),),
                   "https://www.kimi.ai/help/kimi-api/api-troubleshooting",
                   "国际平台密钥与国内平台分开。"),
    ProviderPreset("qwen-global", "QwenCloud · 国际", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                   (ModelConfig("qwen-plus"), ModelConfig("qwen-turbo")),
                   "https://help.aliyun.com/zh/model-studio/singapore-regional-access-information",
                   "新加坡通用 API；密钥须匹配地域和工作空间。"),
    ProviderPreset("qwen-coding", "QwenCloud · Coding", "https://coding.dashscope.aliyuncs.com/v1",
                   (ModelConfig("qwen3-coder-plus"),), "https://help.aliyun.com/en/model-studio/coding-plan",
                   "编程套餐专用；当前 RemChat 不接入。", usage_policy="coding_only"),
    ProviderPreset("qwen-coding-global", "QwenCloud · Coding 国际", "https://coding-intl.dashscope.aliyuncs.com/v1",
                   (ModelConfig("qwen3-coder-plus"),),
                   "https://docs.modelstudio.console.alibabacloud.com/en/model-studio/coding-plan-faq",
                   "编程套餐专用；当前 RemChat 不接入。", usage_policy="coding_only"),
    ProviderPreset("zhipu-global", "Zhipu GLM · 国际", "https://api.z.ai/api/paas/v4",
                   (ModelConfig("your-model"),), "https://docs.z.ai/guides/develop/openai/python",
                   "通用 API；按账号权限填写模型 ID。", structured_output="prompt"),
    ProviderPreset("zhipu-coding", "Zhipu GLM · Coding", "https://open.bigmodel.cn/api/coding/paas/v4",
                   (ModelConfig("your-model"),), "https://docs.bigmodel.cn/cn/coding-plan/overview",
                   "编程套餐专用；当前 RemChat 不接入。", usage_policy="coding_only"),
    ProviderPreset("zhipu-coding-global", "Zhipu GLM · Coding 国际", "https://api.z.ai/api/coding/paas/v4",
                   (ModelConfig("your-model"),), "https://zcode.z.ai/en/docs/configuration",
                   "编程套餐专用；当前 RemChat 不接入。", usage_policy="coding_only"),
    ProviderPreset("siliconflow-global", "SiliconFlow · 国际", "https://api.siliconflow.com/v1",
                   (ModelConfig("your-model"),), "https://docs.siliconflow.com",
                   "完整模型 ID 请按国际平台模型库填写。", structured_output="prompt"),
    ProviderPreset("stepfun", "StepFun 阶跃星辰", "https://api.stepfun.com/v1",
                   (ModelConfig("your-model"),), "https://platform.stepfun.com/docs",
                   "模型 ID 请按平台权限填写。", structured_output="prompt"),
    ProviderPreset("stepfun-global", "StepFun · 国际", "https://api.stepfun.ai/v1",
                   (ModelConfig("your-model"),), "https://platform.stepfun.ai",
                   "国际平台模型 ID 与密钥需匹配。", structured_output="prompt"),
    ProviderPreset("doubao", "火山引擎 · 豆包", "https://ark.cn-beijing.volces.com/api/v3",
                   (ModelConfig("your-model"),), "https://docs.volcengine.com/docs/ark/context-management?lang=zh",
                   "填写账号可用的 Model ID 或推理接入点 ID。", structured_output="prompt",
                   output_parameter="max_completion_tokens"),
    ProviderPreset("nvidia", "NVIDIA NIM", "https://integrate.api.nvidia.com/v1",
                   (ModelConfig("openai/gpt-oss-20b"),), "https://docs.api.nvidia.com/nim/reference/openai-gpt-oss-20b-infer",
                   structured_output="prompt"),
    ProviderPreset("openrouter", "OpenRouter", "https://openrouter.ai/api/v1",
                   (ModelConfig("your-model"),), "https://openrouter.ai/docs/quickstart",
                   "填写完整平台模型 ID；价格与权限按所选模型决定。", structured_output="prompt"),
    ProviderPreset("mistral", "Mistral AI", "https://api.mistral.ai/v1",
                   (ModelConfig("mistral-small-latest"),), "https://docs.mistral.ai/api/",
                   structured_output="prompt"),
    ProviderPreset("groq", "Groq", "https://api.groq.com/openai/v1",
                   (ModelConfig("your-model"),), "https://console.groq.com/docs/openai",
                   "模型 ID 请按平台模型列表填写。", structured_output="prompt"),
    ProviderPreset("xai", "xAI · Grok", "https://api.x.ai/v1",
                   (ModelConfig("your-model"),), "https://x.ai/api",
                   "模型 ID 请按平台权限填写。", structured_output="prompt"),
)

# The user's boxed families, not an exhaustive directory. Keep the complete
# registry available for already-saved profiles, but never create or erase a
# profile while filtering this menu.
_VISIBLE_PRESETS = (
    "custom", "kimi", "kimi-coding", "qwen", "qwen-coding", "deepseek",
    "minimax", "minimax-global", "modelscope", "qwen-global",
    "qwen-coding-global", "mimo", "mimo-token-plan", "zhipu", "zhipu-global", "siliconflow",
)
PROVIDER_PRESETS = tuple(next(p for p in _PRESET_REGISTRY if p.preset_id == key)
                         for key in _VISIBLE_PRESETS)


def preset_for_id(preset_id: str) -> ProviderPreset | None:
    return next((item for item in _PRESET_REGISTRY if item.preset_id == preset_id), None)
