# Claude Code

先注册任务并启动 RemChat 接收器。导出包自带 `runtime/`，只需要系统 Python 3.8+（Windows 本地模型环境通常已经提供），不依赖 RemChat 的源码树。GUI 自带运行时不是可供其他 CLI 调用的通用 Python。保留导出目录；移机/移动目录后重新导出。

默认 `hooks.merge.json` 适配旧版：只有 UserPromptSubmit / Stop，使用一个 command 字符串。Windows 以 UTF-16LE Base64 编码的 PowerShell literal argv 避免 cmd/Git Bash 对中文、空格、`$`、`%` 和引号的不同解析；编码不是加密，可解码检查，内容仅为 Python 通知脚本与固定参数。Linux 使用逐参数 POSIX 引号。检查后手动合并到目标项目 `.claude/settings.local.json`，保留原有 Hook。不自动安装、不改全局设置。

`hooks.exec.merge.json` 是支持新版 exec-form 的宿主专用草稿：command + args 数组，不经过 shell，并增加 StopFailure。不要把它给 2.1.34：该版本实际 schema 不支持 args / StopFailure。不要同时合并两个文件，以免重复通知。

`UserPromptSubmit` 标记开始；`Stop` 仅记录结束检查中（保持 started）；`StopFailure` 通知接口错误。输出不含 Hook 决策，不阻塞或影响 Claude。Stop 输入报告后台任务或 session cron 时不发送状态；其他 Stop Hook 仍可能继续宿主，因此不能据此标 completed。

停用方法：移除/禁用你刚合并的 Hook。需要真正的单次批处理结束，可检查 `task-config.json` 的 `batch_wrapper_argv`，填写宿主的非交互命令。

验证级别：Windows 实际 Claude Code 2.1.34 + 隔离配置 + 本机 Messages 协议桩，真实 UserPromptSubmit / Stop → 本机接收器已通过，特殊路径完整保留。Linux Python3.8/3.12 的自带脚本与本机通知另测，不等于实际 Linux Claude 已验证。新版 exec form、StopFailure 与后台抑制以真实 JSON stdin 验证，未运行新版宿主或付费模型。依据：[官方 Hooks](https://code.claude.com/docs/en/hooks)；旧版兼容同时以本机已安装官方代码和实际运行核对。
