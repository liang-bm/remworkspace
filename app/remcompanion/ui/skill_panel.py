"""Read-only skill browsing; importing never executes skill scripts."""

from __future__ import annotations

import tkinter as tk
from .localized_dialogs import filedialog, messagebox

from .resource_rows import PermissionSwitch
from .theme import WHITE, INK, MUTED, BODY_FONT

FONT = (BODY_FONT, 10)


class SkillPanelMixin:
    """Host: IntegrationPanel helpers and manager.skills public API."""
    def _build_skills(self, page):
        self.skill_overview = tk.Frame(page, bg=WHITE)
        self.skill_overview.pack(fill="both", expand=True, padx=20, pady=18)
        left = self.skill_overview
        header = tk.Frame(left, bg=WHITE)
        header.pack(fill="x", pady=(0, 12))
        tk.Label(header, text="已导入的技能", bg=WHITE, fg=INK,
                 font=(BODY_FONT, 14, "bold")).pack(side="left")
        self.skill_import_button = self._button(header, "导入技能文件夹", self._import_skill, primary=True)
        self.skill_import_button.pack_configure(side="right")
        self._label(left, "先阅读，再允许雷姆参考。导入不执行脚本；危险词检查不等于安全审计。")
        self.skill_list = self._resource_list(left, edit=self._select_skill, toggle=self._toggle_skill_row)
        self.skill_list.bind("<<ListboxSelect>>", self._select_skill)
        self.skill_detail = tk.Frame(page, bg=WHITE)
        inner = tk.Frame(self.skill_detail, bg=WHITE)
        inner.pack(fill="both", expand=True, padx=20, pady=18)
        header = tk.Frame(inner, bg=WHITE)
        header.pack(fill="x", pady=(0, 8))
        self.skill_back_button = self._button(header, "返回技能列表", self._back_skill)
        right = self._scroll_detail(inner)
        self.skill_detail_body = right
        self.skill_name = tk.StringVar(self, "还没有选择技能")
        self.skill_description = tk.StringVar(self, "从左侧选择技能，或导入含 SKILL.md 的文件夹。")
        self.skill_location = tk.StringVar(self)
        for variable, size, bold in ((self.skill_name, 15, True), (self.skill_description, 10, False)):
            label = tk.Label(right, textvariable=variable, bg=WHITE, fg=INK if bold else MUTED,
                font=(BODY_FONT, size, "bold" if bold else "normal"), anchor="w", justify="left")
            label._remchat_locale_exclude = True  # Names, descriptions and paths are user data.
            label.pack(fill="x", pady=(8, 4))
            label.bind("<Configure>", lambda event, target=label: target.configure(wraplength=max(100, event.width - 4)))
        permission = self._detail_section(right, "参考权限")
        self.skill_check = PermissionSwitch(permission, text="让雷姆参考此技能", variable=self.skill_enabled,
                                             command=self._toggle_skill)
        self.skill_check.pack(fill="x", pady=(0, 6))
        self._label(permission, "只把说明作为聊天参考，不启动脚本、不授权操作。先阅读下方正文，再明确允许。")
        self.skill_state = tk.StringVar(self, "未选择 · 不能启用")
        label = tk.Label(permission, textvariable=self.skill_state, bg=WHITE, fg=MUTED, font=FONT,
                         anchor="w", justify="left")
        label.pack(fill="x", pady=(0, 8))
        label.bind("<Configure>", lambda e: label.configure(wraplength=max(100, e.width - 4)))
        content = self._detail_section(right, "技能说明")
        self._label(content, "只读，可选择文字复制。导入和阅读不会执行技能中的脚本。")
        self.skill_output = self._text(content, 16)
        self.skill_technical_button = self._button(right, "安全检查与文件信息  ▸", self._toggle_skill_technical)
        self.skill_technical_button.pack_configure(side="top", anchor="w", pady=(18, 8))
        self.skill_technical = tk.Frame(right, bg=WHITE)
        self.skill_checks = tk.StringVar(self, "未读取技能")
        self.skill_metadata = self._text(self.skill_technical, 7, code=True)
        tools = tk.Frame(self.skill_technical, bg=WHITE)
        tools.pack(fill="x", pady=(8, 0))
        folder = self._button(tools, "打开技能库", lambda: self._open_path(self.manager.skills.directory))
        self.skill_remove_button = self._button(tools, "移除技能", self._remove_skill)
        self._flow_buttons(tools, (folder, self.skill_remove_button))
        self._skill_loaded_id = ""
        self._skill_reading_id = ""
        self._reset_skill()
        self._skill_controls()

    def _refresh_skill_metadata(self):
        self._set_text(self.skill_metadata, self.skill_location.get() + "\n\n" + self.skill_checks.get(), readonly=True)

    def _toggle_skill_technical(self):
        if self.skill_technical.winfo_manager():
            self.skill_technical.pack_forget()
            self.skill_technical_button.configure(text="安全检查与文件信息  ▸")
        else:
            self.skill_technical.pack(fill="x", pady=(0, 12))
            self.skill_technical_button.configure(text="安全检查与文件信息  ▾")

    def _back_skill(self):
        if self.busy:
            return
        self.skill_detail.pack_forget()
        self.skill_overview.pack(fill="both", expand=True, padx=20, pady=18)

    def _show_skill_detail(self):
        self.skill_overview.pack_forget()
        self.skill_detail.pack(fill="both", expand=True)
        self._detail_top(self.skill_detail_body)

    def _toggle_skill_row(self, row, desired):
        if self.busy:
            return
        if desired:
            self._select_skill()
            self.status.set("先阅读技能说明，再勾选“让雷姆参考此技能”。")
            return
        def complete(_result):
            if self._skill_loaded_id == row["id"]:
                self.skill_enabled.set(False)
                self.skill_state.set("已停用 · 读完后可允许参考")
            self.status.set("技能参考状态已更新，没有执行脚本。")
        self._work("正在保存技能参考状态…", lambda: self.manager.skills.toggle(row["id"], False),
                   complete, on_error=self._skill_error)

    def _reset_skill(self):
        self._skill_loaded_id = ""
        self._skill_reading_id = ""
        self.skill_enabled.set(False)
        self.skill_name.set("还没有选择技能")
        self.skill_description.set("从左侧选择技能，或导入含 SKILL.md 的文件夹。")
        self.skill_location.set("")
        self.skill_checks.set("未读取技能")
        self._refresh_skill_metadata()
        self.skill_state.set("未选择 · 不能启用")
        self._set_text(self.skill_output, "导入会保留 scripts / references 等目录，默认不让雷姆参考。", readonly=True)

    def _skill_controls(self):
        self.skill_list.set_busy(self.busy)
        self.skill_back_button.configure(state="disabled" if self.busy else "normal")
        selected = self._selection(self.skill_list, self.skill_rows)
        ready = selected and selected["id"] == self._skill_loaded_id
        self.skill_import_button.configure(state="disabled" if self.busy else "normal")
        self.skill_remove_button.configure(state="normal" if selected and not self.busy else "disabled")
        self.skill_check.configure(state="normal" if ready and not self.busy else "disabled")

    def _import_skill(self):
        if self.busy:
            return
        path = filedialog.askdirectory(parent=self, title="选择包含 SKILL.md 的技能目录")
        if path:
            from ..integrations.skills import describe_skill
            from pathlib import Path
            self._work("正在检查技能说明…", lambda: describe_skill(Path(path)),
                       lambda row: self._confirm_skill_import(path, row), on_error=self._skill_error)

    def _confirm_skill_import(self, path, row):
        if any(item["id"] == row["id"] for item in self.manager.skills.list()):
            if not messagebox.askyesno("替换已导入的技能",
                f"技能 {row['name']} 已存在。导入将替换它并停用；旧版会保留在 backups 中。是否继续？",
                parent=self, default="no"):
                self.status.set("已取消导入，原技能未改动。")
                return
        self._work("正在导入技能…", lambda: self.manager.skills.import_skill(path),
                   self._skill_imported, on_error=self._skill_error)

    def _skill_imported(self, result):
        self.skill_list._search_query.set("")
        self.refresh()
        for index, row in enumerate(self.skill_rows):
            if row["id"] == result["id"]:
                self.skill_list.selection_clear(0, "end")
                self.skill_list.selection_set(index)
                self.skill_list.see(index)
                self._select_skill()
                break
        self.status.set("技能已导入，默认停用。读完说明后再允许雷姆参考。")

    def _select_skill(self, _event=None):
        selected = self._selection(self.skill_list, self.skill_rows)
        if self.busy:
            if selected and selected["id"] == self._skill_reading_id:
                return  # Refresh may queue a second selection event for this read.
            expected = self._skill_reading_id or self._skill_loaded_id
            self.skill_list.selection_clear(0, "end")
            for index, row in enumerate(self.skill_rows):
                if row["id"] == expected:
                    self.skill_list.selection_set(index)
            self.status.set("正在处理接入操作，请稍候再选择技能。")
            return
        if not selected:
            self._reset_skill()
            self._skill_controls()
            return
        identifier = selected["id"]
        self._show_skill_detail()
        self.skill_name.set(selected.get("name", identifier))
        self.skill_description.set(selected.get("description", ""))
        self.skill_location.set("")
        self.skill_checks.set("正在读取技能…")
        self._refresh_skill_metadata()
        self._set_text(self.skill_output, "正在读取技能…", readonly=True)
        self._skill_loaded_id = ""
        self._skill_reading_id = identifier
        self.skill_state.set("正在读取技能…")
        self._work("正在读取技能…", lambda: self.manager.skills.read(identifier),
                   lambda row: self._show_skill(identifier, row), on_error=self._skill_error)

    def _show_skill(self, identifier, row):
        self._skill_reading_id = ""
        selected = self._selection(self.skill_list, self.skill_rows)
        if not selected or selected["id"] != identifier:
            self._reset_skill()
            return
        self._skill_loaded_id = identifier
        self.skill_name.set(row["name"])
        self.skill_description.set(row.get("description", "") or "此技能没有提供简介，请阅读下方说明。")
        self.skill_location.set(str(self.manager.root / row["path"]))
        self.skill_enabled.set(bool(row.get("enabled")))
        self.skill_state.set("已允许雷姆参考 · 不执行脚本" if row.get("enabled") else "已停用 · 读完后可允许参考")
        checks = "；".join(row["warnings"]) or "未发现已知危险词；这不代表内容已受信任。"
        self.skill_checks.set(f"SKILL.md 内容检查：{checks}\n其他脚本/资源未作代码安全审计，应用绝不自动执行。\nSHA256：{row['sha256']}")
        self._refresh_skill_metadata()
        self._set_text(self.skill_output, row['text'], readonly=True)
        self._detail_top(self.skill_detail_body)
        self._skill_controls()

    def _skill_error(self, error):
        self._skill_loaded_id = ""
        self._skill_reading_id = ""
        self.skill_enabled.set(False)
        self.skill_state.set("操作失败 · 检查文件后重试")
        self.skill_checks.set("读取或更新未完成；检查文件后重试。")
        self._refresh_skill_metadata()
        self._set_text(self.skill_output, error, readonly=True)
        self._skill_controls()

    def _toggle_skill(self):
        selected = self._selection(self.skill_list, self.skill_rows)
        if not selected or self.busy:
            return
        desired = self.skill_enabled.get()
        identifier = selected["id"]
        def complete(_row):
            self.skill_state.set("已允许雷姆参考 · 不执行脚本" if desired else "已停用 · 读完后可允许参考")
            self.status.set("技能参考状态已更新，没有执行脚本。")
        def failed(error):
            self.skill_enabled.set(bool(selected.get("enabled")))
            self.skill_state.set("保存失败 · 原状态保留")
            self.status.set(error)
        self._work("正在保存技能参考状态…",
                   lambda: self.manager.skills.toggle(identifier, desired), complete, on_error=failed)

    def _remove_skill(self):
        selected = self._selection(self.skill_list, self.skill_rows)
        if not selected or self.busy:
            return
        if not messagebox.askyesno("移除技能",
            f"移除 {selected.get('name', selected['id'])}？原文件可从 data/integrations/backups 找回。",
            parent=self, default="no"):
            return
        def complete(_result):
            self.skill_list.selection_clear(0, "end")
            self._reset_skill()
            self.refresh()
            self.status.set("技能已移出库；原文件可从 data/integrations/backups 找回。")
            self._back_skill()
        self._work("正在移除技能…", lambda: self.manager.skills.remove(selected["id"]),
                   complete, on_error=self._skill_error)
