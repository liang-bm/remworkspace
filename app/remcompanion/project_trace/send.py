"""Standard-library CLI helper. Sends evidence, never executes project actions.

python send.py --root /path/to/remworkspace --session SESSION --event plan.json
The caller supplies goal/goal_update/request/checkpoint/memory/tool/usage/plan/
step/file JSON. Observation itself makes no model call and spends no model tokens.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlparse
import urllib.request
import uuid


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("项目过程事件不允许重定向")


def send(root: Path, session_id: str, payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("事件需要 JSON 对象")
    directory = root / "data/integrations"
    receiver = json.loads((directory / "receiver.json").read_text(encoding="utf-8"))
    parsed = urlparse(str(receiver.get("url", "")))
    if not receiver.get("running") or parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path != "/events" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("请先在 RemChat 复制当前项目接入信息，启动本机接收器")
    body = json.dumps({**payload, "session_id": session_id, "event_id": payload.get("event_id") or uuid.uuid4().hex}, ensure_ascii=False).encode("utf-8")
    if len(body) > 65536:
        raise ValueError("项目事件不能超过 64 KiB")
    url = f"http://127.0.0.1:{parsed.port}/project-events"
    token = (directory / "receiver-token.txt").read_text(encoding="utf-8").strip()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "Authorization": "Bearer " + token}, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=5) as response:
        return json.loads(response.read(65536))


def main() -> int:
    parser = argparse.ArgumentParser(description="向可选的 RemChat 项目过程视图上报结构化事件")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--event", type=Path, required=True, help="goal / request / checkpoint / memory / tool / usage / plan / step / file JSON")
    parser.add_argument("--quiet", action="store_true", help="成功时不输出，适合 CLI Hook，减少宿主上下文")
    arguments = parser.parse_args()
    try:
        with arguments.event.open("rb") as stream:
            body = stream.read(65537)
        if len(body) > 65536:
            raise ValueError("项目事件不能超过 64 KiB")
        value = json.loads(body.decode("utf-8-sig"))
        result = send(arguments.root.resolve(), arguments.session, value)
        if not arguments.quiet:
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as error:
        print(f"RemChat 项目事件未送达：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
