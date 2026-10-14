"""Rem Companion Windows 控制中心。

界面只使用 Tkinter，确保模型环境尚未安装时仍可启动。耗时任务全部放入后台线程，
日志通过线程安全队列返回主线程。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
import os
import queue
import threading
import traceback
import tkinter as tk
from tkinter import messagebox, ttk

from .config import AppConfig, ensure_project_directories, load_config
from .dataset import prepare_dataset
from .downloader import download_components
from .environment import CheckResult, run_doctor
from .gptsovits import start_tts_api, train_models
from .launcher import start_companion_overlay
from .memory import initialize_memory_database


BG = "#08121F"
PANEL = "#0E1C2C"
PANEL_ALT = "#12263A"
LINE = "#1F3B55"
TEXT = "#EAF6FF"
MUTED = "#89A5BB"
CYAN = "#67D3F4"
BLUE = "#7BA8FF"
SUCCESS = "#65D6A5"
WARNING = "#F2C879"
ERROR = "#FF8296"


class ControlCenter:
    def __init__(self, root: tk.Tk, config: AppConfig) -> None:
        self.root = root
        self.config = config
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.buttons: list[tk.Button] = []
        self.status_labels: dict[str, tk.Label] = {}
        self.busy = False

        root.title("REM COMPANION // LOCAL WORKSHOP")
        root.geometry("1360x860")
        root.minsize(1080, 740)
        root.configure(bg=BG)
        root.option_add("*Font", ("Microsoft YaHei UI", 10))

        self._configure_styles()
        self._build_layout()
        self._initialize_data()
        self.root.after(80, self._drain_events)
        self.root.after(250, lambda: self.run_task("环境检查", self._doctor_task))

    def _configure_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Rem.Horizontal.TProgressbar",
            troughcolor=PANEL_ALT,
            background=CYAN,
            lightcolor=CYAN,
            darkcolor=CYAN,
            bordercolor=PANEL_ALT,
        )

    def _build_layout(self) -> None:
        header = tk.Frame(self.root, bg=BG, padx=34, pady=24)
        header.pack(fill="x")

        title_wrap = tk.Frame(header, bg=BG)
        title_wrap.pack(side="left")
        tk.Label(
            title_wrap,
            text="REM COMPANION",
            bg=BG,
            fg=TEXT,
            font=("Bahnschrift SemiBold", 25),
        ).pack(anchor="w")
        tk.Label(
            title_wrap,
            text="LOCAL VOICE WORKSHOP  /  TRAINING & CONTROL",
            bg=BG,
            fg=CYAN,
            font=("Cascadia Mono", 9),
        ).pack(anchor="w", pady=(3, 0))

        top_actions = tk.Frame(header, bg=BG)
        top_actions.pack(side="right")
        self._small_button(top_actions, "打开项目目录", self._open_project).pack(side="left", padx=5)
        self._small_button(top_actions, "刷新状态", lambda: self.run_task("环境检查", self._doctor_task)).pack(
            side="left", padx=5
        )

        accent = tk.Frame(self.root, bg=CYAN, height=2)
        accent.pack(fill="x", padx=34)

        body = tk.Frame(self.root, bg=BG, padx=34, pady=24)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, minsize=280, weight=0)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self._build_status_panel(body)
        self._build_workbench(body)

    def _build_status_panel(self, parent: tk.Frame) -> None:
        panel = tk.Frame(parent, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        panel.grid(row=0, column=0, sticky="nsew", padx=(0, 18))

        tk.Label(
            panel,
            text="SYSTEM READINESS",
            bg=PANEL,
            fg=MUTED,
            font=("Cascadia Mono", 9, "bold"),
        ).pack(anchor="w", padx=20, pady=(20, 14))

        for key, label in (
            ("GPU", "RTX / CUDA"),
            ("雷姆素材", "VOICE DATA"),
            ("FFmpeg", "FFMPEG"),
            ("GPT-SoVITS 源码", "GPT-SOVITS"),
            ("GPT-SoVITS Python", "TRAIN RUNTIME"),
            ("Qwen3.5-4B", "QWEN3.5 4B"),
            ("训练清单", "DATASET"),
        ):
            row = tk.Frame(panel, bg=PANEL_ALT, padx=12, pady=10)
            row.pack(fill="x", padx=14, pady=3)
            dot = tk.Label(row, text="●", bg=PANEL_ALT, fg=MUTED, font=("Segoe UI Symbol", 10))
            dot.pack(side="left")
            tk.Label(
                row,
                text=label,
                bg=PANEL_ALT,
                fg=TEXT,
                font=("Cascadia Mono", 9),
            ).pack(side="left", padx=(8, 0))
            self.status_labels[key] = dot

        tk.Frame(panel, bg=LINE, height=1).pack(fill="x", padx=18, pady=17)
        self.stage_label = tk.Label(
            panel,
            text="IDLE / 等待任务",
            bg=PANEL,
            fg=BLUE,
            font=("Cascadia Mono", 9, "bold"),
            wraplength=230,
            justify="left",
        )
        self.stage_label.pack(anchor="w", padx=20)
        self.progress = ttk.Progressbar(
            panel,
            style="Rem.Horizontal.TProgressbar",
            mode="indeterminate",
            length=230,
        )
        self.progress.pack(fill="x", padx=20, pady=(12, 18))

        notice = (
            "原始语音资源只读；加密语料仅在构建数据集时临时解密。\n"
            "下载可断点续传；训练可按阶段恢复。\n"
            "训练时请关闭游戏与其他 GPU 模型。"
        )
        tk.Label(
            panel,
            text=notice,
            bg=PANEL,
            fg=MUTED,
            justify="left",
            wraplength=235,
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", padx=20, pady=(0, 20))

    def _build_workbench(self, parent: tk.Frame) -> None:
        workbench = tk.Frame(parent, bg=BG)
        workbench.grid(row=0, column=1, sticky="nsew")
        workbench.grid_rowconfigure(1, weight=1)
        workbench.grid_columnconfigure(0, weight=1)

        actions = tk.Frame(workbench, bg=BG)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        for column in range(3):
            actions.grid_columnconfigure(column, weight=1)

        tasks = (
            ("01", "环境检查", "核对显卡、素材和组件", self._doctor_task),
            ("02", "下载 / 安装组件", "模型、引擎与训练环境", self._download_task),
            ("03", "处理雷姆素材", "SRT 切片与训练清单", self._prepare_task),
            ("04", "开始训练", "官方 V2Pro 全流程", self._train_task),
            ("05", "启动 TTS API", "加载最新雷姆权重", self._start_tts_task),
            ("06", "启动桌面伴侣", "中文回答 / 双语发音", self._start_companion_task),
            ("ALL", "一键准备并训练", "下载 → 素材 → 训练", self._all_task),
        )
        for index, (number, title, subtitle, callback) in enumerate(tasks):
            button = self._task_button(actions, number, title, subtitle, callback)
            button.grid(
                row=index // 3,
                column=index % 3,
                sticky="nsew",
                padx=(0 if index % 3 == 0 else 6, 0 if index % 3 == 2 else 6),
                pady=(0, 8),
            )

        terminal = tk.Frame(workbench, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        terminal.grid(row=1, column=0, sticky="nsew")

        bar = tk.Frame(terminal, bg=PANEL_ALT, padx=14, pady=9)
        bar.pack(fill="x")
        tk.Label(
            bar,
            text="LIVE TASK LOG",
            bg=PANEL_ALT,
            fg=MUTED,
            font=("Cascadia Mono", 9, "bold"),
        ).pack(side="left")
        self.log_state = tk.Label(
            bar,
            text="READY",
            bg=PANEL_ALT,
            fg=SUCCESS,
            font=("Cascadia Mono", 9, "bold"),
        )
        self.log_state.pack(side="right")

        log_wrap = tk.Frame(terminal, bg=PANEL)
        log_wrap.pack(fill="both", expand=True, padx=5, pady=5)
        scrollbar = tk.Scrollbar(log_wrap)
        scrollbar.pack(side="right", fill="y")
        self.log_text = tk.Text(
            log_wrap,
            bg="#07101B",
            fg="#BFD9E8",
            insertbackground=TEXT,
            selectbackground="#274B67",
            relief="flat",
            padx=14,
            pady=12,
            wrap="word",
            font=("Cascadia Mono", 9),
            yscrollcommand=scrollbar.set,
            state="disabled",
        )
        self.log_text.pack(fill="both", expand=True)
        scrollbar.config(command=self.log_text.yview)
        self.log_text.tag_configure("time", foreground="#59778E")
        self.log_text.tag_configure("error", foreground=ERROR)
        self.log_text.tag_configure("success", foreground=SUCCESS)

    def _task_button(
        self,
        parent: tk.Frame,
        number: str,
        title: str,
        subtitle: str,
        callback: Callable[[], None],
    ) -> tk.Button:
        text = f"{number}  {title}\n     {subtitle}"
        button = tk.Button(
            parent,
            text=text,
            command=lambda: self.run_task(title, callback),
            bg=PANEL_ALT,
            fg=TEXT,
            activebackground="#193550",
            activeforeground=TEXT,
            disabledforeground="#4E687B",
            relief="flat",
            bd=0,
            padx=15,
            pady=13,
            anchor="w",
            justify="left",
            cursor="hand2",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.buttons.append(button)
        return button

    @staticmethod
    def _small_button(parent: tk.Frame, text: str, command: Callable[[], None]) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=PANEL_ALT,
            fg=TEXT,
            activebackground="#193550",
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            padx=14,
            pady=8,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9),
        )

    def _initialize_data(self) -> None:
        ensure_project_directories(self.config)
        database = initialize_memory_database(self.config.root / "data" / "memory.sqlite3")
        database.close()
        self._append_log("控制中心已启动；所有缓存和输出均指向当前工作目录。", "success")

    def _open_project(self) -> None:
        from .platforms import native_module
        native_module("shell").open_directory(self.config.root)

    def _append_log(self, message: str, tag: str | None = None) -> None:
        self.log_text.configure(state="normal")
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{stamp}] ", "time")
        self.log_text.insert("end", message.rstrip() + "\n", tag or "")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _post_log(self, message: str) -> None:
        self.events.put(("log", message))

    def _progress_log(self, name: str, downloaded: int, total: int | None) -> None:
        current_mb = downloaded / (1024**2)
        if total:
            self._post_log(f"[进度] {name}: {current_mb:.1f}/{total / (1024**2):.1f} MB")
        else:
            self._post_log(f"[进度] {name}: {current_mb:.1f} MB")

    def run_task(self, name: str, callback: Callable[[], None]) -> None:
        if self.busy:
            messagebox.showinfo("任务正在运行", "请等待当前任务结束。")
            return
        self.busy = True
        self.stage_label.configure(text=f"RUNNING / {name}", fg=CYAN)
        self.log_state.configure(text="RUNNING", fg=CYAN)
        self.progress.start(10)
        for button in self.buttons:
            button.configure(state="disabled")
        self._append_log(f"===== {name} =====")

        def worker() -> None:
            try:
                callback()
            except Exception:
                self.events.put(("error", traceback.format_exc()))
            else:
                self.events.put(("done", name))

        threading.Thread(target=worker, name=f"rem-{name}", daemon=True).start()

    def _doctor_task(self) -> None:
        results = run_doctor(self.config)
        self.events.put(("doctor", results))
        for result in results:
            marker = "✓" if result.ok else "○"
            self._post_log(f"{marker} {result.name}: {result.detail}")

    def _download_task(self) -> None:
        download_components(
            self.config,
            log=self._post_log,
            progress=self._progress_log,
        )

    def _prepare_task(self) -> None:
        prepare_dataset(self.config, log=self._post_log)

    def _train_task(self) -> None:
        train_models(self.config, log=self._post_log)

    def _start_tts_task(self) -> None:
        start_tts_api(self.config, log=self._post_log)

    def _start_companion_task(self) -> None:
        start_companion_overlay(self.config, log=self._post_log)

    def _all_task(self) -> None:
        self._post_log("一键流程开始：下载组件 → 处理数据 → 训练模型")
        self._download_task()
        self._prepare_task()
        self._train_task()

    def _apply_doctor(self, results: list[CheckResult]) -> None:
        by_name = {result.name: result for result in results}
        for name, label in self.status_labels.items():
            result = by_name.get(name)
            label.configure(fg=SUCCESS if result and result.ok else MUTED)

    def _set_idle(self, success: bool, message: str) -> None:
        self.busy = False
        self.progress.stop()
        color = SUCCESS if success else ERROR
        self.stage_label.configure(text="IDLE / 等待任务", fg=BLUE)
        self.log_state.configure(text="DONE" if success else "FAILED", fg=color)
        for button in self.buttons:
            button.configure(state="normal")
        self._append_log(message, "success" if success else "error")

    def _drain_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "log":
                    self._append_log(str(payload))
                elif event == "doctor":
                    self._apply_doctor(payload)  # type: ignore[arg-type]
                elif event == "done":
                    self._set_idle(True, f"{payload} 已完成。")
                    if payload != "环境检查":
                        self.root.after(350, lambda: self.run_task("环境检查", self._doctor_task))
                elif event == "error":
                    self._append_log(str(payload), "error")
                    self._set_idle(False, "任务失败；请查看上方完整堆栈和失败命令。")
                    messagebox.showerror("任务失败", "任务没有完成。详细原因已写入控制台日志。")
        except queue.Empty:
            pass
        self.root.after(80, self._drain_events)


def main() -> None:
    config = load_config()
    root = tk.Tk()
    ControlCenter(root, config)
    root.mainloop()


if __name__ == "__main__":
    main()
