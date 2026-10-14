"""Deterministic view data from actual trace evidence; no model or filesystem I/O."""

from __future__ import annotations

from copy import deepcopy
from pathlib import PurePosixPath

from .files import is_generated_path
from ..workspace_context import TEXT_EXTENSIONS


IMPORTANT_EXTENSIONS = TEXT_EXTENSIONS | {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico", ".ipynb", ".sql", ".vue", ".svelte", ".go", ".kt", ".swift", ".cs", ".rb", ".lock"}
IMPORTANT_NAMES = {"dockerfile", "makefile", "license", "notice", "readme", ".gitignore", ".gitattributes", ".editorconfig"}


def visible_changes(snapshot: dict, *, include_discovered=False, include_generated=False) -> list[dict]:
    """Keep raw history intact; return chronological copies of selected evidence.

    Important explicitly reported paths remain eligible irrespective of suffix.
    Generated items from old snapshots stay exportable but hidden by default.
    """
    result = []
    for row in snapshot.get("changes", []):
        path = row.get("path", "")
        if not isinstance(path, str) or not path:
            continue
        if row.get("operation") in {"baseline", "discovered"} and not include_discovered:
            continue
        if is_generated_path(path):
            if not include_generated:
                continue
        elif row.get("evidence") != "tool_reported":
            name = PurePosixPath(path.replace("\\", "/")).name.lower()
            if PurePosixPath(name).suffix not in IMPORTANT_EXTENSIONS and name not in IMPORTANT_NAMES:
                continue
        result.append(deepcopy(row))
    return result


def event_timeline(snapshot: dict) -> list[dict]:
    """Real receive order, including stable persisted IDs; never fabricate missing events."""
    return deepcopy(snapshot.get("timeline", []))


def phase_summary(snapshot: dict) -> dict:
    """Only reported goals/results and mechanical counts, not inferred completion."""
    steps = snapshot.get("steps", [])
    counts = {state: sum(row.get("state") == state for row in steps)
              for state in ("pending", "running", "done", "failed", "cancelled")}
    requests, checkpoints = snapshot.get("requests", []), snapshot.get("checkpoints", [])
    return {"goal": deepcopy(snapshot.get("goal")),
            "active_goal": deepcopy(snapshot.get("active_goal") or snapshot.get("goal")),
            "latest_memory": deepcopy(snapshot.get("latest_memory")),
            "tools": deepcopy(snapshot.get("tool_events", [])),
            "usage": {"turns": deepcopy(snapshot.get("usage_turns", [])),
                      "cumulative": deepcopy(snapshot.get("usage_cumulative", []))},
            "current": deepcopy([row for row in steps if row.get("state") == "running"]),
            "done": deepcopy([row for row in steps if row.get("state") == "done"]),
            "remaining": deepcopy([row for row in steps if row.get("state") not in {"done", "cancelled"}]),
            "checkpoint": deepcopy(checkpoints[-1]) if checkpoints else None,
            "request_count": len(requests), "request_total": len(requests) + snapshot.get("requests_truncated", 0),
            "counts": {**counts, "steps": len(steps), "important_changes": len(visible_changes(snapshot)),
                       "baseline_files": snapshot.get("baseline_files", 0),
                       "discovered_files": snapshot.get("discovered_files", 0)},
            "plan_revision": snapshot.get("plan_revision", 0),
            "observation_policy": snapshot.get("observation_policy", "")}
