"""Event-driven desktop task rings; no model calls and no inferred completion.

THESIS: small real-status companions beside Rem, not another chat bubble.
OWN-WORLD: existing blue/grey tokens, transparent surroundings, readable labels.
STORY: see up to three concurrent tasks, click to inspect every tracked task.
FIRST VIEWPORT: elapsed/status inside rings; task names outside; hidden when empty.
FORM: local extension of the desktop pet; independent window preserves its bounds.
"""
from __future__ import annotations

from copy import deepcopy
import math
import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont, messagebox, ttk
from typing import Callable

from ..platforms import native_module
from ..window_geometry import WindowRect
from . import theme
from .selection_feedback import CheckedTreeSelection, Tooltip, TopNotice


TRANSPARENT = "#010203"
WORDS = {
    "zh-Hans": ("运行中", "已结束", "失败", "已取消", "等待接入", "时长未知", "已停止观察", "监工任务", "任务", "状态", "时长", "接入 / 管理追踪", "更多任务", "只接收任务事件，不调用模型，也不控制外部任务。"),
    "zh-Hant": ("執行中", "已結束", "失敗", "已取消", "等待接入", "時長未知", "已停止觀察", "監工任務", "任務", "狀態", "時長", "接入 / 管理追蹤", "更多任務", "只接收任務事件，不呼叫模型，也不控制外部任務。"),
    "en": ("Running", "Ended", "Failed", "Cancelled", "Waiting", "Unknown", "Not observing", "Tracked tasks", "Task", "Status", "Time", "Connect / manage tracking", "More tasks", "Events only. No model calls or control of external tasks."),
    "ja": ("実行中", "終了", "失敗", "取消済み", "接続待ち", "時間不明", "監視停止", "監視タスク", "タスク", "状態", "時間", "接続 / 追跡管理", "他のタスク", "タスクのイベントのみ受信。モデル呼び出しや外部タスクの制御は行いません。"),
}
EXTRA_WORDS = {
    "zh-Hans": {"idle": "空闲", "unknown": "状态未知", "disconnected": "同步中断", "sessions": "运行 / 会话", "all": "全部工具 / 任务", "id": "会话 ID", "workspace": "工作目录", "bind": "关联到当前项目", "binding": "正在关联…", "bound": "已关联当前项目；不会补造旧事件。", "bind_off": "项目观察未开启，不能关联。"},
    "zh-Hant": {"idle": "閒置", "unknown": "狀態未知", "disconnected": "同步中斷", "sessions": "執行 / 會話", "all": "全部工具 / 任務", "id": "會話 ID", "workspace": "工作目錄", "bind": "關聯到目前專案", "binding": "正在關聯…", "bound": "已關聯目前專案；不會補造舊事件。", "bind_off": "專案觀察未開啟，不能關聯。"},
    "en": {"idle": "Idle", "unknown": "Unknown", "disconnected": "Sync lost", "sessions": "Running / sessions", "all": "All tools / tasks", "id": "Session ID", "workspace": "Workspace", "bind": "Link to current project", "binding": "Linking…", "bound": "Linked to current project; no historical events invented.", "bind_off": "Enable project observation before linking."},
    "ja": {"idle": "待機中", "unknown": "状態不明", "disconnected": "同期中断", "sessions": "実行 / セッション", "all": "すべてのツール / タスク", "id": "セッション ID", "workspace": "作業ディレクトリ", "bind": "現在のプロジェクトに関連付け", "binding": "関連付け中…", "bound": "関連付けました。過去のイベントは作成しません。", "bind_off": "先にプロジェクト観察を有効にしてください。"},
}

RECORD_SOURCES = frozenset({"local_session_record", "claude_session_record", "antigravity_session_record"})

DISPLAY_WORDS = {
    "zh-Hans": {"hide_group": "隐藏此工具环", "hide_selected": "隐藏选中工具环", "hide_all": "隐藏全部跟踪环", "restore_all": "恢复全部跟踪环", "display_hint": "隐藏只关闭桌面显示；监工与计时记录继续。恢复入口：设置 → 通用 → 跟踪环。", "hidden": "已隐藏桌面跟踪环；后台监工继续。", "restored": "已恢复跟踪环；没有重新启动外部任务。", "save_error": "无法保存跟踪环显示设置", "save_hint": "显示设置未变。请检查桌面偏好文件是否可写、是否为有效 JSON。"},
    "zh-Hant": {"hide_group": "隱藏此工具環", "hide_selected": "隱藏所選工具環", "hide_all": "隱藏全部跟蹤環", "restore_all": "恢復全部跟蹤環", "display_hint": "隱藏僅關閉桌面顯示；監工與計時記錄繼續。恢復入口：設定 → 通用 → 跟蹤環。", "hidden": "已隱藏桌面跟蹤環；背景監工繼續。", "restored": "已恢復跟蹤環；未重新啟動外部任務。", "save_error": "無法儲存跟蹤環顯示設定", "save_hint": "顯示設定未變。請檢查桌面偏好檔案是否可寫、是否為有效 JSON。"},
    "en": {"hide_group": "Hide this tool ring", "hide_selected": "Hide selected tool ring", "hide_all": "Hide all tracking rings", "restore_all": "Restore all tracking rings", "display_hint": "Hiding affects display only; monitoring and recorded time continue. Restore in Settings → General → Tracking rings.", "hidden": "Desktop rings hidden; background monitoring continues.", "restored": "Tracking rings restored. External tasks were not restarted.", "save_error": "Cannot save tracking-ring display settings", "save_hint": "Display settings were not changed. Check that desktop preferences are writable and valid JSON."},
    "ja": {"hide_group": "このツールのリングを非表示", "hide_selected": "選択したツールのリングを非表示", "hide_all": "すべてのリングを非表示", "restore_all": "すべてのリングを再表示", "display_hint": "非表示は表示のみ変更し、監視と時間記録は継続します。設定 → 一般 → 追跡リングから復元できます。", "hidden": "リングを非表示にしました。バックグラウンドの監視は継続します。", "restored": "リングを再表示しました。外部タスクは再起動していません。", "save_error": "リングの表示設定を保存できません", "save_hint": "表示設定は変更されていません。設定ファイルの書き込み権限と JSON 形式を確認してください。"},
}

ACTION_WORDS = {
    "zh-Hans": {"choose": "选择", "selected": "已选 {count} 项", "all_items": "全选", "clear": "取消选中", "stop": "停止观察", "remove": "移除记录", "working": "正在处理…", "stop_hint": "停止 RemChat 的观察并冻结计时；不会停止外部 AI。记录仍可查看。", "remove_hint": "移除所选监工记录，不删除原工具的聊天、项目文件或用量历史。", "confirm": "移除这 {count} 条监工记录？\n\n{name}\n\n仅移除 RemChat 观察记录；不会删除外部聊天、项目文件或用量历史。", "stopped": "已停止观察 {count} 项；外部 AI 未停止。", "removed": "已移除 {count} 条监工记录；外部聊天与用量历史保留。", "failed": "操作失败：{error}", "many": "已选 {count} 项。可批量停止观察或移除；只选一项时显示会话详情。", "waiting_hint": "仅手工登记，尚未收到开始事件；不是已连接的 AI 会话。可停止观察或移除这条记录。"},
    "zh-Hant": {"choose": "選擇", "selected": "已選 {count} 項", "all_items": "全選", "clear": "取消選取", "stop": "停止觀察", "remove": "移除記錄", "working": "正在處理…", "stop_hint": "停止 RemChat 觀察並凍結計時；不會停止外部 AI。記錄仍可查看。", "remove_hint": "移除所選監工記錄，不刪除原工具的聊天、專案檔案或用量歷史。", "confirm": "移除這 {count} 條監工記錄？\n\n{name}\n\n僅移除 RemChat 觀察記錄；不刪除外部聊天、專案檔案或用量歷史。", "stopped": "已停止觀察 {count} 項；外部 AI 未停止。", "removed": "已移除 {count} 條監工記錄；外部聊天與用量歷史保留。", "failed": "操作失敗：{error}", "many": "已選 {count} 項。可批次停止觀察或移除；只選一項時顯示會話詳情。", "waiting_hint": "僅手工登記，尚未收到開始事件；不是已連接的 AI 會話。可停止觀察或移除此記錄。"},
    "en": {"choose": "Select", "selected": "{count} selected", "all_items": "Select all", "clear": "Clear selection", "stop": "Stop observing", "remove": "Remove records", "working": "Working…", "stop_hint": "Stop RemChat observation and freeze its timer. The external AI keeps running; records remain.", "remove_hint": "Remove selected tracking records, not external chats, project files or usage history.", "confirm": "Remove {count} tracking records?\n\n{name}\n\nOnly RemChat observation records are removed. External chats, project files and usage history remain.", "stopped": "Stopped observing {count} tasks. External AI was not stopped.", "removed": "Removed {count} tracking records. External chats and usage history remain.", "failed": "Operation failed: {error}", "many": "{count} selected. Stop or remove together; select one task to inspect its details.", "waiting_hint": "Manually registered; no start event received. This is not a connected AI session. You can stop observing or remove this record."},
    "ja": {"choose": "選択", "selected": "{count} 件選択", "all_items": "すべて選択", "clear": "選択解除", "stop": "監視を停止", "remove": "記録を削除", "working": "処理中…", "stop_hint": "RemChat の監視と計時のみ停止します。外部 AI は停止せず、記録は残ります。", "remove_hint": "監視記録のみ削除します。外部チャット、プロジェクトや利用履歴は残ります。", "confirm": "監視記録 {count} 件を削除しますか？\n\n{name}\n\nRemChat の監視記録のみ削除します。外部チャット、プロジェクトや利用履歴は残ります。", "stopped": "{count} 件の監視を停止しました。外部 AI は停止していません。", "removed": "監視記録 {count} 件を削除しました。外部チャットと利用履歴は残ります。", "failed": "操作に失敗：{error}", "many": "{count} 件選択。まとめて監視停止・削除できます。詳細は一件を選択してください。", "waiting_hint": "手動登録のみで開始イベントは未受信です。接続済み AI セッションではありません。監視停止・削除できます。"},
}


def is_record_source(row: dict) -> bool:
    """A persisted record is not proof that its host is currently running."""
    return row.get("source") in RECORD_SOURCES


def record_sync_connected(row: dict) -> bool:
    """Observer connectivity is independent of the host's process liveness."""
    return (is_record_source(row) and row.get("monitoring", True)
            and row.get("sync_state") == "connected")


def _timestamp(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result > 0 else None
    except (OverflowError, ValueError):
        return None


def task_status(row: dict, now: float | None = None) -> tuple[str, int | None]:
    """Return a semantic state and elapsed seconds, never registration age.

    Terminal clocks only use finished_at; stopping observation uses observed_until.
    Older records without evidence of a start explicitly have unknown duration.
    """
    now = time.time() if now is None else now
    start = _timestamp(row.get("started_at"))
    state = str(row.get("state", "started"))
    detail = row.get("session_detail")
    detail = detail if isinstance(detail, dict) else {}
    local_record = is_record_source(row)
    sync = str(row.get("sync_state") or detail.get("sync_state") or "")
    if sync in {"lost", "sync_lost", "disconnected", "error", "stale"} and state not in {"completed", "failed", "cancelled"}:
        state = "disconnected"
    if not row.get("monitoring", True):
        state, end = "stopped", _timestamp(row.get("observed_until"))
    elif state in {"started", "registered"}:
        native_started = state == "started" and bool(row.get("external_session_id"))
        state = "running" if start or native_started else ("waiting" if "started_at" in row else "unknown")
        end = (_timestamp(row.get("finished_at")) or _timestamp(detail.get("last_activity"))) if local_record else _timestamp(now)
    elif state == "running":
        end = (_timestamp(row.get("finished_at")) or _timestamp(detail.get("last_activity"))) if local_record else _timestamp(now)
    elif state in {"idle", "unknown", "disconnected", "sync_lost", "waiting"}:
        # A missing/stale native source never implies completion. Freeze the
        # known clock at the final observation rather than pretending liveness.
        end = (_timestamp(row.get("observed_until")) or _timestamp(row.get("last_activity"))
               or _timestamp(detail.get("last_activity")) or _timestamp(row.get("updated_at")))
    else:
        if state not in {"completed", "failed", "cancelled", "stopped"}:
            state = "unknown"
        end = _timestamp(row.get("finished_at"))
    elapsed = max(0, int(end - start)) if start is not None and end is not None else None
    if local_record and state == "running":
        state = "unknown"  # A recorded start is evidence, not a live process list.
    return state, elapsed


def task_groups(rows: list[dict]) -> list[dict]:
    """Group explicit external sessions, while keeping legacy tasks independent."""
    groups: dict[str, dict] = {}
    for row in rows:
        connection = str(row.get("connection_id") or "")
        tool = str(row.get("tool_id") or "")
        key = "connection:" + connection if connection else "tool:" + tool if tool else "task:" + str(row["id"])
        group = groups.setdefault(key, {"id": key, "name": row.get("tool_name") or tool or row.get("name", ""),
                                        "rows": [], "native": bool(connection or tool)})
        group["rows"].append(row)
    for group in groups.values():
        states = [task_status(row)[0] for row in group["rows"]]
        group["record"] = all(is_record_source(row) for row in group["rows"])
        group["record_connected"] = group["record"] and any(record_sync_connected(row) for row in group["rows"])
        group["record_started"] = sum(record_sync_connected(row) and row.get("state") == "started"
                                      and _timestamp(row.get("started_at")) is not None for row in group["rows"])
        group["record_known"] = any(row.get("state") in {"started", "completed", "failed", "cancelled", "idle"}
                                    for row in group["rows"])
        group["running"], group["total"] = states.count("running"), len(states)
        if group["running"]:
            group["state"] = "running"
        elif all(state == "completed" for state in states):
            group["state"] = "completed"
        else:
            group["state"] = next((state for state in ("failed", "disconnected", "sync_lost", "unknown", "waiting", "idle", "stopped", "cancelled") if state in states), "unknown")
    return sorted(groups.values(), key=lambda group: (0 if group["running"] else 1, -max((_timestamp(row.get("updated_at")) or 0) for row in group["rows"]), group["id"]))


def elapsed_text(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    minutes, second = divmod(max(0, seconds), 60)
    if minutes >= 60:
        hours, minute = divmod(minutes, 60)
        return f"{hours}:{minute:02}:{second:02}"
    return f"{minutes:02}:{second:02}"


def two_line_name(text: str, font: tkfont.Font, width: float) -> str:
    """Explicit measured lines: Tk word-wrap may turn a width estimate into 3 lines."""
    remaining = " ".join(text.split())
    lines = []
    for line_index in range(2):
        if font.measure(remaining) <= width:
            lines.append(remaining)
            break
        cut = 0
        suffix = "…" if line_index else ""
        while cut < len(remaining) and font.measure(remaining[:cut+1]+suffix) <= width:
            cut += 1
        if not cut:
            return "…"
        if line_index == 0:
            space = remaining.rfind(" ", 0, cut+1)
            if space > 0:
                cut = space
        lines.append(remaining[:cut].rstrip()+suffix)
        remaining = remaining[cut:].lstrip()
    return "\n".join(lines)


def ring_position(pet: WindowRect, width: int, height: int, work: WindowRect, gap: int = 12) -> tuple[int, int]:
    """Prefer Rem's left, switch sides at display boundaries, allow negative screens."""
    x = pet.left - width - gap
    if x < work.left + gap and work.right - pet.right >= width + gap:
        x = pet.right + gap
    y = pet.top + (pet.height - height) // 2
    return (max(work.left, min(x, work.right - width)),
            max(work.top, min(y, work.bottom - height)))


class TaskRings:
    """All manager callbacks enqueue data. Tk and time rendering stay on its thread."""

    def __init__(self, master: tk.Misc, manager, *, anchor_provider: Callable[[], WindowRect | None],
                 language: Callable[[], str] = lambda: "zh-Hans", on_manage: Callable[[], None] = lambda: None,
                 visible: bool = True, hidden_groups: tuple[str, ...] = (),
                 on_display_change: Callable[[bool, tuple[str, ...]], object] | None = None):
        self.master, self.manager = master, manager
        self.anchor_provider, self.language, self.on_manage = anchor_provider, language, on_manage
        self.closed = False
        self.pending: queue.Queue[dict] = queue.Queue(maxsize=256)
        self.results: queue.SimpleQueue[dict] = queue.SimpleQueue()
        self.overflow = threading.Event()
        self.rows: dict[str, dict] = {}
        self.popup: tk.Toplevel | None = None
        self._group_filter = ""
        self._bind_busy = False
        self._operation_busy = False
        self._last_render = None
        self._last_popup_refresh = None
        self._reserve_after = None
        self.visible = bool(visible)
        self.hidden_groups = set(hidden_groups)
        self.on_display_change = on_display_change
        self._dismiss_buttons: dict[str, tk.Button] = {}
        self._context_menu: tk.Menu | None = None
        # Subscribe first: concurrent events cannot disappear during the initial snapshot.
        self.unsubscribe = manager.subscribe(self._enqueue)
        try:
            self.rows = {str(row["id"]): row for row in manager.list_tasks()}
        except (OSError, ValueError, KeyError):
            self.overflow.set()
        self.window = tk.Toplevel(master)
        self.window.withdraw()
        self.window.title("RemChatTaskRings")
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg=TRANSPARENT)
        native_module("windowing").configure_overlay_transparency(self.window, TRANSPARENT)
        self.canvas = tk.Canvas(self.window, bg=TRANSPARENT, highlightthickness=1, highlightbackground=TRANSPARENT,
                                highlightcolor=theme.palette()["ACCENT"], takefocus=True, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._ring_click)
        self.canvas.bind("<Return>", lambda _event: self.show_tasks())
        self.canvas.bind("<space>", lambda _event: self.show_tasks())
        self.canvas.bind("<Button-3>", self._show_context_menu)
        self.canvas.bind("<Shift-F10>", self._show_context_menu)
        self.canvas.bind("<Escape>", lambda _event: self.hide_all())
        self.sync_tooltip = Tooltip(self.canvas, self._sync_hint)
        self._after = master.after(250, self._tick)

    def _enqueue(self, event: dict) -> None:
        if self.closed or event.get("type") not in {"task", "task_removed"}:
            return
        try:
            self.pending.put_nowait(deepcopy(event))
        except queue.Full:
            self.overflow.set()

    def _words(self):
        return WORDS.get(self.language(), WORDS["zh-Hans"])

    def _caption(self, key):
        if key in ACTION_WORDS["zh-Hans"]:
            return ACTION_WORDS.get(self.language(), ACTION_WORDS["zh-Hans"])[key]
        if key in DISPLAY_WORDS["zh-Hans"]:
            return DISPLAY_WORDS.get(self.language(), DISPLAY_WORDS["zh-Hans"])[key]
        if key in {"bind", "bound"}:
            focused = {
                "zh-Hans": ("聚焦到项目视图", "项目视图已聚焦此会话；其他会话继续监工，不会补造旧事件。"),
                "zh-Hant": ("聚焦到專案視圖", "專案視圖已聚焦此會話；其他會話繼續監工，不會補造舊事件。"),
                "en": ("Focus in project view", "Project view focuses this session; other sessions stay monitored. No historical events invented."),
                "ja": ("プロジェクト表示にフォーカス", "このセッションを表示します。他のセッションの監視も継続します。過去のイベントは作成しません。"),
            }
            return focused.get(self.language(), focused["zh-Hans"])[0 if key == "bind" else 1]
        return EXTRA_WORDS.get(self.language(), EXTRA_WORDS["zh-Hans"])[key]

    def _record_label(self):
        return {"zh-Hans": "记录（非实时）", "zh-Hant": "記錄（非即時）", "en": "Records (not live)", "ja": "記録（非リアルタイム）"}.get(self.language(), "记录（非实时）")

    def _sync_label(self):
        return {"zh-Hans": "本地同步", "zh-Hant": "本機同步", "en": "Local sync", "ja": "ローカル同期"}.get(self.language(), "本地同步")

    def _connected_label(self):
        return {"zh-Hans": "已接入", "zh-Hant": "已接入", "en": "Connected", "ja": "接続済み"}.get(self.language(), "已接入")

    def _sync_hint(self):
        return {"zh-Hans": "蓝色表示 RemChat 正在同步选中会话的本地事件，不代表 AI 进程实时在线。\n数字为已记录开始且未见结束的回合 / 会话总数；时长只累加到最后确认的活动。\n点击查看会话、最近活动与调用详情。",
                "zh-Hant": "藍色表示 RemChat 正在同步所選會話的本機事件，不代表 AI 程序即時在線。\n數字為已記錄開始且未見結束的回合 / 會話總數；時長只累加至最後確認的活動。\n點擊查看會話、最近活動與呼叫詳情。",
                "en": "Blue means RemChat is syncing local events for selected sessions, not that an AI process is live.\nCounts are recorded open turns / selected sessions; time stops at confirmed activity.\nClick for sessions, recent activity and calls.",
                "ja": "青は選択セッションのローカルイベント同期を示し、AI プロセスの稼働証明ではありません。\n数値は開始記録があり終了未記録のターン / セッション数。時間は確認済み活動までです。\nクリックして詳細を表示。"}.get(self.language(), "本地事件同步；不代表实时进程状态。")

    def _row_label(self, row, state):
        if is_record_source(row):
            label = self._label(state)
            if row.get("state") in {"started", "running"} and state == "unknown":
                label = {"zh-Hans": "回合已开始 · 未见结束", "zh-Hant": "回合已開始 · 未見結束", "en": "Turn started · no end recorded", "ja": "ターン開始済み・終了未記録"}.get(self.language(), "回合已开始 · 未见结束")
            return label
        return self._label(state)

    def _label(self, state: str) -> str:
        if state in {"idle", "unknown", "disconnected", "sync_lost"}:
            return self._caption("disconnected" if state == "sync_lost" else state)
        index = {"running": 0, "completed": 1, "failed": 2, "cancelled": 3, "waiting": 4,
                 "unknown": 5, "stopped": 6}.get(state, 5)
        return self._words()[index]

    def _ordered(self) -> list[dict]:
        def key(row):
            state, _elapsed = task_status(row)
            priority = 0 if state == "running" else 1 if state == "waiting" else 2
            return priority, -(_timestamp(row.get("updated_at")) or 0), str(row.get("id", ""))
        return sorted(self.rows.values(), key=key)

    def _tick(self):
        if self.closed:
            return
        try:
            while not self.results.empty():
                result = self.results.get_nowait()
                self._operation_busy = False
                self._bind_busy = False
                self._popup_feedback = result["message"]
                if self.popup is not None and self.popup.winfo_exists():
                    self.top_notice.show(result["message"], error=result.get("error", False))
                    self._selection()
            # Bounded work per UI tick; overflow reconciles with the local store.
            for _index in range(256):
                try:
                    event = self.pending.get_nowait()
                except queue.Empty:
                    break
                if event["type"] == "task_removed":
                    self.rows.pop(str(event.get("task_id", "")), None)
                elif isinstance(event.get("task"), dict):
                    row = event["task"]
                    self.rows[str(row["id"])] = row
                elif event["type"] == "ui_binding":
                    self._bind_busy = False
                    self._popup_feedback = event["message"]
                    if self.popup is not None and self.popup.winfo_exists():
                        self.notice.configure(text=event["message"])
                        self._selection()
            if self.overflow.is_set():
                self.overflow.clear()
                try:
                    self.rows = {str(row["id"]): row for row in self.manager.list_tasks()}
                except (OSError, ValueError, KeyError):
                    self.overflow.set()
            rows = self._ordered()
            self._render_overlay(rows)
            # Hidden ornaments still receive events and keep an open detail
            # window current. Display preferences never stop observations.
            popup_stamp = (int(time.time()), repr(rows), self.language(), theme.palette()["APP_BG"])
            if popup_stamp != self._last_popup_refresh:
                self._last_popup_refresh = popup_stamp
                self._refresh_popup(rows)
        except tk.TclError as error:
            self._last_error = str(error)
            print(f"[监工] 跟踪环已停止刷新：{error}")
            self.close()
            return
        self._after = self.master.after(250, self._tick)

    def _render_overlay(self, rows):
        # Stopped observations stay inspectable in management, not on the desktop.
        groups = [group for group in task_groups([row for row in rows if row.get("monitoring", True)])
                  if group["id"] not in self.hidden_groups]
        anchor = self.anchor_provider()
        if not self.visible or not groups or anchor is None or self.master.state() == "withdrawn":
            self.window.withdraw()
            return
        # Desktop ornament stays compact even on high-DPI screens.
        factor = min(1.3, max(1.0, float(self.master.tk.call("tk", "scaling")) / (96 / 72)))
        width, height = round(224 * factor), round((84 * min(3, len(groups)) + (22 if len(groups) > 3 else 0)) * factor)
        fallback = WindowRect(0, 0, self.master.winfo_screenwidth(), self.master.winfo_screenheight())
        work = native_module("windowing").work_area_at(anchor, fallback)
        width, height = min(width, work.width), min(height, work.height)
        x, y = ring_position(anchor, width, height, work)
        self.window.geometry(f"{width}x{height}{x:+}{y:+}")
        stamp = (int(time.time()), repr(rows), self.language(), theme.palette()["APP_BG"], width, height)
        if stamp != self._last_render:
            self._last_render = stamp
            self._draw(groups, factor, width)
        if self.window.state() == "withdrawn":
            self.window.deiconify()
            native_module("windowing").remove_pet_frame(self.window)

    def _set_display(self, visible: bool, hidden_groups: set[str], feedback: str) -> bool:
        if self.closed:
            return False
        try:
            if self.on_display_change is not None:
                result = self.on_display_change(visible, tuple(sorted(hidden_groups)))
                if result is False:
                    raise OSError(self._caption("save_hint"))
        except (OSError, ValueError, UnicodeError):
            messagebox.showerror(self._caption("save_error"), self._caption("save_hint"),
                                 parent=self.popup or self.window)
            return False
        self.visible, self.hidden_groups = visible, set(hidden_groups)
        self._popup_feedback = self._caption(feedback)
        self._last_render = None
        rows = self._ordered()
        self._render_overlay(rows)
        self._refresh_popup(rows)
        return True

    def hide_group(self, group_id: str) -> bool:
        if not group_id:
            return False
        return self._set_display(self.visible, self.hidden_groups | {group_id}, "hidden")

    def hide_all(self) -> bool:
        return self._set_display(False, self.hidden_groups, "hidden")

    def restore_all(self) -> bool:
        return self._set_display(True, set(), "restored")

    def _selected_group(self) -> str:
        if self.popup is not None and self.popup.winfo_exists():
            selected = self.task_list.selection()
            if selected:
                return self.task_list.parent(selected[0]) or selected[0]
        return self._group_filter

    def _hide_selected(self):
        groups = {self.task_list.parent(item) or item for item in self.task_list.selection()}
        if groups:
            self._set_display(self.visible, self.hidden_groups | groups, "hidden")

    def _show_context_menu(self, event):
        if self._context_menu is not None:
            self._context_menu.destroy()
        colors = theme.palette()
        menu = self._context_menu = tk.Menu(self.window, tearoff=False, bg=colors["WHITE"],
                                           fg=colors["INK"], activebackground=colors["ACCENT_SOFT"],
                                           activeforeground=colors["INK"])
        if event.type == tk.EventType.ButtonPress:
            index = int(event.y / (84 * getattr(self, "_ring_factor", 1)))
            groups = getattr(self, "_visible_groups", [])
            if 0 <= index < len(groups):
                menu.add_command(label=self._caption("hide_group"),
                                 command=lambda group_id=groups[index]["id"]: self.hide_group(group_id))
        menu.add_command(label=self._caption("hide_all"), command=self.hide_all)
        menu.add_command(label=self._caption("restore_all"), command=self.restore_all)
        menu.add_separator()
        menu.add_command(label=self._words()[7], command=self.show_tasks)
        menu.add_command(label=self._words()[11], command=self._manage)
        try:
            x, y = (event.x_root, event.y_root) if event.type == tk.EventType.ButtonPress else (self.canvas.winfo_rootx()+8, self.canvas.winfo_rooty()+8)
            menu.tk_popup(x, y)
        except tk.TclError:
            # A native Windows menu can outlive its owner during app exit.
            if not self.closed:
                raise
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass

    def _draw(self, groups, factor, width):
        self.canvas.delete("all")
        colors = theme.palette()
        # Ornament geometry deliberately caps DPI growth. Point fonts would keep
        # growing at 200/300% and escape those compact bounds; use pixel fonts
        # tied to the same geometry instead. Main chat fonts remain untouched.
        title_font = tkfont.Font(root=self.window, family=theme.BODY_FONT, size=-round(12*factor))
        below_font = tkfont.Font(root=self.window, family=theme.BODY_FONT, size=-round(9*factor))
        more_font = tkfont.Font(root=self.window, family=theme.BODY_FONT, size=-round(11*factor))
        center_fonts = []
        self._visible_groups, self._ring_factor = groups[:3], factor
        shown_ids = {group["id"] for group in self._visible_groups}
        for group_id in set(self._dismiss_buttons) - shown_ids:
            self._dismiss_buttons.pop(group_id).destroy()
        for index, group in enumerate(groups[:3]):
            row = group["rows"][0]
            top = index * 84 * factor
            cx, cy, radius = 39 * factor, top + 41 * factor, 34 * factor
            state, elapsed = task_status(row)
            if group["native"]:
                state = group["state"]
            color = colors["ERROR"] if state == "failed" else colors["SUCCESS"] if state == "completed" else colors["ACCENT"] if state == "running" or group["record_connected"] else colors["MUTED"]
            bbox = (cx-radius, cy-radius, cx+radius, cy+radius)
            self.canvas.create_oval(*bbox, fill=colors["WHITE"], outline=colors["LINE"], width=max(1, 2*factor))
            if state == "running" or group["record_connected"] and state not in {"completed", "failed", "cancelled"}:
                # Native running state, or local observer connectivity. The
                # latter is labelled Local sync, never host-process liveness.
                self.canvas.create_arc(*bbox, style="arc", start=-(int(time.monotonic()) * 42) % 360,
                                       extent=100, outline=color, width=max(2, 3*factor))
                center, below = elapsed_text(elapsed), self._label(state)
            else:
                self.canvas.create_oval(*bbox, outline=color, width=max(1, 2*factor))
                center, below = self._label(state), elapsed_text(elapsed)
                if state == "stopped" and self.language() == "en":
                    center = "Stopped"
            if group["native"]:
                center = f"{group['running']}/{group['total']}"
                below = self._caption("sessions")
                if group["record"]:
                    if group["record_connected"]:
                        center = f"{group['record_started']}/{group['total']}" if group["record_known"] else self._connected_label()
                        below = self._sync_label()
                    else:
                        center, below = f"—/{group['total']}", self._label("disconnected")
            center_pixels = round(11*factor)
            center_font = tkfont.Font(root=self.window, family=theme.BODY_FONT, size=-center_pixels, weight="bold")
            while center_pixels > round(6*factor) and center_font.measure(center) > radius*1.85:
                center_pixels -= 1
                center_font.configure(size=-center_pixels)
            if center_font.measure(center) > radius*1.85:
                while center and center_font.measure(center+"…") > radius*1.85:
                    center = center[:-1]
                center += "…"
            center_fonts.append(center_font)
            self.canvas.create_text(cx, cy-6*factor, text=center, fill=color, font=center_font)
            self.canvas.create_text(cx, cy+15*factor, text=below, fill=colors["MUTED"], font=below_font)
            # Names sit on a readable token-coloured capsule, never on transparent wallpaper.
            left, right = 81*factor, width-3*factor
            self.canvas.create_rectangle(left, cy-23*factor, right, cy+23*factor,
                                         fill=colors["WHITE"], outline=colors["LINE"])
            close_size = round(26*factor)
            name = two_line_name(str(group.get("name", "")), title_font, max(8, right-left-close_size-16*factor))
            self.canvas.create_text(left+6*factor, cy, text=name, anchor="w",
                                    fill=colors["INK"], font=title_font)
            button = self._dismiss_buttons.get(group["id"])
            if button is None:
                button = tk.Button(self.canvas, text="×", relief="flat", bd=0,
                                   command=lambda group_id=group["id"]: self.hide_group(group_id),
                                   takefocus=True, highlightthickness=1)
                button.bind("<Escape>", lambda _event: self.hide_all())
                self._dismiss_buttons[group["id"]] = button
                Tooltip(button, lambda: self._caption("hide_group") + " · " + self._caption("display_hint"))
            button.configure(bg=colors["WHITE"], fg=colors["INK"], activebackground=colors["ACCENT_SOFT"],
                             activeforeground=colors["INK"], highlightbackground=colors["WHITE"],
                             highlightcolor=colors["ACCENT"], font=(theme.BODY_FONT, -round(18*factor)))
            button._remchat_accessible_label = self._caption("hide_group")
            self.canvas.create_window(right-close_size/2-3*factor, cy, window=button,
                                      width=close_size, height=close_size)
        if len(groups) > 3:
            self.canvas.create_text(width/2, (84*3+10)*factor, text=f"+{len(groups)-3} · {self._words()[12]}",
                                    fill=colors["INK"], font=more_font)
        self.canvas._task_name_font = title_font  # Keep native font alive for the drawn text.
        self.canvas._task_center_fonts = center_fonts
        self.canvas._task_other_fonts = (below_font, more_font)

    def _ring_click(self, event):
        index = int(event.y / (84 * getattr(self, "_ring_factor", 1)))
        groups = getattr(self, "_visible_groups", [])
        self.show_tasks(groups[index]["id"] if 0 <= index < len(groups) else "")

    def show_tasks(self, group_id: str = ""):
        if self.closed:
            return
        if self.popup is not None and self.popup.winfo_exists():
            self._group_filter = group_id
            self._refresh_popup(self._ordered())
            self.popup.lift()
            return
        colors = theme.palette()
        self._group_filter = group_id
        self._popup_feedback = ""
        self.popup = tk.Toplevel(self.master)
        self.popup.title(self._words()[7])
        self.popup.configure(bg=colors["WHITE"])
        self.popup.attributes("-topmost", True)
        from .dpi import display_scale
        factor = display_scale(self.popup)
        anchor = self.anchor_provider()
        fallback = WindowRect(0, 0, self.master.winfo_screenwidth(), self.master.winfo_screenheight())
        work = native_module("windowing").work_area_at(anchor, fallback) if anchor else fallback
        width, height = max(240, min(round(900*factor), work.width-24)), max(260, min(round(640*factor), work.height-48))
        x, y = max(work.left, work.left+(work.width-width)//2), max(work.top, work.top+(work.height-height)//2)
        self.popup.geometry(f"{width}x{height}{x:+}{y:+}")
        self.popup.minsize(min(width, round(380*factor)), min(height, round(280*factor)))
        self.popup.grid_columnconfigure(0, weight=1)
        self.popup.grid_rowconfigure(2, weight=1)
        self.popup.protocol("WM_DELETE_WINDOW", self._close_popup)
        self.popup.bind("<Escape>", lambda _event: self._close_popup())
        self.top_notice = TopNotice(self.popup)
        self.notice = tk.Label(self.popup, text=self._caption("display_hint"), font=(theme.BODY_FONT, 9), anchor="w", justify="left", bg=colors["WHITE"], fg=colors["MUTED"], wraplength=550)
        self.notice.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 6))
        self.notice.bind("<Configure>", lambda event: self.notice.configure(wraplength=max(120,event.width)))
        self.filter_choice = ttk.Combobox(self.popup, state="readonly", font=(theme.BODY_FONT, 10))
        self.filter_choice.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        self.filter_choice.bind("<<ComboboxSelected>>", self._filter_changed)
        # Packing a fixed-height detail editor at the bottom used to unmap the
        # session tree at the allowed minimum size. A real adjustable split
        # gives both panes bounded space; no larger window minimum is required.
        self.panes = tk.PanedWindow(self.popup, orient="vertical", bg=colors["LINE"],
                                    bd=0, sashwidth=6, sashpad=0, sashrelief="flat")
        self.panes.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 8))
        self.list_host = tk.Frame(self.panes, bg=colors["WHITE"])
        self.selection_bar = tk.Frame(self.list_host, bg=colors["WHITE"])
        self.selection_bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.selection_bar.grid_columnconfigure(0, weight=1)
        self.selection_count = tk.Label(self.selection_bar, anchor="w", bg=colors["WHITE"],
                                        fg=colors["INK"], font=(theme.BODY_FONT, 10, "bold"))
        self.selection_count.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.selection_buttons = []
        for column, key, command in (
            (1, "all_items", lambda: self.checked_selection.select_all()),
            (2, "clear", lambda: self.checked_selection.clear()),
        ):
            button = tk.Button(self.selection_bar, text=self._caption(key), command=command, relief="flat",
                               bg=colors["WHITE"], fg=colors["INK"], padx=8, pady=4,
                               font=(theme.BODY_FONT, 9), takefocus=True, highlightthickness=1,
                               highlightbackground=colors["LINE"], highlightcolor=colors["ACCENT"])
            button.grid(row=0, column=column, sticky="ew", padx=(4, 0), pady=(0, 6))
            self.selection_buttons.append((key, button))
        self.operation_bar = tk.Frame(self.selection_bar, bg=colors["WHITE"])
        self.operation_bar.grid(row=1, column=0, columnspan=3, sticky="ew")
        for column, key in enumerate(("stop", "remove")):
            self.operation_bar.grid_columnconfigure(column, weight=1, uniform="task-operations")
            button = tk.Button(self.operation_bar, text=self._caption(key),
                               command=lambda action=key: self._operate_selected(action), relief="flat",
                               bg=colors["WHITE"], fg=colors["ERROR"] if key == "remove" else colors["INK"],
                               padx=8, pady=6, font=(theme.BODY_FONT, 9), takefocus=True,
                               highlightthickness=1, highlightbackground=colors["LINE"], highlightcolor=colors["ACCENT"])
            button.grid(row=0, column=column, sticky="ew", padx=(0, 4) if column == 0 else (4, 0))
            self.selection_buttons.append((key, button))
            Tooltip(button, lambda action=key: self._caption(action + "_hint"))
            if key == "stop":
                self.stop_button = button
            else:
                self.remove_button = button
        font = tkfont.Font(root=self.popup, family=theme.BODY_FONT, size=10)
        self._list_font = font
        style = ttk.Style(self.popup)
        style.configure("TaskRings.Treeview", font=font, rowheight=font.metrics("linespace")+16)
        self.task_list = ttk.Treeview(self.list_host, columns=("choose", "name", "state", "elapsed", "id", "workspace"), show="tree headings", selectmode="extended", style="TaskRings.Treeview")
        self.task_list.heading("#0", text="")
        self.task_list.column("#0", width=24, minwidth=24, stretch=False)
        self.task_list.heading("choose", text=self._caption("choose"))
        self.task_list.column("choose", width=48, minwidth=42, stretch=False, anchor="center")
        self.task_list.heading("name", text=self._words()[8])
        self.task_list.column("name", width=220, minwidth=160, stretch=True)
        for key, label, size in zip(("state", "elapsed", "id", "workspace"), (self._words()[9], self._words()[10], self._caption("id"), self._caption("workspace")), (190, 90, 220, 230)):
            self.task_list.heading(key, text=label)
            self.task_list.column(key, width=size, minwidth=70, stretch=True)
        scroll = ttk.Scrollbar(self.list_host, orient="vertical", command=self.task_list.yview)
        horizontal = ttk.Scrollbar(self.list_host, orient="horizontal", command=self.task_list.xview)
        self.task_list.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.list_host.grid_columnconfigure(0, weight=1)
        self.list_host.grid_rowconfigure(1, weight=1)
        self.task_list.grid(row=1, column=0, sticky="nsew")
        scroll.grid(row=1, column=1, sticky="ns")
        horizontal.grid(row=2, column=0, sticky="ew")
        self.checked_selection = CheckedTreeSelection(
            self.task_list, leaf_ids=lambda: tuple(getattr(self, "_popup_rows", {})),
            group_ids=lambda: self.task_list.get_children(),
            enabled=lambda: not (self._operation_busy or self._bind_busy))
        detail_host = tk.Frame(self.panes, bg=colors["WHITE"])
        self.detail_host = detail_host
        self.detail = tk.Text(detail_host, height=10, wrap="word", font=(theme.BODY_FONT, 9), bg=colors["WHITE"], fg=colors["INK"], bd=0,
                              highlightthickness=1, highlightbackground=colors["LINE"], highlightcolor=colors["ACCENT"], takefocus=True)
        detail_scroll = ttk.Scrollbar(detail_host, orient="vertical", command=self.detail.yview)
        self.detail.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side="right", fill="y")
        self.detail.pack(side="left", fill="both", expand=True)
        # Reserve two complete tree rows plus its heading and scrollbar. Tk
        # counts a clipped last row as visible, so see() alone is insufficient
        # when a small pane ends midway through the selected row.
        heading_font = tkfont.Font(root=self.popup, family=theme.BODY_FONT, size=9)
        list_minimum = (2 * (font.metrics("linespace") + 16)
                        + heading_font.metrics("linespace") + 12
                        + horizontal.winfo_reqheight() + self.selection_bar.winfo_reqheight() + 12)
        self.panes.add(self.list_host, minsize=list_minimum, stretch="always")
        self.panes.add(detail_host, minsize=heading_font.metrics("linespace")+4, stretch="always")
        self.task_list.bind("<<TreeviewSelect>>", self._selection)
        self.task_list.bind("<Configure>", lambda _event: self.task_list.after_idle(self._keep_selected_visible))
        actions = tk.Frame(self.popup, bg=colors["WHITE"])
        self.actions = actions
        actions.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 8))
        for column in range(3):
            actions.grid_columnconfigure(column, weight=1, uniform="task-actions")
        self.manage_button = tk.Button(actions, text=self._words()[11], command=self._manage,
                                      bg=colors["ACCENT_DEEP"], fg=colors["ON_ACCENT"], relief="flat", font=(theme.BODY_FONT, 9), padx=12, pady=6,
                                      highlightthickness=1, highlightbackground=colors["LINE"], highlightcolor=colors["ACCENT"], takefocus=True)
        self.manage_button.grid(row=0, column=2, sticky="ew", padx=(4, 0))
        self.bind_button = tk.Button(actions, text=self._caption("bind"), command=self._bind_selected,
                                     bg=colors["WHITE"], fg=colors["INK"], relief="flat", font=(theme.BODY_FONT, 9), padx=12, pady=6,
                                     highlightthickness=1, highlightbackground=colors["LINE"], highlightcolor=colors["ACCENT"], takefocus=True)
        self.bind_button.grid(row=0, column=0, columnspan=2, sticky="ew", padx=(0, 4))
        Tooltip(self.bind_button, lambda: self._caption("bind") + " · " + self._caption("bound"))
        self.display_buttons = []
        for row, column, key, command in (
            (1, 0, "hide_selected", self._hide_selected),
            (1, 1, "hide_all", self.hide_all),
            (1, 2, "restore_all", self.restore_all),
        ):
            button = tk.Button(actions, text=self._caption(key), command=command, bg=colors["WHITE"],
                               fg=colors["INK"], activebackground=colors["ACCENT_SOFT"],
                               activeforeground=colors["INK"], relief="flat", font=(theme.BODY_FONT, 9),
                               padx=12, pady=6, highlightthickness=1,
                               highlightbackground=colors["LINE"], highlightcolor=colors["ACCENT"], takefocus=True)
            button.grid(row=row, column=column, sticky="ew",
                        padx=(0, 4) if column == 0 else (4, 0) if column == 2 else 4, pady=(6, 0))
            self.display_buttons.append((key, button))
            Tooltip(button, lambda: self._caption("display_hint"))
        def wrap_actions(event):
            wrap = max(60, event.width // 3 - 32)
            for button in (self.bind_button, self.manage_button, *(item[1] for item in self.display_buttons)):
                button.configure(wraplength=wrap*2 if button is self.bind_button else wrap)
            # Preserve the tree's two complete rows even when translated action
            # text wraps. New recovery controls must not recreate the old
            # min-size layout bug where the entire session list disappeared.
            def reserve_minimum():
                self._reserve_after = None
                if self.popup is not None and self.popup.winfo_exists():
                    required = (list_minimum + heading_font.metrics("linespace")+4
                                + self.notice.winfo_reqheight() + self.filter_choice.winfo_reqheight()
                                + actions.winfo_reqheight() + 48)
                    self.popup.minsize(min(width, round(380*factor)), min(height, required))
            if self._reserve_after is None:
                self._reserve_after = self.master.after_idle(reserve_minimum)
        actions.bind("<Configure>", wrap_actions)
        self._refresh_popup(self._ordered())
        self.filter_choice.focus_set()

    def _keep_selected_visible(self):
        # Shrinking the split must not hide the session being inspected below
        # its tool group. Scroll only on resize, not on periodic refreshes or
        # while the user deliberately browses other rows.
        if self.popup is None or not self.popup.winfo_exists():
            return
        selected = self.task_list.selection()
        if selected:
            bounds = self.task_list.bbox(selected[0])
            if not bounds or bounds[1] + bounds[3] > self.task_list.winfo_height():
                self.task_list.see(selected[0])

    def _filter_changed(self, _event=None):
        index = self.filter_choice.current()
        self._group_filter = self._filter_ids[index] if 0 <= index < len(self._filter_ids) else ""
        self._refresh_popup(self._ordered())

    def _selection(self, _event=None):
        if self.popup is None:
            return
        self.checked_selection.sync()
        selected = self.checked_selection.selected_ids()
        count = len(selected)
        busy = self._operation_busy or self._bind_busy
        self.selection_count.configure(text=self._caption("selected").format(count=count))
        for key, button in self.selection_buttons:
            enabled = not busy and (bool(count) if key in {"clear", "remove"} else
                       any(self._popup_rows[item].get("monitoring", True) for item in selected) if key == "stop" else
                       bool(getattr(self, "_popup_rows", {})))
            button.configure(state="normal" if enabled else "disabled")
        row = getattr(self, "_popup_rows", {}).get(selected[0], {}) if count == 1 else {}
        if row:
            from .tool_sessions_panel import session_detail_text
            content = session_detail_text(row)
            if task_status(row)[0] == "waiting" and not row.get("external_session_id"):
                content = self._caption("waiting_hint") + "\n\n" + content
        elif count > 1:
            content = self._caption("many").format(count=count)
        else:
            content = self._words()[13]
        trace = getattr(self.manager, "project_trace", None)
        bindable = (row.get("external_session_id") and row.get("monitoring", True)
                    and getattr(trace, "enabled", False) and not busy)
        self.bind_button.configure(state="normal" if bindable else "disabled")
        self.bind_button.configure(text=self._caption("binding" if self._bind_busy else "bind"))
        for key, button in self.display_buttons:
            if key == "hide_selected":
                button.configure(state="normal" if self._selected_group() else "disabled")
        if getattr(self, "_detail_value", None) != content:
            selection_key = selected[0] if selected else ""
            scroll = self.detail.yview()[0] if selection_key == getattr(self, "_detail_selection", None) else 0
            self.detail.configure(state="normal")
            self.detail.delete("1.0", "end")
            self.detail.insert("1.0", content)
            self.detail.configure(state="disabled")
            self.detail.yview_moveto(scroll)
            self._detail_value = content
            self._detail_selection = selection_key

    def _refresh_popup(self, rows):
        if self.popup is None or not self.popup.winfo_exists():
            return
        colors = theme.palette()
        self.popup.configure(bg=colors["WHITE"])
        self.panes.configure(bg=colors["LINE"])
        self.list_host.configure(bg=colors["WHITE"])
        self.detail_host.configure(bg=colors["WHITE"])
        self.actions.configure(bg=colors["WHITE"])
        self.selection_bar.configure(bg=colors["WHITE"])
        self.operation_bar.configure(bg=colors["WHITE"])
        self.selection_count.configure(bg=colors["WHITE"], fg=colors["INK"])
        for key, button in self.selection_buttons:
            button.configure(text=self._caption(key), bg=colors["ACCENT_SOFT"] if key == "stop" else
                             colors["SIDEBAR"] if key == "remove" else colors["WHITE"],
                             fg=colors["ERROR"] if key == "remove" else colors["INK"],
                             activebackground=colors["ACCENT_SOFT"], activeforeground=colors["INK"],
                             disabledforeground=colors["MUTED"], highlightbackground=colors["LINE"],
                             highlightcolor=colors["ACCENT"])
        self.popup.title(self._words()[7])
        feedback = getattr(self, "_popup_feedback", "")
        self.notice.configure(text=(feedback+"\n" if feedback else "")+self._caption("display_hint"), bg=colors["WHITE"], fg=colors["MUTED"])
        self.detail.configure(bg=colors["WHITE"], fg=colors["INK"])
        self.manage_button.configure(text=self._words()[11], bg=colors["ACCENT_DEEP"], fg=colors["ON_ACCENT"])
        self.bind_button.configure(bg=colors["WHITE"], fg=colors["INK"], activebackground=colors["ACCENT_SOFT"],
                                   highlightbackground=colors["LINE"], highlightcolor=colors["ACCENT"])
        for key, button in self.display_buttons:
            button.configure(text=self._caption(key), bg=colors["WHITE"], fg=colors["INK"],
                             activebackground=colors["ACCENT_SOFT"], activeforeground=colors["INK"],
                             highlightbackground=colors["LINE"], highlightcolor=colors["ACCENT"])
        self.task_list.heading("choose", text=self._caption("choose"))
        self.task_list.heading("name", text=self._words()[8])
        for key, label in zip(("state", "elapsed", "id", "workspace"), (self._words()[9], self._words()[10], self._caption("id"), self._caption("workspace"))):
            self.task_list.heading(key, text=label)
        style = ttk.Style(self.popup)
        style.configure("TaskRings.Treeview", font=self._list_font, rowheight=self._list_font.metrics("linespace")+16,
                        background=colors["WHITE"], fieldbackground=colors["WHITE"], foreground=colors["INK"])
        style.configure("TaskRings.Treeview.Heading", background=colors["SIDEBAR"], foreground=colors["INK"], font=(theme.BODY_FONT, 9, "bold"))
        style.map("TaskRings.Treeview", background=[("selected", colors["ACCENT_SOFT"])], foreground=[("selected", colors["INK"])])
        groups = task_groups(rows)
        self._filter_ids = [""] + [group["id"] for group in groups]
        if self._group_filter not in self._filter_ids:
            self._group_filter = ""
        def group_caption(group):
            count = (f"{group['record_started']}/{group['total']}" if group["record_connected"] and group["record_known"] else
                     self._connected_label() if group["record_connected"] else
                     f"—/{group['total']}" if group["record"] else f"{group['running']}/{group['total']}")
            return f"{group['name']} · {count}" + (f" · {self._sync_label()}" if group["record_connected"] else "")
        self.filter_choice.configure(values=[self._caption("all")] + [group_caption(group) for group in groups])
        self.filter_choice.current(self._filter_ids.index(self._group_filter))
        shown = [group for group in groups if not self._group_filter or group["id"] == self._group_filter]
        self._popup_rows = {}
        existing_groups = set(self.task_list.get_children())
        for index, group in enumerate(shown):
            group_id = group["id"]
            count = ((f"{group['record_started']}/{group['total']} · {self._sync_label()}" if group["record_known"] else
                      f"{self._connected_label()} · {group['total']} · {self._sync_label()}") if group["record_connected"] else
                     f"—/{group['total']} · {self._label('disconnected')}" if group["record"] else
                     f"{group['running']}/{group['total']} · {self._label(group['state'])}")
            if self.task_list.exists(group_id):
                self.task_list.item(group_id, values=("", group["name"], count, "—", "", ""))
                existing_groups.discard(group_id)
            else:
                self.task_list.insert("", "end", iid=group_id, values=("", group["name"], count, "—", "", ""), open=True)
            self.task_list.move(group_id, "", index)
            existing_sessions = set(self.task_list.get_children(group_id))
            for session_index, row in enumerate(group["rows"]):
                identifier = "session:" + str(row["id"])
                self._popup_rows[identifier] = row
                state, elapsed = task_status(row)
                detail = row.get("session_detail")
                detail = detail if isinstance(detail, dict) else {}
                values = ("", row.get("name", ""), self._row_label(row, state), elapsed_text(elapsed), row.get("external_session_id") or row["id"], row.get("workspace") or detail.get("workspace") or "—")
                if self.task_list.exists(identifier):
                    self.task_list.item(identifier, values=values)
                    existing_sessions.discard(identifier)
                else:
                    self.task_list.insert(group_id, "end", iid=identifier, values=values)
                self.task_list.move(identifier, group_id, session_index)
            for identifier in existing_sessions:
                self.task_list.delete(identifier)
        for identifier in existing_groups:
            self.task_list.delete(identifier)
        self._selection()

    def _operate_selected(self, action):
        if self.popup is None or self._operation_busy or self._bind_busy:
            return
        selected = self.checked_selection.selected_ids()
        rows = [self._popup_rows[item] for item in selected]
        if action == "stop":
            rows = [row for row in rows if row.get("monitoring", True)]
        if not rows:
            return
        if action == "remove":
            names = "\n".join(str(row.get("name") or row["id"])[:120] for row in rows[:5])
            if len(rows) > 5:
                names += f"\n… +{len(rows)-5}"
            if not messagebox.askyesno(self._caption("remove"),
                    self._caption("confirm").format(count=len(rows), name=names),
                    parent=self.popup, icon="warning", default="no"):
                return
        identifiers = [str(row["id"]) for row in rows]
        self._operation_busy = True
        self._popup_feedback = self._caption("working")
        self._selection()
        language = self.language()
        words = ACTION_WORDS.get(language, ACTION_WORDS["zh-Hans"])
        def operate():
            try:
                changed = (self.manager.cancel_monitoring_many(identifiers) if action == "stop"
                           else self.manager.remove_tasks(identifiers))
                result = {"message": words["stopped" if action == "stop" else "removed"].format(count=len(changed))}
            except Exception as error:
                result = {"message": words["failed"].format(error=error), "error": True}
            if not self.closed:
                self.results.put(result)
        threading.Thread(target=operate, name="remchat-observation-management", daemon=True).start()

    def _bind_selected(self):
        if self.popup is None or self._bind_busy or self._operation_busy:
            return
        selected = self.checked_selection.selected_ids()
        row = getattr(self, "_popup_rows", {}).get(selected[0]) if len(selected) == 1 else None
        if not row or not row.get("external_session_id") or not row.get("monitoring", True):
            return
        trace = getattr(self.manager, "project_trace", None)
        if not getattr(trace, "enabled", False):
            self.notice.configure(text=self._caption("bind_off"))
            return
        task_id, session_id = str(row["id"]), str(row["external_session_id"])
        adapter = str(row.get("tool_id") or "generic")
        self._bind_busy = True
        self._selection()
        self._popup_feedback = {"zh-Hans": "正在聚焦到项目视图；其他会话继续监工。", "zh-Hant": "正在聚焦到專案視圖；其他會話繼續監工。", "en": "Focusing project view; other sessions stay monitored.", "ja": "プロジェクト表示を変更中。他のセッションの監視は継続します。"}.get(self.language(), "正在聚焦到项目视图；其他会话继续监工。")
        self.notice.configure(text=self._popup_feedback)
        success_message = self._caption("bound")
        def bind():
            try:
                updated = self.manager.bind_task_project(task_id, session_id, adapter=adapter)
                self._enqueue({"type": "task", "task": updated})
                message = success_message
                failed = False
            except Exception as error:
                message = str(error)
                failed = True
            if not self.closed:
                self.results.put({"message": message, "error": failed})
        threading.Thread(target=bind, name="remchat-session-project-link", daemon=True).start()

    def _manage(self):
        self._close_popup()
        self.on_manage()

    def _close_popup(self):
        if self._reserve_after is not None:
            self.master.after_cancel(self._reserve_after)
            self._reserve_after = None
        if self.popup is not None:
            self.top_notice.close()
            self.popup.destroy()
            self.popup = None
        self._detail_value = None
        self._detail_selection = None

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.unsubscribe()
        self.sync_tooltip.close()
        try:
            self.master.after_cancel(self._after)
            self._close_popup()
            if self._context_menu is not None:
                self._context_menu.destroy()
            self.window.destroy()
        except tk.TclError:
            pass
