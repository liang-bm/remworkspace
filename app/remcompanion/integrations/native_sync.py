"""Application-lifetime local native synchronization, independent of Tk/LLMs.

One worker per connection keeps slow/broken archives isolated. Catalog reads
and bounded snapshots use the existing read-only adapters; no monitor task is
created and no external process/model turn is started by this service.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import threading
import time


POLL_SECONDS = 2.0
CATALOG_SECONDS = 5.0
SNAPSHOTS_PER_PASS = 8
MAX_SNAPSHOTS = 256


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


class NativeSynchronization:
    def __init__(self, monitor):
        self.monitor = monitor
        self.manager = monitor.manager
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.stop = threading.Event()
        self.bootstrap_thread = None
        self.workers = {}
        self.retired_workers = set()
        self.wake = {}
        self.catalogs = {}
        self.states = {}
        self.snapshots = OrderedDict()
        self.markers = {}
        self.fingerprints = {}
        self.watches = {}
        self.requested = set()
        self.last_published = {}

    def start(self):
        with self.lock:
            if self.bootstrap_thread is not None or self.stop.is_set():
                return
            self.bootstrap_thread = threading.Thread(target=self._bootstrap, name="remchat-native-startup", daemon=True)
            self.bootstrap_thread.start()

    def _bootstrap(self):
        # Retry missing/temporarily locked directories too: an initially empty
        # installation must discover files created after RemChat was opened.
        while not self.stop.is_set():
            try:
                saved = self.monitor.store.read()["tool_connections"]
                entries = [row for row in self.monitor.catalog() if row.get("supported")]
                for entry in entries:
                    if self.stop.is_set():
                        return
                    tool = entry["id"]
                    choices = [row for row in saved if row.get("tool_id") == tool]
                    if not choices:
                        choices = [{"tool_id": tool, "endpoint": "", "id": "default:" + tool}]
                    for row in choices:
                        endpoint = row.get("endpoint", "")
                        if "autoconnect" not in row and row.get("state") == "disconnected":
                            # Earlier releases persisted this state only on an
                            # explicit Disconnect click (startup was a view-only
                            # disconnected label). Preserve that older intent.
                            with self.monitor.lock:
                                # The saved startup list can be stale after an
                                # explicit Connect/Disconnect. Migrate only the
                                # current legacy row, never overwrite that click.
                                current = self.monitor.store.get_item("tool_connections", row["id"])
                                if "autoconnect" not in current and current.get("state") == "disconnected":
                                    current["autoconnect"] = False
                                    self.monitor.store.update_item("tool_connections", current)
                                row = current
                        if row.get("autoconnect") is False:
                            self._state(row["id"], tool, state="disconnected", session_count=0,
                                        location=row.get("location", endpoint), last_error="已手动断开；后台不会自动重连。")
                            continue
                        # A proxy is an explicit advanced operation. Startup
                        # reconnects local archives only, never a service process.
                        if endpoint.startswith("native") or endpoint.casefold().endswith(".sock"):
                            continue
                        with self.monitor.lock:
                            if row.get("id") in self.monitor.sources:
                                continue
                        location = endpoint if endpoint not in {"", "local-records"} else entry.get("default_location", "")
                        if not location or not Path(location.strip('"')).exists():
                            self._state(row.get("id", "default:" + tool), tool, state="unavailable", location=location,
                                        last_error="本机默认/指定存档尚不存在；后台继续等待，不会启动外部工具。")
                            continue
                        try:
                            self.monitor.connect(tool, endpoint, automatic=True)
                            with self.lock:
                                self.states.pop("default:" + tool, None)
                        except Exception as error:
                            self._state(row.get("id", "default:" + tool), tool, state="error", location=location,
                                        last_error=self.monitor.public_error(error))
            except Exception:
                # A corrupt settings file must not spin or kill this reader.
                pass
            self.stop.wait(CATALOG_SECONDS)

    def attach(self, connection, source):
        identifier = connection["id"]
        with self.lock:
            if self.stop.is_set():
                return
            self.catalogs.pop(identifier, None)
            for key in [key for key in self.snapshots if key[0] == identifier]:
                self.snapshots.pop(key, None)
                self.markers.pop(key, None)
                self.fingerprints.pop(key, None)
            signal = threading.Event()
            self.wake[identifier] = signal
            thread = threading.Thread(target=self._read_connection, args=(connection, source, signal),
                                      name="remchat-native-" + connection["tool_id"], daemon=True)
            previous = self.workers.get(identifier)
            self.retired_workers = {worker for worker in self.retired_workers if worker.is_alive()}
            if previous and previous.is_alive():
                self.retired_workers.add(previous)
            self.workers[identifier] = thread
            self._state(identifier, connection["tool_id"], state="reading", location=connection.get("location", ""), last_error="")
            thread.start()

    def _current(self, identifier, source):
        with self.monitor.lock:
            return not self.monitor.closed and self.monitor.sources.get(identifier) is source

    def _state(self, identifier, tool, **fields):
        with self.lock:
            old = self.states.get(identifier, {})
            row = {"connection_id": identifier, "tool_id": tool, "state": "reading", "revision": 0,
                   "catalog_revision": 0, "snapshot_revision": 0, "last_success": None,
                   "last_error": "", "location": "", "session_count": 0, **old, **fields}
            self.states[identifier] = row
            self.condition.notify_all()
            changed = any(row.get(key) != old.get(key) for key in ("state", "last_error", "revision", "session_count"))
            heartbeat = bool(row.get("last_success")) and time.monotonic() - self.last_published.get(identifier, 0) >= CATALOG_SECONDS
            if changed or heartbeat:
                self.last_published[identifier] = time.monotonic()
        if changed or heartbeat:
            self.manager._publish({"type": "native_sync_state", **deepcopy(row)})

    def status(self, tool=""):
        with self.lock:
            rows = [deepcopy(row) for row in self.states.values() if not tool or row["tool_id"] == tool]
        good = [row for row in rows if row["state"] == "synced"]
        current = good or rows
        latest = max(current, key=lambda row: row.get("last_success") or 0, default={})
        return {"tool_id": tool, "state": latest.get("state", "reading"),
                "revision": sum(row["revision"] for row in rows),
                "last_success": max((row.get("last_success") or 0 for row in rows), default=0) or None,
                "last_error": latest.get("last_error", ""), "location": latest.get("location", ""),
                "session_count": sum(row["session_count"] for row in rows), "connections": rows}

    def catalog(self, identifier, workspace="", *, wait=False):
        from ..platforms.path_conversion import workspace_path
        from ..project_trace.recorder import _redact
        if not isinstance(workspace, str) or (workspace and not workspace_path(workspace)):
            raise ValueError("工作空间路径无效。")
        with self.condition:
            if wait and identifier not in self.catalogs:
                deadline = time.monotonic() + 4
                while identifier not in self.catalogs and not self.stop.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or identifier not in self.monitor.sources:
                        break
                    self.condition.wait(min(.2, remaining))
            rows = deepcopy(self.catalogs.get(identifier, []))
        if workspace:
            from ..platforms.windows.tool_sessions import _workspace_key
            rows = [row for row in rows if workspace_path(row.get("workspace"))
                    and _workspace_key(workspace_path(row.get("workspace"))) == _workspace_key(workspace)]
        # The cache is private and keeps exact identities for filtering. Every
        # returned catalog remains a public, redacted display projection.
        for row in rows:
            row["workspace"] = _redact(workspace_path(row.get("workspace")))[:1000]
        return rows

    def _workspace_identity(self, identifier, session):
        """Internal scope matching only; not copied into events/public detail."""
        from ..platforms.path_conversion import workspace_path
        with self.lock:
            cached = self.snapshots.get((identifier, session))
            if cached is not None and isinstance(cached.get("data"), dict):
                return workspace_path(cached["data"].get("workspace")) or None
            row = next((row for row in self.catalogs.get(identifier, []) if row.get("id") == session), None)
            return (workspace_path(row.get("workspace")) or None) if row is not None else None

    def request_catalog(self, identifier):
        with self.lock:
            signal = self.wake.get(identifier)
        if signal:
            signal.set()

    def watch(self, identifier, session, owner):
        with self.lock:
            # One preview per consumer; changing a dropdown releases its old
            # selection. This does not create or resume a tracked task.
            changed = self.watches.get(str(owner)) != (identifier, session)
            self.watches[str(owner)] = (identifier, session)
            if changed or (identifier, session) not in self.snapshots:
                self.requested.add((identifier, session))
            signal = self.wake.get(identifier) if changed else None
        if signal:
            signal.set()

    def unwatch(self, owner):
        with self.lock:
            self.watches.pop(str(owner), None)

    def snapshot(self, identifier, session, timeout=4.0):
        key = (identifier, session)
        deadline = time.monotonic() + timeout
        with self.condition:
            if key in self.snapshots:
                self.snapshots.move_to_end(key)
                return deepcopy(self.snapshots[key]["data"])
            self.requested.add(key)
            signal = self.wake.get(identifier)
            if signal:
                signal.set()
            while key not in self.snapshots and not self.stop.is_set():
                if identifier not in self.monitor.sources:
                    raise ValueError("原连接已断开；不会自动恢复手动断开的工具。")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("本地公开记录仍在后台读取；稍后将自动更新。")
                self.condition.wait(min(remaining, .2))
            if key not in self.snapshots:
                raise RuntimeError("本地同步已关闭。")
            return deepcopy(self.snapshots[key]["data"])

    def _marker(self, source, row):
        marker = getattr(source, "sync_marker", None)
        if callable(marker):
            return marker(row["id"])
        # Markerless advanced sources are reread only for active preview/tasks
        # and the bounded initial pass, not via an unbounded polling fan-out.
        return (row.get("updated_at"), row.get("last_activity"), row.get("name"), row.get("metadata_only"))

    def _read_connection(self, connection, source, signal):
        identifier, tool = connection["id"], connection["tool_id"]
        next_catalog = 0.0
        rows = []
        while not self.stop.is_set() and self._current(identifier, source):
            try:
                if time.monotonic() >= next_catalog or signal.is_set():
                    signal.clear()
                    rows = self.monitor._discover_source(source, private_paths=True)
                    if not self._current(identifier, source):
                        return
                    digest = _fingerprint(rows)
                    with self.monitor.lock:
                        if not self._current(identifier, source):
                            return
                        with self.lock:
                            changed = digest != self.fingerprints.get((identifier, "catalog"))
                            self.fingerprints[(identifier, "catalog")] = digest
                            self.catalogs[identifier] = deepcopy(rows)
                            old = self.states.get(identifier, {})
                            revision = old.get("revision", 0) + int(changed)
                            catalog_revision = old.get("catalog_revision", 0) + int(changed)
                        self._state(identifier, tool, state="synced", last_success=time.time(), last_error="",
                                    revision=revision, catalog_revision=catalog_revision, session_count=len(rows))
                        if changed:
                            self.manager._publish({"type": "tool_catalog_changed", **self.states[identifier]})
                    next_catalog = time.monotonic() + CATALOG_SECONDS
                tasks = [row for row in self.monitor.store.read()["tasks"]
                         if row.get("connection_id") == identifier and row.get("monitoring", True)]
                with self.lock:
                    priority = {session for conn, session in self.watches.values() if conn == identifier}
                    priority.update(session for conn, session in self.requested if conn == identifier)
                priority.update(row["external_session_id"] for row in tasks)
                if rows:
                    # Keep the current main conversation warm even while every
                    # UI page is hidden. Source timestamps, never a running guess.
                    def recency(row):
                        return max((value for key in ("last_activity", "updated_at", "created_at")
                                    if type(value := row.get(key)) in {int, float} and math.isfinite(value)), default=0)
                    priority.add(max(rows, key=recency)["id"])
                by_id = {row["id"]: row for row in rows}
                for task in tasks:
                    if task["external_session_id"] not in by_id and self._current(identifier, source):
                        self.monitor._lost_task(task["id"], "原生目录暂未列出此会话；保留上次观察，当前状态未知，后台继续重试。")
                selected = [by_id[session] for session in priority if session in by_id]
                # Every main conversation eventually gets a small public
                # snapshot. Initial reads are bounded per pass, latest first.
                for row in rows:
                    if row["id"] in priority:
                        continue
                    key = (identifier, row["id"])
                    try:
                        marker = self._marker(source, row)
                    except (OSError, ValueError):
                        marker = ("temporarily_unreadable", row.get("id"))
                    if key not in self.markers or marker != self.markers[key]:
                        selected.append(row)
                    if len(selected) >= max(SNAPSHOTS_PER_PASS, len(priority)):
                        break
                errors = []
                for row in selected:
                    if self.stop.is_set() or not self._current(identifier, source):
                        return
                    try:
                        self._snapshot_one(connection, source, row, tasks, priority)
                    except Exception as error:
                        # One moved/partial session must not starve other chats
                        # on the same tool, much less another tool's worker.
                        errors.append(self.monitor.public_error(error))
                        for task in tasks:
                            if task["external_session_id"] == row["id"] and self._current(identifier, source):
                                self.monitor._lost_task(task["id"], "会话同步暂不可读：" + errors[-1])
                with self.monitor.lock:
                    if not self._current(identifier, source):
                        return
                    if errors:
                        self._state(identifier, tool, state="error", last_error=errors[0])
                    else:
                        self._state(identifier, tool, state="synced", last_success=time.time(), last_error="")
            except Exception as error:
                with self.monitor.lock:
                    if not self._current(identifier, source):
                        return
                    self._state(identifier, tool, state="error", last_error=self.monitor.public_error(error))
                # Keep the last successful catalog/snapshot during partial
                # writes and temporary errors; never report stale data as new.
            signal.wait(POLL_SECONDS)

    def _snapshot_one(self, connection, source, row, tasks, priority):
        identifier, tool = connection["id"], connection["tool_id"]
        key = (identifier, row["id"])
        marker = self._marker(source, row)
        with self.lock:
            requested = key in self.requested
            cached = self.snapshots.get(key)
        if cached and self.markers.get(key) == marker and not requested and row["id"] not in priority:
            return
        raw = source.snapshot(row["id"])
        if not isinstance(raw, dict) or raw.get("id") != row["id"]:
            raise ValueError("本地公开记录身份不匹配。")
        if not self._current(identifier, source):
            return
        digest = _fingerprint(raw)
        self.monitor._record_native_usage(connection, raw)
        # Serialise publication with Disconnect, not disk parsing. A completed
        # in-flight read cannot republish a detached connection or its cache.
        with self.monitor.lock:
            if not self._current(identifier, source):
                return
            with self.condition:
                changed = self.fingerprints.get(key) != digest
                self.fingerprints[key] = digest
                self.markers[key] = marker
                self.snapshots[key] = {"data": deepcopy(raw), "read_at": time.time()}
                self.snapshots.move_to_end(key)
                self.requested.discard(key)
                while len(self.snapshots) > MAX_SNAPSHOTS:
                    self.snapshots.popitem(last=False)
                if changed:
                    state = self.states[identifier]
                    state["revision"] += 1
                    state["snapshot_revision"] += 1
                self.condition.notify_all()
            if changed:
                self.manager._publish({"type": "tool_snapshot_changed", "session_id": row["id"], **self.states[identifier]})
        for task in tasks:
            if task["external_session_id"] == row["id"]:
                self.monitor._apply_current_task(identifier, source, task["id"], raw)

    def detach(self, identifier, tool, location=""):
        with self.condition:
            signal = self.wake.pop(identifier, None)
            if signal:
                signal.set()
            self.catalogs.pop(identifier, None)
            for cache in (self.snapshots, self.markers, self.fingerprints):
                for key in [key for key in cache if key[0] == identifier]:
                    cache.pop(key, None)
            worker = self.workers.pop(identifier, None)
            if worker and worker.is_alive():
                self.retired_workers.add(worker)
            self.watches = {key: value for key, value in self.watches.items() if value[0] != identifier}
            self.requested = {key for key in self.requested if key[0] != identifier}
            self.condition.notify_all()
        self._state(identifier, tool, state="disconnected", session_count=0, location=location,
                    last_error="已手动断开；后台不会自动重连。")

    def close(self):
        self.stop.set()
        with self.condition:
            for signal in self.wake.values():
                signal.set()
            self.condition.notify_all()
            workers = [*self.workers.values(), *self.retired_workers]
            if self.bootstrap_thread:
                workers.append(self.bootstrap_thread)
        for worker in workers:
            if worker is not threading.current_thread():
                worker.join(timeout=2)
        with self.condition:
            self.catalogs.clear()
            self.snapshots.clear()
            self.markers.clear()
            self.fingerprints.clear()
            self.watches.clear()
            self.requested.clear()
            self.wake.clear()
            self.workers.clear()
            self.retired_workers.clear()
            self.states.clear()
            self.last_published.clear()
