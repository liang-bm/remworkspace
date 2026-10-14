# WorkBuddy

官方 WorkBuddy 存在跨设备任务状态功能，但公开文档中的 CodeBuddy Code Hook 不能直接视为 WorkBuddy 桌面版接口兼容保证。此目录暂标实验 marker/helper，不自动改 `.codebuddy` 或猜测 WorkBuddy 私有全局设置。

可用办法：选一个具体产物任务，使用 RemChat 的 marker 文件监工，让你控制的工作流程完成时写 marker，或执行 task-config 的 notify argv。仅当确实有非交互 CLI 时才使用 batch wrapper，不包装 WorkBuddy GUI。

取消监工不取消宿主任务。Windows/Linux 支持通知脚本；官方桌面宿主的 Linux 支持不作保证。依据：[WorkBuddy 官方任务状态](https://www.workbuddy.ai/document/cross-device-tasks)、[官方 CLI 版本记录](https://www.workbuddy.ai/docs/cli/release-notes/v2.152.0)。
