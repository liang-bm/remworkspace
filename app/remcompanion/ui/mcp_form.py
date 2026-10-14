"""Common MCP fields with a lossless, optional JSON escape hatch.

Only configuration is edited here. No process or network operation is started.
Unknown fields (headers, environment references, cwd, etc.) survive form edits.
"""

from __future__ import annotations

from copy import deepcopy
import json
import tkinter as tk
from .soft_fields import SoftTtkEntry
from tkinter import ttk
from typing import Any

from ..integrations.storage import validate_mcp
from .theme import WHITE, INK, MUTED, LINE, BODY_FONT, ERROR


FONT = (BODY_FONT, 10)


class MCPForm(tk.Frame):
    def __init__(self, parent, *, text_factory, button_factory, enabled):
        super().__init__(parent, bg=WHITE)
        self._base: dict[str, Any] = {}
        self._locked_id = ""
        self.enabled = enabled
        self.advanced = False
        self.fields: dict[str, tk.StringVar] = {}
        self.entries: dict[str, ttk.Entry] = {}
        self.basic = tk.Frame(self, bg=WHITE)
        self.basic.pack(fill="x")
        self._field(self.basic, "name", "服务名称")
        self._field(self.basic, "id", "配置 ID")
        self._label(self.basic, "连接方式")
        self.transport = ttk.Combobox(self.basic, state="readonly", font=FONT,
            values=("HTTP 地址", "本机程序（stdio）"))
        self.transport._remchat_ui_choice_labels = ("HTTP 地址", "本机程序（stdio）")
        self.transport.pack(fill="x", pady=(0, 8))
        self.transport.bind("<<ComboboxSelected>>", self._transport_changed)
        self.http = tk.Frame(self.basic, bg=WHITE)
        self._field(self.http, "url", "服务 URL")
        self._label(self.http, "使用 HTTPS；本机 localhost / 127.0.0.1 可用 HTTP。", hint=True)
        self.stdio = tk.Frame(self.basic, bg=WHITE)
        self._field(self.stdio, "command", "可执行程序")
        self._label(self.stdio, "启动参数 · 每行一个，或填 JSON 字符串数组")
        self.args = text_factory(self.stdio, 3, code=True)
        self._label(self.stdio, "连接会启动该程序。保存不会执行；工作目录、环境变量在高级 JSON 中设置。", hint=True)
        self.toggle_button = button_factory(self, "高级 JSON  ▸", self._toggle)
        self.toggle_button.pack_configure(side="top", anchor="w", pady=(12, 6))
        self.raw_host = tk.Frame(self, bg=WHITE)
        self.raw = text_factory(self.raw_host, 8, code=True)
        self._label(self.raw_host, "配置明文保存。密钥使用 headers_env / env_refs 引用环境变量，不填真实密钥。", hint=True)
        self.error = tk.StringVar(self)
        label = tk.Label(self, textvariable=self.error, bg=WHITE, fg=ERROR,
                         font=FONT, justify="left", anchor="w")
        label.pack(fill="x", pady=(4, 0))
        label.bind("<Configure>", lambda e: label.configure(wraplength=max(100, e.width - 4)))

    @staticmethod
    def _label(parent, text, *, hint=False):
        label = tk.Label(parent, text=text, bg=WHITE, fg=MUTED if hint else INK,
                         font=FONT, anchor="w", justify="left")
        label.pack(fill="x", pady=(4, 5))
        label.bind("<Configure>", lambda e: label.configure(wraplength=max(100, e.width - 4)))

    def _field(self, parent, key, label):
        self._label(parent, label)
        variable = tk.StringVar(self)
        entry = SoftTtkEntry(parent, textvariable=variable, font=FONT)
        entry.pack(fill="x", pady=(0, 8))
        self.fields[key], self.entries[key] = variable, entry

    def load(self, value: dict[str, Any], *, existing=False):
        """Load a new selection, resetting both representations together."""
        self._base = deepcopy(value)
        self._locked_id = str(value.get("id", "")) if existing else ""
        for key, variable in self.fields.items():
            variable.set(str(value.get(key, "")))
        self.entries["id"].configure(state="readonly" if existing else "normal")
        self.enabled.set(bool(value.get("enabled", False)))
        self.transport.current(1 if value.get("transport") == "stdio" else 0)
        args = value.get("args", [])
        argument_text = (json.dumps(args, ensure_ascii=False) if any(not arg or "\n" in arg for arg in args)
                         else "\n".join(args))
        self.args.delete("1.0", "end")
        self.args.insert("1.0", argument_text)
        self._set_raw(value)
        self.error.set("")
        self._transport_changed()

    def _set_raw(self, value):
        self.raw.delete("1.0", "end")
        self.raw.insert("1.0", json.dumps(value, ensure_ascii=False, indent=2))

    def set_busy(self, busy):
        for key, entry in self.entries.items():
            entry.configure(state="disabled" if busy else "readonly" if key == "id" and self._locked_id else "normal")
        self.transport.configure(state="disabled" if busy else "readonly")
        for widget in (self.args, self.raw, self.toggle_button):
            widget.configure(state="disabled" if busy else "normal")

    def _transport_changed(self, _event=None):
        self.http.pack_forget()
        self.stdio.pack_forget()
        (self.stdio if self.transport.current() == 1 else self.http).pack(fill="x")

    def _value(self):
        if self.advanced:
            value = json.loads(self.raw.get("1.0", "end-1c"))
            if not isinstance(value, dict) or "mcpServers" in value:
                raise ValueError("这里编辑单条配置。多个 mcpServers 请用“导入 JSON”先检查。")
        else:
            value = deepcopy(self._base)
            value.update({key: variable.get().strip() for key, variable in self.fields.items()
                          if key in {"id", "name"}})
            value["transport"] = "stdio" if self.transport.current() == 1 else "http"
            if value["transport"] == "http":
                value["url"] = self.fields["url"].get().strip()
            else:
                value["command"] = self.fields["command"].get().strip()
                arguments = self.args.get("1.0", "end-1c")
                value["args"] = json.loads(arguments) if arguments.lstrip().startswith("[") else [line for line in arguments.splitlines() if line]
        value["enabled"] = self.enabled.get()
        return value

    def read(self):
        """Validate before saving/connecting. Errors leave every input intact."""
        try:
            value = self._value()
            if self._locked_id and value.get("id") != self._locked_id:
                raise ValueError("已保存服务的 ID 不能直接修改；请点“新增”建立另一条配置。")
            checked = validate_mcp(value)
        except (ValueError, TypeError) as error:
            self.error.set(str(error))
            if self.advanced:
                self.raw.focus_set()
            else:
                key = "id" if not self.fields["id"].get().strip() else ("command" if self.transport.current() == 1 else "url")
                self.entries[key].focus_set()
            raise ValueError(str(error)) from error
        self.error.set("")
        return checked

    def dirty(self):
        try:
            return validate_mcp(self._value()) != validate_mcp(self._base)
        except (ValueError, TypeError):
            return True

    def _toggle(self):
        try:
            if self.advanced:
                value = self.read()
                saved = self._base
                self.load(value, existing=bool(self._locked_id))
                self._base = saved  # Switching representation is not saving.
                self.raw_host.pack_forget()
                self.basic.pack(fill="x", before=self.toggle_button)
                self.toggle_button.configure(text="高级 JSON  ▸")
            else:
                self._set_raw(self._value())
                self.basic.pack_forget()
                self.raw_host.pack(fill="both", expand=True, after=self.toggle_button)
                self.toggle_button.configure(text="常用表单  ▾")
            self.advanced = not self.advanced
        except (ValueError, TypeError) as error:
            self.error.set(str(error))
            (self.raw if self.advanced else self.args).focus_set()
