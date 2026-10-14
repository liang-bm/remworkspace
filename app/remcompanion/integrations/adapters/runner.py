"""显式批处理命令监工，Windows / Linux 标准库实现。

只用于会退出的非交互任务，不用 GUI/TUI 进程退出推断模型任务完成。
保留原始输出和退出码；通知失败不会吞掉宿主命令的结果。
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from remcompanion.platforms import native_module
from remcompanion.integrations.notify import send_event
from remcompanion.integrations.processes import ProcessScope

program_argv = native_module("shell").program_argv


def main() -> int:
    parser = argparse.ArgumentParser(description="围绕明确的非交互命令发送真实任务状态。")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--batch", action="store_true", help="确认此命令是单次批处理任务，不是 GUI 或常驻 TUI")
    parser.add_argument("--cwd", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not args.batch or not command:
        parser.error("需要 --batch 并在 -- 后填写明确的非交互命令 argv。")

    def notify(state: str, message: str) -> None:
        try:
            send_event(args.root, args.task, state, message)
        except Exception as error:
            print(f"RemChat 通知未送达（不影响任务退出码）：{error}", file=sys.stderr)

    scope = ProcessScope()
    try:
        # 这是用户主动启动自己的 CLI，不是自动启用未知工具。
        # 保留 CLI 自己的登录环境；argv 不交给 shell 拼接解释。
        process = scope.launch(program_argv(command[0], command[1:]),
                               cwd=str(args.cwd) if args.cwd else None, env=dict(os.environ),
                               stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
        notify("started", "批处理命令已启动。")
        code = process.wait()
        notify("completed" if code == 0 else "failed", f"批处理命令已退出，exit code={code}；产物仍需你验收。")
        return code
    except KeyboardInterrupt:
        scope.close(graceful=False)
        notify("cancelled", "用户中断了批处理命令。")
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        notify("failed", "命令未能启动：" + str(error)[:1000])
        print(str(error), file=sys.stderr)
        return 1
    finally:
        scope.close(graceful=False)


if __name__ == "__main__":
    raise SystemExit(main())
