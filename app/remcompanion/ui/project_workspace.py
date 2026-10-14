"""One project feature, with observation and connection management kept separate."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .integrations_panel import IntegrationPanel
from .theme import APP_BG, WHITE


class ProjectWorkspace(tk.Frame):
    """Reuse the window's manager; switching views never starts/stops observation."""

    def __init__(self, parent: tk.Misc, owner) -> None:
        super().__init__(parent, bg=APP_BG)
        self.owner = owner
        self.active = False
        self.closed = False
        self.process_panel = None
        self.tabs = ttk.Notebook(self, takefocus=True)
        self.tabs.pack(fill="both", expand=True)
        self.observation_page = tk.Frame(self.tabs, bg=WHITE)
        self.management_page = tk.Frame(self.tabs, bg=APP_BG)
        self.tabs.add(self.observation_page, text="观察展示")
        self.tabs.add(self.management_page, text="接入与会话管理")

        # External management is a tasks-only view of the shared manager.
        # RemChat's resource forms and notification lifetime belong to the owner.
        self.integration_panel = IntegrationPanel(
            self.management_page, owner.service.config.root,
            manager=owner.integration_manager, embedded=True, scope="external",
        )
        self.integration_panel.pack(fill="both", expand=True)
        owner.external_integration_panel = self.integration_panel
        self.tabs.bind("<<NotebookTabChanged>>", self._tab_changed, add=True)
        self.bind("<Destroy>", self._destroyed, add=True)

    def _ensure_process(self):
        if self.process_panel is None:
            from .session_process_panel import SessionProcessPanel

            self.process_panel = SessionProcessPanel(self.observation_page, self.owner)
            self.process_panel.pack(fill="both", expand=True)
            self.owner.project_process_panel = self.process_panel
            self.owner.plaza_body.process_panel = self.process_panel
        return self.process_panel

    def show(self, mode: str = "observe", *, section: str = "tasks") -> None:
        if self.closed:
            return
        self.active = True
        if mode == "manage":
            if self.process_panel is not None:
                self.process_panel.pause()
            self.integration_panel._session_preview_active = True
            self.tabs.select(self.management_page)
            self.integration_panel.show_section(section)
            self.integration_panel.refresh()
            if section == "tasks":
                self.integration_panel.preview_sessions(self.process_panel._tool_id if self.process_panel is not None else None)
        else:
            self.integration_panel._session_preview_active = False
            self.integration_panel._stop_auto_tool_preview()
            self.tabs.select(self.observation_page)
            self._ensure_process().refresh()

    def _tab_changed(self, _event=None) -> None:
        if self.closed or not self.active or not self.winfo_ismapped():
            return
        if self.tabs.select() == str(self.observation_page):
            self.integration_panel._session_preview_active = False
            self.integration_panel._stop_auto_tool_preview()
            self._ensure_process().refresh()
        else:
            if self.process_panel is not None:
                self.process_panel.pause()
            self.integration_panel._session_preview_active = True
            self.integration_panel.refresh()
            if self.integration_panel.tabs.select() == str(self.integration_panel.pages["tasks"]):
                self.integration_panel.preview_sessions(self.process_panel._tool_id if self.process_panel is not None else None)

    def pause(self) -> None:
        self.active = False
        self.integration_panel._session_preview_active = False
        self.integration_panel._stop_auto_tool_preview()
        if self.process_panel is not None:
            self.process_panel.pause()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.pause()
        if self.process_panel is not None:
            self.process_panel.close()
        self.integration_panel.close()

    def _destroyed(self, event) -> None:
        if event.widget is self:
            self.close()
