# Hermes Agent

官方 gateway 提供 agent:start / agent:end（单条消息处理开始/结束），与 CLI plugin hooks 不同。导出前填写精确 session_id。检查 `handler.py` 和 `HOOK.yaml` 后手动放到对应 Hermes profile 的 `hooks/remchat-monitor/`，重启 gateway。放置文件就是宿主授权行为，导出不会替你放进去。

必须把同一导出包的 `runtime/` 目录与 handler.py、HOOK.yaml 一起复制。handler 使用同目录的通知脚本，不依赖 RemChat 源码树；通知根目录仍指向导出时选定的 RemChat，移机后重新导出。

没有 session_id 时不发送通知，其他会话被忽略；通知不传用户消息或模型原文，失败也不阻止宿主。停用：移除/改名该 Hook 目录。此 handler 只对 gateway 生效，不宣称能观察交互式 CLI 每轮任务。

Hermes CLI 可使用实际非交互任务 wrapper，或让产物流程写 marker。pre/post_llm_call 与 session_end 不能证明任务结束。Windows/Linux 通知层相同；宿主部署需遵从它自己的系统支持。验证级别：generated handler 的真实 hook 入参/本机事件已验证；未在 Hermes gateway 登录会话验证。依据：[官方 hooks](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/hooks.md)、[官方 plugin 实现](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/plugins.py)。
