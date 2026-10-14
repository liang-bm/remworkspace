"""给外部 CLI / 工作脚本使用的轻量通知入口（只有标准库）。

示例：python notify.py --root /path/to/remworkspace --task TASK_ID --state completed。
此脚本只发送完成事件，不执行外部任务；监听地址从 RemChat 当前接收器文件读取。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import urllib.error
from urllib.parse import urlparse
import urllib.request
import uuid


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *arguments: object, **options: object) -> None:
        raise ValueError("本机通知接口不允许重定向。")


def send_event(root: Path | str, task_id: str, state: str, message: str = "") -> dict[str, object]:
    directory = Path(root).resolve() / "data" / "integrations"
    receiver = json.loads((directory / "receiver.json").read_text(encoding="utf-8"))
    if not receiver.get("running"):
        raise ValueError("RemChat 本机事件接收器未运行。请在任务监工页启动它。")
    url = str(receiver.get("url", ""))
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path != "/events":
        raise ValueError("接收器地址不是受支持的本机接口。")
    token = (directory / "receiver-token.txt").read_text(encoding="utf-8").strip()
    payload = {"task_id": task_id, "state": state, "message": message, "event_id": uuid.uuid4().hex}
    request = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json", "Authorization": "Bearer " + token}, method="POST")
    # This is local IPC. Never forward the bearer token through HTTP_PROXY or
    # system proxy settings inherited from the user's model/CLI environment.
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=5) as response:
        return json.loads(response.read(65536))


def main() -> int:
    parser = argparse.ArgumentParser(description="向 RemChat 发送已注册任务的真实状态事件。")
    parser.add_argument("--root", type=Path, required=True, help="RemChat 项目根目录")
    parser.add_argument("--task", required=True, help="任务监工页显示的任务 ID")
    parser.add_argument("--state", choices=["started", "completed", "failed", "cancelled"])
    parser.add_argument("--message", default="")
    parser.add_argument("--claude-hook", action="store_true", help="读取 Claude Code 原生 Hook JSON，并发送本轮状态；不控制或阻塞 Claude")
    arguments = parser.parse_args()
    try:
        state = arguments.state
        message = arguments.message
        if arguments.claude_hook:
            payload = json.loads(sys.stdin.read(65537))
            if not isinstance(payload, dict):
                raise ValueError("Claude Hook 输入不是 JSON 对象。")
            kind = payload.get("hook_event_name")
            if kind == "UserPromptSubmit":
                state, message = "started", "Claude 正在处理你刚提交的提示。"
            elif kind == "Stop":
                if payload.get("background_tasks") or payload.get("session_crons"):
                    return 0
                # 其他 Stop Hook 可能阻止结束或让模型继续，不能在此提前宣告完成。
                state, message = "started", "Claude 进入本轮结束检查；是否真正完成需由批处理退出或产物标记确认。"
            elif kind == "StopFailure":
                state, message = "failed", "Claude 本轮因接口错误结束：" + str(payload.get("error", "unknown"))[:200]
            else:
                return 0
        if not state:
            raise ValueError("填写 --state，或使用 --claude-hook。")
        result = send_event(arguments.root, arguments.task, state, message)
        # Hook 无 stdout 决策，避免完成通知成为宿主模型的附加上下文。
        if not arguments.claude_hook:
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, urllib.error.URLError) as error:
        print(f"RemChat 通知未送达：{error}", file=sys.stderr)
        return 0 if arguments.claude_hook else 1


if __name__ == "__main__":
    raise SystemExit(main())
