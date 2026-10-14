"""Optional, request-scoped observation of existing calls. Never calls a model.

ContextVar isolates concurrent threads and captures the observation session at
send time. A workspace switch cannot redirect an in-flight reply to a new task.
Only selected memory, reference identifiers and response counters enter the
trace, not complete prompts, skill descriptions or keys.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import re
import math
import time


@dataclass
class _Observation:
    trace: object
    session_id: str
    request_id: str
    ledger: object = None
    metrics_session: str = ""
    calls: int = 0
    skill_references: set[str] = field(default_factory=set)


_current: ContextVar[_Observation | None] = ContextVar("remchat_session_observation", default=None)


@contextmanager
def observe_request(trace, session_id: str, request_id: str, *, ledger=None, metrics_session=""):
    """Optional trace and local usage are independent, captured request scopes."""
    snapshot = trace.snapshot() if trace is not None and session_id else {}
    traced = snapshot.get("enabled") and snapshot.get("session_id") == session_id
    observation = (_Observation(trace if traced else None, session_id if traced else "", request_id,
                                ledger=ledger, metrics_session=metrics_session or session_id)
                   if traced or (ledger is not None and metrics_session) else None)
    token = _current.set(observation)
    try:
        yield
    finally:
        _current.reset(token)


def _publish(payload: dict) -> None:
    observation = _current.get()
    if observation is None:
        return
    trace = observation.trace
    if trace is None:
        return
    try:
        # The lock makes check + accept atomic relative to workspace switches.
        with trace.lock:
            if not trace.enabled or trace.data["session_id"] != observation.session_id:
                return
            trace.accept(dict(payload, session_id=observation.session_id, source="RemChat"))
    except Exception as error:
        # Optional observation must not discard an otherwise successful reply.
        try:
            with trace.lock:
                if trace.data["session_id"] == observation.session_id:
                    trace.data["error"] = "会话记录失败：" + str(error)[:300]
                    trace.version += 1
        except Exception:
            # Failure of the optional error indicator also stays non-fatal.
            pass


def record_usage(payload: object, requested_model: str) -> None:
    """Use authoritative response usage only. Missing counters stay unknown."""
    observation = _current.get()
    if observation is None or not isinstance(payload, dict):
        return
    observation.calls += 1
    raw = payload.get("usage")
    usage = raw if isinstance(raw, dict) else {}
    def counter(*names):
        for name in names:
            value = usage.get(name)
            if type(value) is int and value >= 0:
                return value
        return None
    model = payload.get("model")
    reported_model = model.strip()[:200] if isinstance(model, str) else ""
    if not isinstance(model, str) or not model.strip():
        model = "请求配置：" + requested_model
    native_id = payload.get("id")
    usage_id = (native_id if isinstance(native_id, str) and 0 < len(native_id) <= 160
                else f"{observation.request_id}-{observation.calls}"[:160])
    tokens = {"input_tokens": counter("prompt_tokens", "input_tokens"),
              "output_tokens": counter("completion_tokens", "output_tokens"),
              "total_tokens": counter("total_tokens")}
    details = usage.get("prompt_tokens_details", usage.get("input_tokens_details", {}))
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    if type(cached) is not int or cached < 0:
        cached = counter("prompt_cache_hit_tokens")
    if type(cached) is int and cached >= 0:
        tokens["cached_input_tokens"] = cached
    _publish({"kind": "usage", "model": model[:200], "scope": "turn",
              "usage_id": usage_id, **tokens})
    if observation.ledger is not None and observation.metrics_session:
        try:
            recorder = getattr(observation.ledger, "record_usage", None) or observation.ledger.record
            received_at = time.time()
            created = payload.get("created")
            usage_time = created if type(created) in {int, float} and 0 < created <= received_at + 300 and math.isfinite(created) else received_at
            recorder("remchat", observation.metrics_session,
                {"source": "remchat_response", "model": reported_model or "unknown",
                 "usage": tokens, "usage_scope": "turn", "usage_id": usage_id,
                 "usage_id_scope": "source" if isinstance(native_id, str) and native_id.startswith(("chatcmpl-", "msg_", "resp_")) else "session",
                 "usage_time": usage_time})
        except Exception:
            # Local statistics are optional; an API reply must still reach UI.
            pass


def record_memory(memories, history) -> None:
    """Describe the actual selected context, not a claimed hidden memory state."""
    if _current.get() is None:
        return
    retained = [f"[{item.category}] {item.content}"[:1000] for item in memories[:20]]
    omitted = []
    if len(memories) > 20:
        omitted.append(f"页面仅展示前 20 项摘要，本轮实际注入 {len(memories)} 项记忆。")
    _publish({"kind": "memory", "summary": f"本轮实际注入 {len(memories)} 项已确认长期记忆和 {len(history)} 条近期消息。近期历史使用现有消息窗口，不是模型生成的语义压缩；未检查宿主的隐藏上下文。",
              "retained": retained, "omitted": omitted})


def record_skill_reference(identifier: str) -> None:
    """Observe an actual context reference, never execution or model compliance.

    Call only after the selected skill text has entered the request context.
    Reading, previewing, installing or enabling a skill is not a reference.
    """
    observation = _current.get()
    if observation is None or not isinstance(identifier, str):
        return
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}", identifier):
        return
    if identifier in observation.skill_references:
        return
    observation.skill_references.add(identifier)
    try:
        _publish({"kind": "tool", "category": "skill", "name": identifier,
                  "state": "completed",
                  "call_id": f"{observation.request_id}:skill:{identifier}"[:160],
                  "summary": "技能内容作为本轮参考注入；未执行脚本，不代表模型实际遵循。"})
    except Exception:
        # Optional instrumentation cannot prevent an otherwise valid send.
        return


def record_skill_references(identifiers: tuple[str, ...]) -> None:
    """Record composer metadata only inside its validated request observation."""
    if not isinstance(identifiers, tuple):
        return
    for identifier in identifiers:
        record_skill_reference(identifier)
