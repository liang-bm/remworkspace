# DeepSeek Harness

已查真正官方仓库 deepseek-ai/deepseek-harness，而不是以模型 API 冒充 Harness。当前接入基线是明确非交互 CLI 的 batch wrapper，或由任务产物生成的 marker；填写 task-config 的实际入口和参数，不自动安装 npm 插件。

官方 Agent API 可观察模型尝试，但 `agent/turn-stopping` 发生在结束前且可被插件延长，`agent/request-error` 可被重试，stream end 也未必是整个任务结束。为避免提前语音报喜，本版没有把这些事件直接映射 completed/failed。常驻 dsh GUI/TUI 不能交给批处理退出监工。

Windows/Linux wrapper 是通用可用路径；宿主插件与版本维护留在本独立目录。验证级别：批处理真实退出/通知已验证，未运行 DeepSeek Harness 模型。依据：[官方源码](https://github.com/deepseek-ai/deepseek-harness)、[Agent API说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent/README.md)。
