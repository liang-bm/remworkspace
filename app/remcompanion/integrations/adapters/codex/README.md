# Codex CLI

独立适配目录，使用单次 `exec --json` wrapper；不改 Codex 配置、不读登录凭据、不启动任意代理。检查 `task-config.json` 的 `codex_jsonl_wrapper_argv`，填写真实入口和任务提示词，主动在终端执行。保留 Codex 默认权限，不自动添加危险/全自动授权开关。

导出目录自带 `runtime/` 的最小标准库代码，只含当前操作系统实现，不需要 app/ 源码目录或桌面 GUI 依赖。外部 Python 需 3.8+；保留导出目录，移动后重新导出。包装用户 CLI 时保留其认证环境，不复制认证文件或替它登录。回环通知失败会写 stderr，但不吞原始输出和退出码。

`turn.started` 发 started；只有 `turn.completed` 且进程退出 0 才发 completed，`turn.failed`/非0退出发 failed。exit 0 但缺最终事件时通知无法确认，不假称成功。中间 item/error 不当最终完成。用户中断发 cancelled。Windows codex.cmd 解析成已知 npm 包 node + codex.js 入口，不拼接 shell；Linux 使用 CLI 入口。

解析器同时识别 App Server 的 turn/completed 并检查 status，interrupted 不是成功，但没有主动启动 App Server 或接管现有会话。Stop Hook 可继续模型，SessionEnd 不是任务完成，因此没有伪造完成 Hook。验证级别：实际 Windows Codex 0.159.2 在隔离配置、只读沙箱、本机 Responses 协议桩下运行 exec --json，真实开始/完成/退出及监工结果通过。失败/中断/缺事件另以受控 JSONL 子进程验证。未向付费模型发请求；Linux 只验证脚本，不宣称实际 Linux Codex 版本兼容。依据：[官方非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)、[App Server协议](https://learn.chatgpt.com/docs/app-server)。
