"""Codex exec --json 的有界 JSONL 观察桥，不需要访问付费 API 验证解析器。"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from remcompanion.platforms import native_module
from remcompanion.integrations.notify import send_event
from remcompanion.integrations.processes import ProcessScope

program_argv = native_module("shell").program_argv

MAX_LINE = 4 * 1024 * 1024


class CodexTurnObserver:
    """只接受真正 turn 事件，item、Stop检查与中间 error 都不假装最终成功。"""

    def __init__(self, on_start: Callable[[], None]) -> None:
        self.on_start = on_start
        self.outcome: str | None = None
        self.saw_turn = False

    def consume(self, value: dict[str, Any]) -> None:
        kind = value.get("type", value.get("method"))
        if kind in {"turn.started", "turn/started"}:
            self.saw_turn = True
            self.outcome = None
            self.on_start()
        elif kind == "turn.completed":
            self.outcome = "completed"
        elif kind == "turn.failed":
            self.outcome = "failed"
        elif kind == "turn/completed":
            params = value.get("params", {})
            turn = params.get("turn", {}) if isinstance(params, dict) else {}
            state = turn.get("status") if isinstance(turn, dict) else None
            self.outcome = {"completed": "completed", "failed": "failed", "interrupted": "cancelled"}.get(state)

    def final_state(self, returncode: int) -> tuple[str, str]:
        if returncode != 0:
            return "failed", f"Codex 批处理非正常退出，exit code={returncode}。"
        if self.outcome == "completed":
            return "completed", "Codex 报告 turn.completed 且命令正常退出；本轮产物待验收。"
        if self.outcome in {"failed", "cancelled"}:
            return self.outcome, "Codex 本轮报告" + ("失败。" if self.outcome == "failed" else "被中断。")
        return "cancelled", "命令已退出但未收到可确认的最终 turn 事件；监工停止，不判定成功。"


def main() -> int:
    parser = argparse.ArgumentParser(description="观察用户明确启动的 Codex exec --json 批处理。")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--batch", action="store_true")
    parser.add_argument("--cwd", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not args.batch or not command or "--json" not in command or "exec" not in command:
        parser.error("需要 --batch，且 -- 后必须是明确的 codex exec --json 命令 argv。")

    def notify(state: str, message: str) -> None:
        try:
            send_event(args.root, args.task, state, message)
        except Exception as error:
            print(f"RemChat 通知未送达（不影响 Codex 退出码）：{error}", file=sys.stderr)

    observer = CodexTurnObserver(lambda: notify("started", "Codex 报告 turn.started。"))
    scope = ProcessScope()
    try:
        process = scope.launch(program_argv(command[0], command[1:]),
                               cwd=str(args.cwd) if args.cwd else None, env=dict(os.environ),
                               stdin=sys.stdin, stderr=sys.stderr, stdout=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace")
        assert process.stdout
        while True:
            line = process.stdout.readline(MAX_LINE + 1)
            if not line:
                break
            if len(line) > MAX_LINE or len(line.encode("utf-8")) > MAX_LINE:
                raise ValueError("Codex JSONL 单行超过 4 MB，停止桥接并清理该命令进程。")
            sys.stdout.write(line)
            sys.stdout.flush()
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    observer.consume(value)
            except json.JSONDecodeError:
                pass
        code = process.wait()
        notify(*observer.final_state(code))
        return code
    except KeyboardInterrupt:
        scope.close(graceful=False)
        notify("cancelled", "用户中断了 Codex 批处理。")
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        notify("failed", "Codex 观察桥失败：" + str(error)[:1000])
        print(str(error), file=sys.stderr)
        return 1
    finally:
        scope.close(graceful=False)


if __name__ == "__main__":
    raise SystemExit(main())
