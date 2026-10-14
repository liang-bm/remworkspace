# OpenCode

在导出前填写目标 `sessionID`。阅读 `remchat-monitor.mjs` 后把 `ENABLED` 改成 `true`，确认 `SESSION_ID`。复制到目标项目 `.opencode/plugins/remchat-monitor.mjs`（或检查后选择全局插件目录），重新启动 OpenCode。导出本身不会安装插件。

插件只观察指定会话：`session.status` 的 busy 开始、`session.error` 失败、随后 `session.idle` 本轮结束。初始空闲不会误提醒；失败后空闲不会假称成功；其他会话/子任务 ID 被忽略。没有会话 ID 时完全不发送事件。任务重开由下一次 busy 表示。接收器地址与 token 每次读取，重启后动态端口仍有效。

停止：将 ENABLED 设 false 或移除插件。Windows/Linux 源码相同，项目路径移机后重新导出。没有非交互模式的 TUI 不应交给 batch wrapper。

验证级别：JS 模拟真实事件形状并通过真实 loopback 接收器验证；未在 OpenCode 宿主跑模型。依据：[官方插件文档](https://opencode.ai/docs/plugins/)、[官方 SDK 事件类型](https://github.com/anomalyco/opencode/blob/dev/packages/sdk/js/src/v2/gen/types.gen.ts)。
