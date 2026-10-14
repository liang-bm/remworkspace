""""界面工作队列中的不可变消息，不包含任何 Tk 操作。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatEvent:
    kind: str
    payload: object | None = None
