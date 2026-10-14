"""Draft-aware per-model editor, independent of credentials and network requests."""

from dataclasses import asdict
import tkinter as tk
from tkinter import ttk, font as tkfont

from ..model_providers import ModelConfig
from .theme import WHITE, INK, MUTED, ERROR
from .dpi import pixels
from . import theme

theme.register_module(__name__)


class ProviderModelsMixin:
    def _build_provider_models(self, parent):
        self._model_rows = []
        self._model_index = None
        self._provider_model_drafts = {}
        self.provider_model_name_var = tk.StringVar(master=self.root)
        self.provider_context_var = tk.StringVar(master=self.root)
        self.provider_output_var = tk.StringVar(master=self.root)
        heading = tk.Frame(parent, bg=WHITE)
        heading.pack(fill="x", pady=(12, 6))
        tk.Label(heading, text="模型", bg=WHITE, fg=INK, font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        self.provider_remove_model_button = self._setting_button(heading, "删除模型", self._remove_provider_model, danger=True)
        self.provider_remove_model_button.pack(side="right")
        self.provider_add_model_button = self._setting_button(heading, "添加模型", self._add_provider_model)
        self.provider_add_model_button.pack(side="right", padx=(0, 6))
        list_row = tk.Frame(parent, bg=WHITE)
        list_row.pack(fill="x", pady=(0, 8))
        model_font = tkfont.Font(root=self.root, family="Microsoft YaHei UI", size=9)
        style = ttk.Style(self.root)
        style.configure("ProviderModels.Treeview", font=model_font,
                        rowheight=model_font.metrics("linespace") + pixels(self.root, 8))
        style.configure("ProviderModels.Treeview.Heading", font=("Microsoft YaHei UI", 8, "bold"))
        self._provider_model_font = model_font
        self.provider_models_tree = ttk.Treeview(list_row, columns=("name",), show="tree headings", height=3,
                                                selectmode="browse", style="ProviderModels.Treeview")
        self.provider_models_tree.heading("#0", text="模型 ID")
        self.provider_models_tree.heading("name", text="显示名称")
        self.provider_models_tree.column("#0", width=pixels(self.root, 160), minwidth=pixels(self.root, 80), stretch=True)
        self.provider_models_tree.column("name", width=pixels(self.root, 130), minwidth=pixels(self.root, 60), stretch=True)
        scrollbar = ttk.Scrollbar(list_row, orient="vertical", command=self.provider_models_tree.yview)
        self.provider_models_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.provider_models_tree.pack(side="left", fill="x", expand=True)
        self.provider_models_tree.bind("<<TreeviewSelect>>", self._select_provider_model)
        # Changing native ttk theme resets custom styles. Reapply measured row
        # geometry after that event, not only once before the window is mapped.
        self.provider_models_tree.bind("<<ThemeChanged>>", self._refresh_model_tree_style)
        self.provider_models_tree.bind("<Map>", self._refresh_model_tree_style)
        fields = tk.Frame(parent, bg=WHITE)
        fields.pack(fill="x")
        self.provider_model_field_hosts = []
        entries = []
        for label, variable in (("模型 ID", self.provider_model_var), ("显示名称", self.provider_model_name_var),
                                ("上下文 Token", self.provider_context_var), ("最大输出 Token", self.provider_output_var)):
            host = tk.Frame(fields, bg=WHITE)
            entry = self._provider_field(host, label, variable)
            entries.append(entry)
            self.provider_model_field_hosts.append(host)
        self.provider_model_entry, self.provider_model_name_entry, self.provider_context_entry, self.provider_output_entry = entries
        self._provider_model_field_columns = None

        def reflow(event):
            columns = 2 if event.width >= pixels(self.root, 360) else 1
            if columns == self._provider_model_field_columns:
                return
            self._provider_model_field_columns = columns
            for index, host in enumerate(self.provider_model_field_hosts):
                host.grid(row=index // columns, column=index % columns, sticky="ew",
                          padx=(0, 12) if columns == 2 and index % 2 == 0 else 0)
            fields.columnconfigure(0, weight=1)
            fields.columnconfigure(1, weight=1 if columns == 2 else 0)
        fields.bind("<Configure>", reflow)
        self.provider_limit_note = tk.Label(parent, text="未知限制留空；上下文非精确计数。输出取软件预算与此上限的较小值。",
                                           bg=WHITE, fg=MUTED, anchor="w", justify="left", font=("Microsoft YaHei UI", 8))
        self.provider_limit_note.pack(fill="x", pady=(0, 8))
        self.provider_limit_note.bind("<Configure>", lambda e: self.provider_limit_note.configure(wraplength=max(80, e.width)))

    def _refresh_model_tree_style(self, _event=None):
        style = ttk.Style(self.root)
        style.configure("ProviderModels.Treeview", font=self._provider_model_font,
                        rowheight=self._provider_model_font.metrics("linespace") + pixels(self.root, 8))
        style.configure("ProviderModels.Treeview.Heading", font=("Microsoft YaHei UI", 8, "bold"))

    def _model_form_values(self):
        return dict(model_id=self.provider_model_var.get(), display_name=self.provider_model_name_var.get(),
                    context_tokens=self.provider_context_var.get(), max_output_tokens=self.provider_output_var.get(),
                    advanced_model_enabled=self.provider_advanced.get(), supports_images=self.provider_images.get())

    def _capture_model_form(self):
        if self._model_index is not None and 0 <= self._model_index < len(self._model_rows):
            self._model_rows[self._model_index] = self._model_form_values()
            iid = str(self._model_index)
            if self.provider_models_tree.exists(iid):
                row = self._model_rows[self._model_index]
                self.provider_models_tree.item(iid, text=row["model_id"] or "新模型", values=(row["display_name"],))

    def _load_model_form(self, index):
        self._model_index = index
        values = self._model_rows[index]
        previous = self._provider_refreshing
        self._provider_refreshing = True
        try:
            for field, variable in (("model_id", self.provider_model_var), ("display_name", self.provider_model_name_var),
                                    ("context_tokens", self.provider_context_var), ("max_output_tokens", self.provider_output_var),
                                    ("advanced_model_enabled", self.provider_advanced), ("supports_images", self.provider_images)):
                variable.set(values[field])
        finally:
            self._provider_refreshing = previous

    def _rebuild_model_tree(self):
        self.provider_models_tree.delete(*self.provider_models_tree.get_children())
        for index, values in enumerate(self._model_rows):
            self.provider_models_tree.insert("", "end", iid=str(index), text=values["model_id"] or "新模型",
                                             values=(values["display_name"],))
        if self._model_index is not None:
            self.provider_models_tree.selection_set(str(self._model_index))
            self.provider_models_tree.see(str(self._model_index))

    def _load_provider_model_rows(self, provider):
        draft = self._provider_model_drafts.get(provider.provider_id)
        if draft:
            rows, index = draft
            self._model_rows = [dict(item) for item in rows]
        else:
            self._model_rows = []
            for model in provider.configured_models:
                row = asdict(model)
                for key in ("context_tokens", "max_output_tokens"):
                    row[key] = str(row[key]) if row[key] is not None else ""
                self._model_rows.append(row)
            index = next(i for i, model in enumerate(provider.configured_models) if model.model_id == provider.model)
        self._load_model_form(index)
        self._rebuild_model_tree()

    def _stash_provider_model_rows(self):
        self._capture_model_form()
        self._provider_model_drafts[self.selected_provider_id] = ([dict(item) for item in self._model_rows], self._model_index)

    def _select_provider_model(self, _event=None):
        selected = self.provider_models_tree.selection()
        if not selected or int(selected[0]) == self._model_index:
            return
        self._capture_model_form()
        self._load_model_form(int(selected[0]))

    def _add_provider_model(self):
        self._capture_model_form()
        self._model_rows.append(dict(model_id="", display_name="", context_tokens="", max_output_tokens="",
                                    advanced_model_enabled=False, supports_images=False))
        self._load_model_form(len(self._model_rows) - 1)
        self._rebuild_model_tree()
        self.provider_model_entry.focus_set()

    def _remove_provider_model(self):
        if len(self._model_rows) < 2:
            self.provider_status.configure(text="至少保留一个模型", fg=ERROR)
            return
        self._model_rows.pop(self._model_index)
        self._load_model_form(min(self._model_index, len(self._model_rows) - 1))
        self._rebuild_model_tree()

    def _models_from_editor(self, *, allow_empty_model=False):
        self._capture_model_form()
        models = []
        for row in self._model_rows:
            values = dict(row)
            values["model_id"] = values["model_id"].strip()
            values["display_name"] = values["display_name"].strip()
            if not values["model_id"] and allow_empty_model:
                values["model_id"] = "pending-model"
            for field in ("context_tokens", "max_output_tokens"):
                text = str(values[field]).strip()
                if text and (not text.isascii() or not text.isdecimal()):
                    raise ValueError("Token 限制必须是正整数；未知时留空")
                values[field] = int(text) if text else None
            models.append(ModelConfig(**values).validate())
        return tuple(models)

    def _reset_model_capabilities(self, *_args):
        if self._provider_refreshing:
            return
        self.provider_advanced.set(False)
        self.provider_images.set(False)
        # Address changes invalidate acknowledgements of *every* model.
        if hasattr(self, "_model_rows"):
            for row in self._model_rows:
                row["advanced_model_enabled"] = False
                row["supports_images"] = False

    def _reset_current_model_capabilities(self, *_args):
        if not self._provider_refreshing:
            self.provider_advanced.set(False)
            self.provider_images.set(False)
