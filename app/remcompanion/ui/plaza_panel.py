"""RemChat 广场：轻小游戏在一个工作台里完成，聊天只用于可选讲解。

THESIS: 先选择想用的功能，再进入游戏、学习或陪伴设置。
OWN-WORLD: 延续 RemChat 蓝白界面；棋盘与像素菜地承担视觉重心。
STORY: 切换活动、直接操作、自动保存；主动陪伴由用户明确开启。
FIRST VIEWPORT: 可扩展功能目录，每块均有“进入”；功能内提供返回目录入口。
FORM: 既有桌面产品的功能扩展，继承现有字体与控件习惯，不增加新壳层。
"""

from __future__ import annotations

from collections.abc import Callable
import math
from pathlib import Path
import queue
import random
import threading
import time
import tkinter as tk
from .font_source import native_font
from .scroll_surface import ScrollCanvas
from .soft_fields import SoftEntry, SoftSpinbox
import tkinter.font as tkfont
from .localized_dialogs import filedialog, messagebox

from ..clipboard import copy_text
from ..plaza.companion import CompanionSchedule, CompanionSettings
from ..plaza.garden import CROPS, Garden
from ..plaza.gomoku import GomokuGame
from ..plaza.storage import PlazaStore
from ..plaza.words import WordDeck
from .plaza_persistence import PlazaPersistenceMixin
from . import theme
from .theme import (
    ACCENT_DEEP as BLUE, ACCENT_SOFT as SOFT, APP_BG as BG,
    BODY_FONT as FONT, INK, LINE, MUTED, ROSE, SUCCESS as GREEN, WHITE, ON_ACCENT,
)


ModelRequest = Callable[[str, Callable[[str], None]], None]


class PlazaPanel(PlazaPersistenceMixin, tk.Frame):
    """标准 Tk Frame，存档位于项目 data/plaza，所有提醒在主线程回调。

    on_chat_prompt 将讲解请求送入普通聊天；
    on_notify 只显示/播报提醒；
    on_model_request 可选，它异步返回纯文本且不写入聊天记忆。
    """

    def __init__(
        self, parent: tk.Misc, root_path: Path | str,
        on_chat_prompt: Callable[[str], None], on_notify: Callable[[str], None],
        *, on_model_request: ModelRequest | None = None,
        can_notify: Callable[[], bool] | None = None,
        on_auto_notify: Callable[[str], None] | None = None,
        process_owner=None,
    ) -> None:
        super().__init__(parent, bg=BG)
        self.store = PlazaStore(root_path)
        self._root_path = Path(root_path)
        self.diary_panel = None
        self.data_path = self.store.path
        self.on_chat_prompt = on_chat_prompt
        self.on_notify = on_notify
        self.on_auto_notify = on_auto_notify or on_notify
        self.on_model_request = on_model_request
        self.can_notify = can_notify or (lambda: True)
        self.process_owner = process_owner
        self.process_panel = None
        self.project_workspace = None
        self._save_errors: dict[str, str] = {}
        self.game = self._load("gomoku", GomokuGame.restore, GomokuGame)
        self.garden = self._load("garden", Garden.restore, Garden)
        self.words = self._load("words", WordDeck.restore, WordDeck.demo)
        settings = self._load("companion", CompanionSettings.restore, CompanionSettings)
        self.schedule = CompanionSchedule(settings)
        self._closed = False
        self._timer_id: str | None = None
        self._ai_timer_id: str | None = None
        self._model_timeout_id: str | None = None
        self._model_responses: queue.Queue[tuple[int, str]] = queue.Queue()
        self._local_responses: queue.Queue[tuple[int, tuple[int, int] | None, bool]] = queue.Queue()
        self._local_poll_id: str | None = None
        self._ai_due_at = 0.0
        self._ai_local_pending = False
        self._game_generation = 0
        self._ai_busy = False
        self._notification_index = 0
        self._last_garden_refresh = 0.0
        self._selected_plot = 0
        self._board_geometry = (0.0, 0.0, 1.0)
        self._garden_geometry = (0.0, 0.0, 1.0)
        self._pages: dict[str, tk.Frame] = {}
        self._active_page = "home"
        # Tk 的 pt 字体随屏幕 DPI 放大；固定像素栏宽必须同步放大。
        self._ui_scale = max(1.0,native_font(self,font=(FONT,10)).metrics("linespace")/18)
        self._control_width = round(220 * self._ui_scale)
        self.status = tk.StringVar(self, "")
        self._build()
        self.refresh()
        self.start_companion()
        self.bind("<Destroy>", self._on_destroy, add="+")
        if self._save_errors:
            self.after_idle(lambda: self.status.set("部分存档读取失败，原文件已保留。请打开存档目录检查。"))

    def _label(self, parent: tk.Misc, text: str, *, size: int = 11,
               color: str | None = None, bold: bool = False, **kwargs) -> tk.Label:
        if parent in getattr(self, "_control_frames", []):
            kwargs.setdefault("wraplength", self._control_width - 12)
            kwargs.setdefault("anchor", "w")
            kwargs.setdefault("justify", "left")
        return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=INK if color is None else color,
                        font=(FONT, size, "bold" if bold else "normal"), **kwargs)

    def _button(self, parent: tk.Misc, text: str, command: Callable, *,
                primary: bool = False, **kwargs) -> tk.Button:
        return tk.Button(
            parent, text=text, command=command, bg=BLUE if primary else SOFT,
            fg=ON_ACCENT if primary else (theme.ACCENT if theme.get_mode() == "dark" else BLUE),
            activebackground=theme.ACCENT_HOVER if primary else SOFT,
            activeforeground=ON_ACCENT if primary else INK, bd=0, relief="flat",
            font=(FONT, 10, "bold"), padx=16, pady=10, cursor="hand2",
            highlightthickness=1, highlightbackground=LINE,
            highlightcolor=BLUE, takefocus=True, **kwargs,
        )

    def _build(self) -> None:
        heading = tk.Frame(self, bg=BG)
        heading.pack(fill="x", padx=26, pady=(20, 14))
        self._label(heading, "功能广场", size=23, bold=True).pack(anchor="w")
        self.page_caption = tk.StringVar(self, "")
        self._label(heading, "", color=MUTED, textvariable=self.page_caption).pack(anchor="w", pady=(6, 0))
        navigation = tk.Frame(self, bg=BG)
        navigation.pack(fill="x", padx=26)
        self.back_button = self._button(navigation, "← 返回功能广场", lambda: self.show_page("home"))
        self.content = tk.Frame(self, bg=BG)
        self.content.pack(fill="both", expand=True, padx=26, pady=18)
        for name in ("gomoku", "garden", "words", "companion", "process", "diary"):
            self._pages[name] = tk.Frame(self.content, bg=WHITE)
        from .plaza_hub import PlazaHub
        self._pages["home"] = PlazaHub(self.content, self._enter_feature)
        self._build_gomoku(self._pages["gomoku"])
        self._build_garden(self._pages["garden"])
        self._build_words(self._pages["words"])
        self._build_companion(self._pages["companion"])
        if self.process_owner is not None:
            from .project_workspace import ProjectWorkspace
            self.project_workspace = ProjectWorkspace(self._pages["process"], self.process_owner)
            self.project_workspace.pack(fill="both", expand=True)
        footer = tk.Frame(self, bg=BG)
        footer.pack(fill="x", padx=26, pady=(0, 12))
        actions = tk.Frame(footer, bg=BG)
        actions.pack(side="right")
        self._button(actions, "打开存档", self._open_data).pack(side="right")
        self.reload_button = self._button(actions, "重新读取当前存档", self._reload_visible)
        self.reload_button.pack(side="right", padx=(0, 8))
        status_label = tk.Label(footer, textvariable=self.status, bg=BG, fg=MUTED,
                               font=(FONT, 9), anchor="w", justify="left")
        status_label.pack(side="left", fill="x", expand=True, padx=(0, 12))
        status_label.bind("<Configure>", lambda event: status_label.configure(wraplength=max(120, event.width)))
        self.show_page("home")

    def _enter_feature(self, name: str) -> None:
        self.show_page(name)

    def _reload_visible(self) -> None:
        if self._active_page == 'diary' and self.diary_panel is not None:
            if self.diary_panel.flush_pending():self.diary_panel.on_show()
            return
        if self._active_page in {"gomoku", "garden", "words", "companion"}:
            if self._active_page == "gomoku":
                self._cancel_ai()
            self._reload_activity()

    def pause_process(self) -> None:
        if self.project_workspace is not None:
            self.project_workspace.pause()

    def show_page(self, name: str, *, process_mode: str = "observe", section: str = "tasks") -> None:
        if name not in self._pages:
            return
        if name != self._active_page and not self.flush_diary():
            return
        if name == 'diary' and self.diary_panel is None:
            from .journal_panel import JournalPanel
            self.diary_panel=JournalPanel(self._pages['diary'],self._root_path)
            self.diary_panel.pack(fill='both',expand=True)
        if name == "process" and self.project_workspace is None and not self._pages[name].winfo_children():
            self._label(self._pages[name], "请从 RemChat 主窗口打开项目过程，读取该窗口的会话记录。",
                        color=MUTED).pack(padx=24, pady=24)
        if name != "process":
            self.pause_process()
        if name != "gomoku" and self._active_page == "gomoku":
            self._cancel_ai()
        for page in self._pages.values():
            page.pack_forget()
        self._pages[name].pack(fill="both", expand=True)
        self._active_page = name
        if name == "home":
            self.back_button.pack_forget()
            self.page_caption.set("")
            self.reload_button.configure(state="disabled")
        else:
            self.back_button.pack(side="left")
            self.page_caption.set({"gomoku": "五子棋", "garden": "像素花园",
                                   "words": "单词卡片", "companion": "主动陪伴",
                                   "diary": "日记",
                                   "process": "项目过程 · 观察展示、接入与会话管理"}[name])
            self.reload_button.configure(state="disabled" if name == "process" else "normal")
        self.record_activity()
        if name == 'diary':self.diary_panel.on_show()
        if name == "words":
            self._draw_word()
        if name == "process" and self.project_workspace is not None:
            self.project_workspace.show(process_mode, section=section)
        if name == "garden":
            self.after_idle(self._draw_garden)
        if name == "gomoku":
            self.after_idle(self._draw_board)
            if self.game.turn == 2 and not self.game.finished and not self._ai_busy and self._ai_timer_id is None:
                self._request_ai()

    def flush_diary(self) -> bool:
        return self.diary_panel is None or self.diary_panel.flush_pending()

    @staticmethod
    def _bind_control_wheel(viewport: tk.Canvas, content: tk.Misc) -> None:
        """Bind only this control subtree to its own scrolling canvas."""
        def scroll(event: tk.Event) -> str:
            viewport.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"
        def visit(widget: tk.Misc) -> None:
            widget.bind("<MouseWheel>", scroll, add="+")
            for child in widget.winfo_children():
                visit(child)
        visit(content)
        viewport.bind("<MouseWheel>", scroll)

    def _activity_layout(self, parent: tk.Frame) -> tuple[tk.Frame, tk.Frame]:
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=0)
        parent.rowconfigure(0, weight=1)
        stage = tk.Frame(parent, bg=WHITE)
        stage.grid(row=0, column=0, sticky="nsew", padx=(16, 12), pady=16)
        rail = tk.Frame(parent, bg=WHITE, width=self._control_width + 16)
        rail.grid(row=0, column=1, sticky="ns", padx=(4, 14), pady=18)
        rail.pack_propagate(False)
        viewport = ScrollCanvas(rail, bg=WHITE, width=self._control_width, highlightthickness=0)
        viewport.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(rail, orient="vertical", command=viewport.yview)
        scrollbar.pack(side="right", fill="y")
        viewport.configure(yscrollcommand=scrollbar.set)
        controls = tk.Frame(viewport, bg=WHITE)
        if not hasattr(self, "_control_frames"):
            self._control_frames = []
        self._control_frames.append(controls)
        item = viewport.create_window(0, 0, window=controls, anchor="nw")
        controls.bind("<Configure>", lambda _: viewport.configure(scrollregion=viewport.bbox("all")))
        viewport.bind("<Configure>", lambda event: viewport.itemconfigure(item, width=event.width))
        # 每个活动控件只滚动自己的说明栏，不抢主聊天的滚轮事件。
        self.after_idle(lambda: self._bind_control_wheel(viewport, controls))
        return stage, controls

    def _build_gomoku(self, parent: tk.Frame) -> None:
        # Hiding the entire plaza (for Settings/chat) is also leaving this game,
        # even though the plaza's selected sub-page remains "gomoku".
        parent.bind("<Unmap>", lambda _event: self._cancel_ai(), add="+")
        parent.bind("<Map>", lambda _event: self._request_ai(), add="+")
        stage, controls = self._activity_layout(parent)
        stage.columnconfigure(0, weight=1)
        stage.rowconfigure(0, weight=1)
        self.board_canvas = tk.Canvas(stage, bg="#F4F8FC", highlightthickness=0,
                                      width=420, height=420, cursor="crosshair")
        # 棋盘是固定浅色游戏画布，不能把白棋随 UI surface 一起染成深色。
        self.board_canvas._remchat_theme_fixed = True
        self.board_canvas.grid(row=0, column=0, sticky="nsew")
        self._board_xscroll = tk.Scrollbar(stage, orient="horizontal", command=self.board_canvas.xview)
        self._board_yscroll = tk.Scrollbar(stage, orient="vertical", command=self.board_canvas.yview)
        self.board_canvas.configure(xscrollcommand=self._board_xscroll.set,
                                    yscrollcommand=self._board_yscroll.set)
        self.board_canvas.bind("<Configure>", lambda _: self._draw_board())
        self.board_canvas.bind("<Button-1>", self._board_click)
        self.board_canvas.bind("<MouseWheel>", self._board_wheel)
        self.board_canvas.bind("<Shift-MouseWheel>", lambda event: self._board_wheel(event, horizontal=True))
        self._board_render_key = None
        self._board_label_font = native_font(self,font=(FONT,8))
        self._label(controls, "和雷姆下一盘", size=16, bold=True).pack(anchor="w", pady=(0, 12))
        self.game_status = tk.StringVar(self, "你执黑，先手。")
        tk.Label(controls, textvariable=self.game_status, bg=WHITE, fg=BLUE,
                 font=(FONT, 11, "bold"), wraplength=self._control_width - 12, justify="left").pack(anchor="w", pady=(0, 18))
        self._label(controls, "22×22 新棋盘\n横、竖、斜连成五子获胜。\n点击交叉点落子。",
                    color=MUTED, justify="left").pack(anchor="w", pady=(0, 16))
        self.model_opponent = tk.BooleanVar(self, False)
        tk.Checkbutton(controls, text="让当前聊天模型参与落子（耗 Token）", variable=self.model_opponent,
                       bg=WHITE, fg=INK, selectcolor=SOFT, activebackground=WHITE,
                       font=(FONT, 10), wraplength=self._control_width - 12).pack(anchor="w", pady=8)
        self._label(controls, "本地对手思考 2–5 秒，不耗 Token。\n勾选后调用当前聊天模型。\n旧棋局保持原有尺寸。",
                    color=MUTED, size=9, justify="left").pack(anchor="w", pady=(0, 18))
        self._button(controls, "新开一盘", self._new_game, primary=True).pack(fill="x", pady=4)
        self._button(controls, "悔棋一回合", self._undo_game).pack(fill="x", pady=4)
        self._button(controls, "雷姆讲解棋局", self._explain_game).pack(fill="x", pady=4)
        self._button(controls, "轮到我时给个提示", self._hint_game).pack(fill="x", pady=4)

    def _draw_board(self) -> None:
        if self._closed:
            return
        canvas = self.board_canvas
        width, height = max(100, canvas.winfo_width()), max(100, canvas.winfo_height())
        label_font = self._board_label_font
        margin = max(24, label_font.measure(str(self.game.size)) + 10,
                     label_font.metrics("linespace") + 10)
        minimum_step = max(label_font.measure(str(self.game.size)) + 6,
                           label_font.metrics("linespace") + 4)
        minimum_extent = minimum_step * (self.game.size - 1) + margin * 2
        world_width, world_height = max(width, minimum_extent), max(height, minimum_extent)
        if world_width > width:
            self._board_xscroll.grid(row=1, column=0, sticky="ew")
        else:
            self._board_xscroll.grid_remove()
        if world_height > height:
            self._board_yscroll.grid(row=0, column=1, sticky="ns")
        else:
            self._board_yscroll.grid_remove()
        canvas.configure(scrollregion=(0, 0, world_width, world_height))
        key = (width, height, self.game.size, tuple(self.game.moves),
               tuple(self.game.winning_line), margin, minimum_step)
        if key == self._board_render_key:
            return
        self._board_render_key = key
        side = min(world_width, world_height) - margin * 2
        step = side / (self.game.size - 1)
        x0, y0 = (world_width - side) / 2, (world_height - side) / 2
        self._board_geometry = (x0, y0, step)
        canvas.delete("all")
        for index in range(self.game.size):
            x, y = x0 + index * step, y0 + index * step
            canvas.create_line(x, y0, x, y0 + side, fill="#A7C6DF")
            canvas.create_line(x0, y, x0 + side, y, fill="#A7C6DF")
            canvas.create_text(x, y0 - margin / 2, text=str(index + 1), fill="#596B7D", font=label_font)
            canvas.create_text(x0 - margin / 2, y, text=str(index + 1), fill="#596B7D", font=label_font)
        far, center = self.game.size - 4, self.game.size // 2
        for row, column in ((3, 3), (3, far), (center, center), (far, 3), (far, far)):
            if row < self.game.size and column < self.game.size:
                x, y = x0 + column * step, y0 + row * step
                canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill="#216D9F", outline="")
        radius = step * 0.41
        for row, column, player in self.game.moves:
            x, y = x0 + column * step, y0 + row * step
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius,
                               fill="#223C51" if player == 1 else "#FFFFFF",
                               outline="#223C51" if player == 1 else "#8EB5D4", width=1.5)
        if self.game.moves:
            row, column, player = self.game.moves[-1]
            x, y = x0 + column * step, y0 + row * step
            canvas.create_rectangle(x - 2, y - 2, x + 2, y + 2,
                                    fill="#FFFFFF" if player == 1 else "#216D9F", outline="")
        for row, column in self.game.winning_line:
            x, y = x0 + column * step, y0 + row * step
            canvas.create_oval(x - radius - 3, y - radius - 3, x + radius + 3, y + radius + 3,
                               outline="#AD4C70", width=2)

    def _board_click(self, event: tk.Event) -> None:
        if self._ai_busy or self.game.turn != 1 or self.game.finished:
            return
        x0, y0, step = self._board_geometry
        x, y = self.board_canvas.canvasx(event.x), self.board_canvas.canvasy(event.y)
        column_float, row_float = (x - x0) / step, (y - y0) / step
        # Do not round a click in the outer labels into an edge intersection.
        if not (-0.45 <= row_float <= self.game.size - 0.55
                and -0.45 <= column_float <= self.game.size - 0.55):
            return
        row, column = round(row_float), round(column_float)
        if not self.game.legal(row, column):
            return
        saved, _ = self._commit_activity("gomoku", lambda game: game.play(row, column))
        if not saved:
            return
        self.record_activity()
        self._update_game_status()
        self._draw_board()
        if not self.game.finished:
            self._request_ai()

    def _board_wheel(self, event: tk.Event, *, horizontal: bool = False) -> str:
        if event.delta:
            scroll = self.board_canvas.xview_scroll if horizontal else self.board_canvas.yview_scroll
            scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def _request_ai(self, *, force_local: bool = False) -> None:
        if (self._closed or self._active_page != "gomoku" or self.game.finished
                or self.game.turn != 2 or self._ai_busy
                or not self._pages["gomoku"].winfo_ismapped()):
            return
        self._ai_busy = True
        self._game_generation += 1
        generation = self._game_generation
        self._ai_due_at = time.monotonic() + random.uniform(2.0, 5.0)
        self.game_status.set("雷姆正在考虑这一步…")
        if not force_local and self.model_opponent.get() and self.on_model_request is not None:
            # 工作线程只写 Queue，不跨线程操作 Tk 控件。
            callback = lambda text: self._model_responses.put((generation, str(text)))
            self._model_timeout_id = self.after(45000, lambda: self._finish_ai(generation, ""))
            try:
                self.on_model_request(self.game.model_prompt(), callback)
            except Exception:
                self._finish_ai(generation, "")
        else:
            if not force_local and self.model_opponent.get():
                self.status.set("当前聊天未接入独立基模回调，已使用本地对手。")
            self._finish_ai(generation, "")

    def _finish_ai(self, generation: int, text: str) -> None:
        if (self._closed or generation != self._game_generation or not self._ai_busy
                or self._active_page != "gomoku" or self._ai_local_pending):
            return
        if self._model_timeout_id is not None:
            self.after_cancel(self._model_timeout_id)
            self._model_timeout_id = None
        move = self.game.parse_model_move(text) if text else None
        if move is None:
            if text:
                self.status.set("基模没有给出有效空位，本地对手已接续落子。")
            self._start_local_move(generation)
            return
        self._apply_ai_move(generation, move)

    def _start_local_move(self, generation: int) -> None:
        """Compute an isolated board in a worker; wait only through Tk timers."""
        self._ai_local_pending = True
        snapshot = self.game.snapshot()
        replies = self._local_responses

        def calculate() -> None:
            try:
                move = GomokuGame.restore(snapshot).choose_move()
                replies.put((generation, move, False))
            except Exception:
                replies.put((generation, None, True))

        threading.Thread(target=calculate, name=f"rem-gomoku-{generation}", daemon=True).start()
        self._local_poll_id = self.after(33, self._poll_local_move)

    def _poll_local_move(self) -> None:
        self._local_poll_id = None
        if self._closed or not self._ai_busy or not self._ai_local_pending:
            return
        while True:
            try:
                generation, move, failed = self._local_responses.get_nowait()
            except queue.Empty:
                self._local_poll_id = self.after(33, self._poll_local_move)
                return
            if generation != self._game_generation:
                continue
            if failed:
                self._ai_busy = False
                self._ai_local_pending = False
                self.game_status.set("本地对手暂时无法落子；请重读棋局后继续。")
                return
            delay_ms = max(0, math.ceil((self._ai_due_at - time.monotonic()) * 1000))
            self._ai_timer_id = self.after(delay_ms, lambda: self._apply_ai_move(generation, move))
            return

    def _apply_ai_move(self, generation: int, move: tuple[int, int] | None) -> None:
        if (self._closed or generation != self._game_generation or not self._ai_busy
                or self._active_page != "gomoku"):
            return
        self._ai_timer_id = None
        self._ai_busy = False
        self._ai_local_pending = False
        if move is not None and self.game.turn == 2 and self.game.legal(*move):
            saved, _ = self._commit_activity("gomoku", lambda game: game.play(*move))
            if not saved:
                self.game_status.set("落子未保存；请重读存档后继续。")
                return
        self._draw_board()
        self._update_game_status()

    def _cancel_ai(self) -> None:
        self._game_generation += 1
        self._ai_busy = False
        self._ai_local_pending = False
        for name in ("_ai_timer_id", "_model_timeout_id", "_local_poll_id"):
            timer = getattr(self, name)
            if timer is not None:
                self.after_cancel(timer)
                setattr(self, name, None)

    def _update_game_status(self) -> None:
        if self.game.winner:
            self.game_status.set("你赢啦！再来一盘吗？" if self.game.winner == 1 else "雷姆赢了这局。下一盘加油！")
        elif self.game.finished:
            self.game_status.set("这盘是和棋。")
        else:
            self.game_status.set(f"{self.game.size}×{self.game.size} · 第 {len(self.game.moves) + 1} 手 · " + ("轮到你了" if self.game.turn == 1 else "雷姆思考中…"))

    def _new_game(self) -> None:
        if self.game.moves and not self.game.finished:
            if not messagebox.askyesno("新开一盘", "当前棋局尚未结束，确定重新开始吗？", parent=self):
                return
        saved, _ = self._commit_activity("gomoku", lambda _game: None, candidate=GomokuGame())
        if not saved:
            return
        self._cancel_ai()
        self._update_game_status()
        self._draw_board()
        self.record_activity()

    def _undo_game(self) -> None:
        saved, _ = self._commit_activity("gomoku", lambda game: game.undo_round())
        if not saved:
            return
        self._cancel_ai()
        self._update_game_status()
        self._draw_board()
        self.record_activity()

    def _hint_game(self) -> None:
        if self.game.turn != 1 or self._ai_busy or self.game.finished:
            self.status.set("请等轮到你时再查看提示。")
            return
        move = self.game.choose_move()
        if move is not None:
            self.status.set(f"可以考虑第 {move[0] + 1} 行、第 {move[1] + 1} 列。")
            x0, y0, step = self._board_geometry
            x, y = x0 + move[1] * step, y0 + move[0] * step
            radius = step * 0.4
            self.board_canvas.create_oval(x - radius, y - radius, x + radius, y + radius,
                                          outline=GREEN, width=2, dash=(3, 2), tags="hint")
        self.record_activity()

    def _explain_game(self) -> None:
        prompt = self.game.model_prompt().replace(
            '只输出 JSON：{"row": 行号, "column": 列号}。',
            "请用中文简短讲解当前棋局，用户执黑 X。指出黑方的主要机会和风险，"
            "给出最多两个候选坐标，不要声称已实际落子。",
        )
        self.on_chat_prompt(prompt)
        self.record_activity()

    def _build_garden(self, parent: tk.Frame) -> None:
        stage, controls = self._activity_layout(parent)
        from .pixel_farm import PixelFarm
        self.pixel_farm = PixelFarm(stage, self)
        self.pixel_farm.pack(fill="both", expand=True)
        self.garden_canvas = self.pixel_farm.canvas
        self._label(controls, "雷姆的小农场", size=16, bold=True).pack(anchor="w", pady=(0, 12))
        self.plot_status = tk.StringVar(self)
        tk.Label(controls, textvariable=self.plot_status, bg=WHITE, fg=GREEN,
                 font=(FONT, 11, "bold"), wraplength=self._control_width - 12, justify="left").pack(anchor="w", pady=(0, 14))
        self.seed = self.pixel_farm.crop
        self._label(controls, "选择种子", bold=True).pack(anchor="w")
        seeds=tk.Frame(controls,bg=WHITE);seeds.pack(fill='x',pady=(4,0))
        seed_buttons=[]
        for crop, data in CROPS.items():
            button=tk.Radiobutton(seeds, text=f"{data.name} · {data.minutes} 分钟", variable=self.seed,
                           value=crop, bg=WHITE, activebackground=WHITE, selectcolor=SOFT,
                           fg=INK, font=(FONT, 10),anchor='w',takefocus=True)
            seed_buttons.append(button)
        def seed_layout(event):
            columns=2 if event.width>=max(button.winfo_reqwidth() for button in seed_buttons)*2+12 else 1
            for column in (0,1):seeds.columnconfigure(column,weight=1 if column<columns else 0)
            for index,button in enumerate(seed_buttons):button.grid(row=index//columns,column=index%columns,sticky='ew',pady=2,padx=(0,5))
        seeds.bind('<Configure>',seed_layout)
        for index,button in enumerate(seed_buttons):button.grid(row=index,column=0,sticky='ew',pady=2)
        self._button(controls, "互动 / 使用工具 · 空格", self.pixel_farm.interact, primary=True).pack(fill="x", pady=(16, 4))
        pad = tk.Frame(controls, bg=WHITE)
        pad.pack(pady=8)
        for label, direction, row, column in (("↑", "up", 0, 1), ("←", "left", 1, 0),
                                              ("↓", "down", 1, 1), ("→", "right", 1, 2)):
            tk.Button(pad, text=label, command=lambda d=direction: self.pixel_farm.move(d),
                      bg=SOFT, fg=INK, bd=0, padx=12, pady=9, font=(FONT, 11)).grid(row=row, column=column, padx=3, pady=3)
        self._label(controls, "中央整片草地可连续耕种，不限于原来的九块地。\n点击草地走到它前面；方向键 / WASD 移动。\n1–4 选工具，空格 / E 互动。\n左下水井、右上池塘码头都能装满水壶；入口小门按 E 开关。\n树干、石头、围栏与水面不能穿过，木桥可以通行。\n默认地图铺满并跟随雷姆；全图视角可查看整个农场。\n作物按原有真实时间规则成长，关闭软件也不重置。",
                    size=9, color=MUTED, justify="left").pack(anchor="w", pady=14)
        self.harvest_status = tk.StringVar(self)
        tk.Label(controls, textvariable=self.harvest_status, bg=WHITE, fg=MUTED,
                 font=(FONT, 10), wraplength=self._control_width - 12, justify="left").pack(anchor="w")

    def _draw_garden(self) -> None:
        if self._closed:
            return
        self.pixel_farm.draw()
        plot = self.garden.plots[self._selected_plot]
        if not plot:
            text = f"第 {self._selected_plot + 1} 块地 · 空地"
        elif not plot["watered"]:
            text = f"{CROPS[str(plot['crop'])].name} · 等你浇水"
        else:
            progress = self.garden.progress(self._selected_plot)
            remaining = max(0, CROPS[str(plot["crop"])].minutes * 60 - (time.time() - float(plot["planted"])))
            text = f"{CROPS[str(plot['crop'])].name} · " + ("成熟啦！" if progress >= 1 else f"还需 {int(remaining // 60)} 分 {int(remaining % 60)} 秒")
        self.plot_status.set(text)
        total = sum(self.garden.harvests.values())
        details = " · ".join(f"{CROPS[key].name} {value}" for key, value in self.garden.harvests.items())
        self.harvest_status.set(f"累计收获 {total} 次\n{details}" if details else "还没有收获。第一颗种子正等着你。")

    def _garden_action(self, action: Callable[[Garden], object], message: str) -> None:
        try:
            saved, _ = self._commit_activity("garden", action)
            if not saved:
                return
            self.status.set(message)
            self._draw_garden()
            self.record_activity()
        except ValueError as exc:
            self.status.set(str(exc))

    def _plant(self) -> None:
        self._garden_action(lambda garden: garden.plant(self._selected_plot, self.seed.get()), "种下啦，别忘记浇水。")

    def _water(self) -> None:
        self._garden_action(lambda garden: garden.water(self._selected_plot), "浇好水啦，静静等它长大。")

    def _harvest(self) -> None:
        self._garden_action(lambda garden: garden.harvest(self._selected_plot), "收获完成！这块地又可以播种了。")

    def _build_words(self, parent: tk.Frame) -> None:
        from .word_learning import WordLearningPanel
        self.word_learning = WordLearningPanel(
            parent, self.data_path.parent.parent, get_deck=lambda: self.words,
            commit=lambda change: self._commit_activity("words", change),
            on_explain=self.on_chat_prompt, on_status=lambda text: self.status.set(text),
            on_model_request=self.on_model_request,
        )
        self.word_learning.pack(fill="both", expand=True)

    def _draw_word(self) -> None:
        self.word_learning.refresh()

    def _build_companion(self, parent: tk.Frame) -> None:
        viewport = ScrollCanvas(parent, bg=WHITE, highlightthickness=0)
        scrollbar = tk.Scrollbar(parent, orient="vertical", command=viewport.yview)
        scrollbar.pack(side="right", fill="y")
        viewport.pack(fill="both", expand=True)
        viewport.configure(yscrollcommand=scrollbar.set)
        frame = tk.Frame(viewport, bg=WHITE)
        item = viewport.create_window(30, 26, window=frame, anchor="nw")
        frame.bind("<Configure>", lambda _: viewport.configure(scrollregion=viewport.bbox("all")))
        viewport.bind("<Configure>", lambda event: viewport.itemconfigure(item, width=max(100, event.width - 60)))
        self.after_idle(lambda: self._bind_control_wheel(viewport, frame))
        self._label(frame, "你需要的时候，雷姆才来打扰", size=20, bold=True).pack(anchor="w", pady=(0, 12))
        self._label(frame, "轻提醒默认关闭。启用后按空闲间隔出现；安静时段自动暂停。",
                    color=MUTED).pack(anchor="w", pady=(0, 24))
        settings = self.schedule.settings
        self.companion_enabled = tk.BooleanVar(self, settings.enabled)
        tk.Checkbutton(frame, text="开启主动陪伴", variable=self.companion_enabled,
                       bg=WHITE, fg=INK, activebackground=WHITE, selectcolor=SOFT,
                       font=(FONT, 12, "bold")).pack(anchor="w", pady=(0, 18))
        fields = tk.Frame(frame, bg=WHITE)
        fields.pack(anchor="w")
        self.interval = tk.StringVar(self, str(settings.interval_minutes))
        self.quiet_start = tk.StringVar(self, settings.quiet_start)
        self.quiet_end = tk.StringVar(self, settings.quiet_end)
        self._label(fields, "空闲多久互动一次").grid(row=0, column=0, sticky="w", pady=10, padx=(0, 20))
        SoftSpinbox(fields, from_=5, to=240, width=7, textvariable=self.interval,
                   font=(FONT, 11), bg=BG, fg=INK).grid(row=0, column=1, sticky="w")
        self._label(fields, "分钟（5–240）", color=MUTED).grid(row=0, column=2, sticky="w", padx=12)
        self._label(fields, "安静时段").grid(row=1, column=0, sticky="w", pady=10)
        SoftEntry(fields, textvariable=self.quiet_start, width=7, font=(FONT, 11),
                 bg=BG, fg=INK).grid(row=1, column=1, sticky="w")
        end = tk.Frame(fields, bg=WHITE)
        end.grid(row=1, column=2, sticky="w", padx=12)
        self._label(end, "至").pack(side="left", padx=(0, 12))
        SoftEntry(end, textvariable=self.quiet_end, width=7, font=(FONT, 11),
                 bg=BG, fg=INK).pack(side="left")
        self._label(frame, "使用本机时间，支持跨午夜。开始和结束相同表示不设置安静时段。\n"
                    "提醒只用轻量本地文案，不会在无人操作时反复调用大模型。",
                    color=MUTED, size=10, justify="left").pack(anchor="w", pady=20)
        buttons = tk.Frame(frame, bg=WHITE)
        buttons.pack(anchor="w")
        self._button(buttons, "保存陪伴设置", self._save_companion, primary=True).pack(side="left", padx=(0, 12))
        self._button(buttons, "体验一次提醒", self._preview_notification).pack(side="left")
        self.companion_status = tk.StringVar(self)
        tk.Label(frame, textvariable=self.companion_status, bg=WHITE, fg=GREEN,
                 font=(FONT, 11), anchor="w", wraplength=700, justify="left").pack(fill="x", pady=24)

    def _save_companion(self) -> None:
        try:
            settings = CompanionSettings(self.companion_enabled.get(), int(self.interval.get()),
                                          self.quiet_start.get().strip(), self.quiet_end.get().strip())
            settings.validate()
        except (ValueError, tk.TclError) as exc:
            self.status.set(str(exc))
            return
        if self._save("companion", settings.snapshot()):
            self.schedule.settings = settings
            self.schedule.record_activity()
            self.status.set("陪伴设置已保存。")
            self._update_companion_status()

    def _update_companion_status(self) -> None:
        settings = self.schedule.settings
        if not settings.enabled:
            self.companion_status.set("当前：主动陪伴已关闭。你随时可以回来找雷姆。")
        elif settings.is_quiet():
            self.companion_status.set(f"当前处于安静时段 {settings.quiet_start}–{settings.quiet_end}。")
        else:
            self.companion_status.set(f"已开启：空闲 {settings.interval_minutes} 分钟后轻轻提醒一次。")

    def _preview_notification(self) -> None:
        self._notify()
        self.record_activity()

    def _notify(self, *, passive: bool = False) -> None:
        messages = (
            "等得有点久了吗？雷姆陪你下一步棋，或看看花园吧。",
            "辛苦啦。可以放松一下肩膀，喝口水，雷姆会陪着你。",
            "要不要认识一个新单词？不着急，一张卡片就好。",
            "工作还在进行的话，就先休息片刻吧。回来时我们再继续。",
        )
        text = messages[self._notification_index % len(messages)]
        self._notification_index += 1
        callback = self.on_auto_notify if passive else self.on_notify
        callback(text)

    def set_model_responder(self, responder: ModelRequest | None) -> None:
        self.on_model_request = responder

    def record_activity(self) -> None:
        self.schedule.record_activity()

    def start_companion(self) -> None:
        """可重复调用，不创建重复提醒任务。"""
        if not self._closed and self._timer_id is None:
            self._timer_id = self.after(500, self._tick)

    def stop_companion(self) -> None:
        if self._timer_id is not None:
            self.after_cancel(self._timer_id)
            self._timer_id = None

    def _tick(self) -> None:
        self._timer_id = None
        if self._closed:
            return
        while True:
            try:
                generation, text = self._model_responses.get_nowait()
            except queue.Empty:
                break
            self._finish_ai(generation, text)
        if self._ai_busy or not self.can_notify():
            # Busy interaction is not idle time. Start a full interval after
            # it finishes instead of delivering a queued nudge immediately.
            self.schedule.record_activity()
        elif self.schedule.due():
            self.schedule.record_activity()
            self._notify(passive=True)
        now = time.monotonic()
        if now - self._last_garden_refresh >= 10:
            self._last_garden_refresh = now
            if self._active_page == "garden":
                self._draw_garden()
            self._update_companion_status()
        self._timer_id = self.after(500, self._tick)

    def _open_data(self) -> None:
        from ..platforms import native_module
        try:
            native_module("shell").open_directory(self.data_path)
        except OSError as exc:
            messagebox.showerror("无法打开目录", f"{self.data_path}\n{exc}", parent=self)

    def refresh(self) -> None:
        self._update_game_status()
        self._draw_word()
        self._update_companion_status()
        self._draw_board()
        self._draw_garden()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            self._closed = True
            self.stop_companion()
            self._cancel_ai()

    def destroy(self) -> None:
        self._closed = True
        self.stop_companion()
        self._cancel_ai()
        self.word_learning.close()
        if self.project_workspace is not None:
            self.project_workspace.close()
        super().destroy()
