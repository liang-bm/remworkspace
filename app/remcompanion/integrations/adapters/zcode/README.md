# ZCode（z.ai / zai-org/ZCode）

官方已核实产品身份和 process Hook 的 command + args 协议。`hooks.merge.json` 默认关闭。检查后手动合并用户配置 `~/.zcode/cli/config.json` 的 hooks，将 root 和具体 Hook 的 enabled 改 true，并开启新会话。当前官方版本会忽略项目级 hooks，不要放到 workspace config 假装已接入。

这里仅导出 UserPromptSubmit 开始通知。ZCode 的 Stop 是准备结束的检查，别的 Hook 可以继续模型，因此不把 Stop 或 PostToolUseFailure 当真正任务完成/失败。需要可靠结束通知：在非交互单次任务外使用 `task-config.json` 的 batch wrapper；若只用桌面会话，让工作流程写一个完成 marker，由 RemChat 观察。

停止：禁用已合并 Hook、停掉接收器或取消监工。Windows/Linux 使用 argv，没有 shell 拼接。验证级别：导出 JSON 结构/真实 started 通知可验证；未在安装 ZCode 的模型会话上验证。依据：[官方 hooks](https://zcode.z.ai/en/docs/hooks)、[官方代码](https://github.com/zai-org/ZCode)。
