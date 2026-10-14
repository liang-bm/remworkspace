"""固定内置适配器目录与安装草稿导出，不从未知目录动态执行代码。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import shutil
import time
from typing import Any

from ..storage import checked_id
from ..monitor_runtime import export_runtime

ADAPTERS_DIRECTORY = Path(__file__).parent
ADAPTER_IDS = ("claude_code", "opencode", "codex", "chatgpt", "zcode", "workbuddy", "deepseek_harness", "hermes", "generic")


def adapter_catalog() -> list[dict[str, Any]]:
    result = []
    for identifier in ADAPTER_IDS:
        try:
            value = json.loads((ADAPTERS_DIRECTORY / identifier / "manifest.json").read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("id") != identifier or not isinstance(value.get("name"), str):
                raise ValueError("manifest id/name 与内置适配器不匹配")
            result.append({**value, "available": True})
        except (OSError, ValueError) as error:
            logging.getLogger(__name__).warning("RemChat 适配器 %s 不可用：%s", identifier, error)
            result.append({"id": identifier, "name": f"{identifier} [文件缺失/损坏]", "level": "unavailable", "available": False, "limitations": str(error)})
    return result


def export_adapter(manager: Any, identifier: str, task_id: str, *, session_id: str = "") -> dict[str, Any]:
    """只在 RemChat 数据目录创建全新草稿包，绝不改外部工具全局配置。"""

    if identifier not in ADAPTER_IDS:
        raise ValueError("未知内置适配器。")
    checked_id(task_id)
    task = manager.store.get_item("tasks", task_id)
    # Host sessions and RemChat observation sessions are unrelated identifiers.
    # Only an explicit live binding supplies a project event destination.
    binding = task.get("trace_binding")
    trace = getattr(manager, "project_trace", None)
    snapshot = trace.snapshot() if trace is not None else {}
    observation_session = ""
    if (isinstance(binding, dict) and task.get("monitoring", True)
            and snapshot.get("enabled")
            and binding.get("observation_session_id") == snapshot.get("session_id")):
        observation_session = snapshot["session_id"]
        bound_external = binding.get("external_session_id", "")
        if bound_external and session_id and bound_external != session_id:
            raise ValueError("导出的外部会话 ID 与任务关联不同，请确认目标会话后重新关联。")
        if not session_id and isinstance(bound_external, str):
            session_id = bound_external
    template_dir = ADAPTERS_DIRECTORY / identifier
    manifest = next(row for row in adapter_catalog() if row["id"] == identifier)
    if not manifest.get("available"):
        raise ValueError("适配器文件缺失或损坏，请重新安装/更新 RemChat；不影响普通聊天。")
    target = manager.store.directory / "exports" / f"{identifier}-{task_id}-{time.time_ns()}"
    target.mkdir(parents=True, exist_ok=False)
    try:
        (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        shutil.copyfile(template_dir / "README.md", target / "README.md")
        python = manager.resolve_hook_python()
        export_runtime(target / "runtime")
        notify_path = target / "runtime/remcompanion/integrations/notify.py"
        runner_path = target / "runtime/remcompanion/integrations/adapters/runner.py"
        start_args = [str(notify_path), "--root", str(manager.root), "--task", task_id, "--state", "started", "--message", "外部任务已开始。"]
        done_args = [str(notify_path), "--root", str(manager.root), "--task", task_id, "--state", "completed", "--message", "宿主报告此任务已完成；产物待验收。"]
        configuration = {"enabled": False, "root": str(manager.root), "task_id": task_id, "session_id": session_id, "python_executable": python, "started_argv": [python, *start_args], "completed_argv": [python, *done_args], "failed_argv": [python, *done_args[:-4], "--state", "failed", "--message", "宿主报告任务失败。"], "batch_wrapper_argv": [python, str(runner_path), "--root", str(manager.root), "--task", task_id, "--batch", "--", "明确填写非交互CLI程序", "明确填写它的参数"]}
        configuration.update({
            "observation_session_id": observation_session,
            "external_session_id": session_id,
            "session_id_note": "session_id 是旧版外部宿主会话别名，不是 RemChat 项目观察 ID。",
            "project_event_argv": [python, str(target / "runtime/remcompanion/project_trace/send.py"),
                                   "--root", str(manager.root), "--session", observation_session,
                                   "--event", "用户提供的结构化项目事件.json", "--quiet"] if observation_session else [],
            "project_event_note": "仅显式上报真实计划/结果/交接，不读取隐藏日志。未关联当前项目时命令为空；切换项目后需重新关联并导出。",
        })
        if identifier == "claude_code":
            native = manager.claude_hook_config(task_id, python, notify_script=notify_path)
            (target / "hooks.merge.json").write_text(json.dumps(native, ensure_ascii=False, indent=2), encoding="utf-8")
            modern = manager.claude_hook_config(task_id, python, notify_script=notify_path, exec_form=True)
            (target / "hooks.exec.merge.json").write_text(json.dumps(modern, ensure_ascii=False, indent=2), encoding="utf-8")
        elif identifier == "codex":
            configuration["codex_jsonl_wrapper_argv"] = [python, str(target / "runtime/remcompanion/integrations/adapters/codex/runner.py"), "--root", str(manager.root), "--task", task_id, "--batch", "--", shutil.which("codex") or "codex", "exec", "--json", "用户填写任务提示词"]
        elif identifier == "opencode":
            content = (template_dir / "plugin.mjs").read_text(encoding="utf-8")
            for key, value in (("__REMCHAT_ROOT__", str(manager.root).replace("\\", "/")), ("__REMCHAT_TASK__", task_id), ("__REMCHAT_SESSION__", session_id)):
                content = content.replace(key, json.dumps(value, ensure_ascii=False))
            (target / "remchat-monitor.mjs").write_text(content, encoding="utf-8")
        elif identifier == "zcode":
            # Stop 是结束前检查，可能被其他 Hook 阻止：不把它映射为 completed。
            value = {"hooks": {"enabled": False, "events": {"UserPromptSubmit": [{"hooks": [{"type": "process", "command": python, "args": start_args, "enabled": False, "timeoutMs": 8000}]}]}}}
            (target / "hooks.merge.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        elif identifier == "hermes":
            content = (template_dir / "handler.py.in").read_text(encoding="utf-8")
            for key, value in (("__REMCHAT_ROOT__", str(manager.root)), ("__REMCHAT_TASK__", task_id), ("__REMCHAT_SESSION__", session_id)):
                content = content.replace(key, repr(value))
            (target / "handler.py").write_text(content, encoding="utf-8")
            (target / "HOOK.yaml").write_text("name: remchat-monitor\ndescription: Notify RemChat about selected gateway session\nevents:\n  - agent:start\n  - agent:end\n", encoding="utf-8")
        (target / "task-config.json").write_text(json.dumps(configuration, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"directory": str(target), "adapter": manifest, "enabled": False, "task_id": task_id, "note": "草稿已导出，未安装。阅读 README，检查会话范围与命令后手动合并/启用。"}
    except Exception:
        # 只撤回此次新建的草稿目录，路径由固定内置 id + 校验任务 id 创建。
        shutil.rmtree(target)
        raise
