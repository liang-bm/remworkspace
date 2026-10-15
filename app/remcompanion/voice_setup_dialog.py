"""User-started voice setup, shared by every playback switch.

Workers only publish queue events. They never touch Tk variables or widgets;
readiness is checked before playback is enabled, not during application startup.
"""
from __future__ import annotations

from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from urllib.parse import urlsplit
from typing import Callable

from .platforms import native_module
from .ui import theme
from .ui.diary_widgets import _frame, _label, _Toolbar
from .ui.dpi import pixels
from .ui.rounded_controls import RoundedChrome
from .ui.scroll_surface import ScrollCanvas, install_scroll_repaint
from .ui.theme_controller import set_color_roles


_COPY = {
    "title": ("开启语音", "Enable speech", "音声を有効にする"),
    "heading": ("让回复读出来", "Hear your replies", "返信を音声で聞く"),
    "intro": (
        "先准备本地语音环境，再导入小型推理包。文字聊天和桌宠现在就能用，不必开启语音。",
        "Install the local speech environment, then import the small inference pack. Text chat and your companion work without speech.",
        "ローカル音声環境を用意して、小さな推論パックを読み込みます。音声なしでもチャットやデスクトップペットは使えます。",
    ),
    "environment": ("1 · 安装语音环境", "1 · Install the speech environment", "1 · 音声環境をインストール"),
    "environment_help": (
        "由你点击后下载 GPT-SoVITS 和基础模型。第一次需要网络、磁盘空间和一些时间。",
        "Only this button downloads GPT-SoVITS and its base models. First-time setup needs internet access, disk space and some time.",
        "このボタンを押したときだけ GPT-SoVITS と基礎モデルをダウンロードします。初回はネット接続、空き容量と時間が必要です。",
    ),
    "cpu": ("CPU · 兼容优先", "CPU · widest compatibility", "CPU · 互換性を優先"),
    "cuda": ("NVIDIA GPU · CUDA 12.8", "NVIDIA GPU · CUDA 12.8", "NVIDIA GPU · CUDA 12.8"),
    "device_help": (
        "不确定就选 CPU。GPU 选项需要支持 CUDA 12.8 的 NVIDIA 驱动。",
        "Choose CPU if unsure. The GPU option needs an NVIDIA driver that supports CUDA 12.8.",
        "迷ったら CPU を選んでください。GPU には CUDA 12.8 対応の NVIDIA ドライバーが必要です。",
    ),
    "install": ("安装环境", "Install environment", "環境をインストール"),
    "ffmpeg": ("获取 FFmpeg", "Get FFmpeg", "FFmpeg を入手"),
    "ffmpeg_windows": (
        "先准备 FFmpeg：从官网获取 Windows 版，把解压后的 bin 文件夹放到软件的 engines/ffmpeg/bin；同目录的 DLL 和许可证也要保留。安装语音环境前会检查它，不会自动下载 FFmpeg。",
        "Prepare FFmpeg first: get a Windows build from the official site and place its extracted bin folder at engines/ffmpeg/bin inside the app. Keep the accompanying DLLs and licenses. Setup checks FFmpeg before downloading the speech environment; it does not download FFmpeg for you.",
        "先に FFmpeg を用意します。公式サイトから Windows 版を入手し、解凍した bin フォルダーをアプリの engines/ffmpeg/bin に置いてください。同梱 DLL とライセンスも残します。音声環境のダウンロード前に確認され、FFmpeg 自体は自動取得されません。",
    ),
    "ffmpeg_linux": (
        "先准备 FFmpeg：Ubuntu / Debian 可在终端运行 sudo apt install ffmpeg；其他 Linux 请使用系统包管理器。安装前会检查 FFmpeg，不会替你运行 sudo。",
        "Prepare FFmpeg first. On Ubuntu / Debian, run sudo apt install ffmpeg in a terminal; on other Linux distributions, use the system package manager. Setup checks FFmpeg but never runs sudo for you.",
        "先に FFmpeg を用意します。Ubuntu / Debian では端末で sudo apt install ffmpeg を実行し、その他の Linux ではシステムのパッケージ管理を使ってください。事前確認のみ行い、sudo は自動実行しません。",
    ),
    "pack": ("2 · 导入语音推理包", "2 · Import the voice inference pack", "2 · 音声推論パックを読み込む"),
    "pack_help": (
        "只需要发布者提供的推理包：训练好的权重、参考音频和对应文字。如果是分卷，把全部分卷和 .voice-parts.json 清单下载到同一个文件夹，再选择清单导入；不要手动解压。不要导入完整训练数据集或账号配置。",
        "Use the inference pack with trained weights, reference audio and its transcript. For a split download, keep all parts and the .voice-parts.json manifest in one folder, then import the manifest. Do not extract it yourself or import the training dataset or account settings.",
        "学習済み重み、参照音声と書き起こしが入った推論パックを使います。分割配布の場合は全パーツと .voice-parts.json を同じフォルダーに保存し、その一覧ファイルを読み込みます。手動解凍や学習データ一式・アカウント設定の取り込みは不要です。",
    ),
    "materials": ("打开 Google Drive 资料", "Open Google Drive materials", "Google Drive の資料を開く"),
    "no_link": (
        "资料链接尚未配置。你仍可导入自己有权使用的推理包。",
        "The materials link has not been configured. You can still import an inference pack you have permission to use.",
        "資料リンクはまだ設定されていません。使用許可のある推論パックは読み込めます。",
    ),
    "folder": ("打开下载文件夹", "Open Downloads", "ダウンロードフォルダーを開く"),
    "import": ("导入推理包", "Import inference pack", "推論パックを読み込む"),
    "choose": ("选择语音推理包 ZIP 或分卷清单", "Choose an inference ZIP or parts manifest", "推論 ZIP または分割一覧を選ぶ"),
    "check": ("重新检查", "Check again", "再確認"),
    "enable": ("开启语音", "Enable speech", "音声を有効にする"),
    "later": ("暂不开启", "Not now", "今は使わない"),
    "hide": ("隐藏进度 · 语音保持关闭", "Hide progress · keep speech off", "進捗を隠す · 音声はオフのまま"),
    "busy_help": (
        "关闭此窗口不会取消正在运行的安装或导入；任务会继续，语音仍保持关闭。再次点击播放可回来查看。",
        "Closing this window does not cancel an active install or import. It continues with speech off. Click playback again to return.",
        "この画面を閉じても実行中のインストールや読み込みは続きます。音声はオフのままです。再生ボタンで戻れます。",
    ),
    "checking": ("正在检查语音文件…", "Checking speech files…", "音声ファイルを確認中…"),
    "working": ("正在准备，请稍候…", "Preparing speech, please wait…", "音声を準備しています…"),
    "ready": ("文件已就绪，可以开启语音。", "The files are ready. You can enable speech.", "ファイルが揃いました。音声を有効にできます。"),
    "missing": ("还需要准备：", "Still needed:", "あと必要なもの："),
    "error": (
        "这一步没有完成。查看下方详情后可以重试；文字聊天不受影响。",
        "This step did not finish. Review the details below and retry; text chat is unaffected.",
        "この手順は完了しませんでした。下の詳細を確認して再試行できます。テキストチャットには影響しません。",
    ),
    "enabled": ("自动语音已开启", "Speech playback is on", "音声再生を有効にしました"),
    "pending": ("语音尚未开启 · 请先完成语音准备", "Speech stays off until setup is ready", "準備が整うまで音声はオフです"),
    "logs": ("准备进度", "Setup progress", "準備の進捗"),
}

_MISSING_COPY = {
    "FFmpeg 音频工具": ("FFmpeg audio tools", "FFmpeg 音声ツール"),
    "语音引擎运行环境": ("Speech runtime", "音声ランタイム"),
    "语音引擎版本": ("Compatible speech engine version", "対応する音声エンジンのバージョン"),
    "语音环境配置": ("Speech environment configuration", "音声環境の設定"),
    "GPT-SoVITS 引擎": ("GPT-SoVITS engine", "GPT-SoVITS エンジン"),
    "基础语音模型": ("Base speech models", "基礎音声モデル"),
    "中文 / 日文语言资源": ("Chinese / Japanese language resources", "中国語・日本語の言語リソース"),
    "GPT 声线权重": ("Trained GPT voice weights", "学習済み GPT 音声重み"),
    "SoVITS 声线权重": ("Trained SoVITS voice weights", "学習済み SoVITS 音声重み"),
    "声线参考音频": ("Voice reference audio", "音声の参照サンプル"),
    "参考音频文字": ("Reference audio transcript", "参照音声の書き起こし"),
    "已验证的声线素材包": ("Verified voice inference pack", "検証済み音声推論パック"),
    "本机安全密钥或声线权限": ("Local secure storage or voice permission", "ローカルの安全な保存領域・音声の使用許可"),
}


_HANT = {
    'title': '開啟語音', 'heading': '讓回覆讀出來',
    'intro': '先準備本機語音環境，再匯入小型推論包。文字聊天和桌寵現在就能使用，不必開啟語音。',
    'environment': '1 · 安裝語音環境',
    'environment_help': '由你點擊後下載 GPT-SoVITS 和基礎模型。第一次需要網路、磁碟空間和一些時間。',
    'cpu': 'CPU · 相容性優先', 'cuda': 'NVIDIA GPU · CUDA 12.8',
    'device_help': '不確定就選 CPU。GPU 選項需要支援 CUDA 12.8 的 NVIDIA 驅動程式。',
    'install': '安裝環境', 'ffmpeg': '取得 FFmpeg',
    'ffmpeg_windows': '先準備 FFmpeg：從官網取得 Windows 版，把解壓縮後的 bin 資料夾放到軟體的 engines/ffmpeg/bin；同目錄的 DLL 和授權文件也要保留。安裝語音環境前會檢查它，不會自動下載 FFmpeg。',
    'ffmpeg_linux': '先準備 FFmpeg：Ubuntu / Debian 可在終端機執行 sudo apt install ffmpeg；其他 Linux 請使用系統套件管理器。安裝前會檢查 FFmpeg，不會替你執行 sudo。',
    'pack': '2 · 匯入語音推論包',
    'pack_help': '只需要發布者提供的推論包：訓練好的權重、參考音訊和對應文字。如果是分卷，把全部分卷和 .voice-parts.json 清單下載到同一個資料夾，再選擇清單匯入；不要手動解壓縮。不要匯入完整訓練資料集或帳戶設定。',
    'materials': '開啟 Google Drive 資料',
    'no_link': '資料連結尚未設定。你仍可匯入自己有權使用的推論包。',
    'folder': '開啟下載資料夾', 'import': '匯入推論包',
    'choose': '選擇語音推論包 ZIP 或分卷清單', 'check': '重新檢查',
    'enable': '開啟語音', 'later': '暫不開啟', 'hide': '隱藏進度 · 語音保持關閉',
    'busy_help': '關閉此視窗不會取消正在執行的安裝或匯入；工作會繼續，語音仍保持關閉。再次點擊播放可回來查看。',
    'checking': '正在檢查語音檔案…', 'working': '正在準備，請稍候…',
    'ready': '檔案已就緒，可以開啟語音。', 'missing': '還需要準備：',
    'error': '這一步沒有完成。查看下方詳細資訊後可以重試；文字聊天不受影響。',
    'enabled': '自動語音已開啟', 'pending': '語音尚未開啟 · 請先完成語音準備',
    'logs': '準備進度',
}
_MISSING_HANT = {
    'FFmpeg 音频工具': 'FFmpeg 音訊工具', '语音引擎运行环境': '語音引擎執行環境',
    '语音引擎版本': '語音引擎版本', '语音环境配置': '語音環境設定',
    'GPT-SoVITS 引擎': 'GPT-SoVITS 引擎', '基础语音模型': '基礎語音模型',
    '中文 / 日文语言资源': '中文 / 日文語言資源', 'GPT 声线权重': 'GPT 聲線權重',
    'SoVITS 声线权重': 'SoVITS 聲線權重', '声线参考音频': '聲線參考音訊',
    '参考音频文字': '參考音訊文字', '已验证的声线素材包': '已驗證的聲線素材包',
    '本机安全密钥或声线权限': '本機安全金鑰或聲線權限',
}


def _text(key: str, language: str) -> str:
    if language == 'zh-Hant':
        return _HANT[key]
    return _COPY[key][1 if language == "en" else 2 if language == "ja" else 0]


def _material_url() -> str:
    from .voice_setup import material_download_url

    value = material_download_url().strip()
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.hostname != "drive.google.com"
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not parsed.path.startswith(("/file/d/", "/drive/folders/", "/open"))):
        return ""
    return value


class VoiceSetupDialog:
    """One non-modal guide. A hidden active job can be reopened, not duplicated."""

    def __init__(self, parent: tk.Misc, config, readiness: dict, *,
                 language: str, on_ready: Callable[[], None],
                 on_close: Callable[[], None]) -> None:
        self.config = config
        self.language = language
        self.on_ready = on_ready
        self.on_close = on_close
        self._events: queue.SimpleQueue = queue.SimpleQueue()
        self._closed = False
        self._dismissed = False
        self._busy = False
        self._poll_job = None
        self._layout_job = None
        self._readiness = readiness
        self.window = tk.Toplevel(parent)
        self.window.title(self.tr("title"))
        set_color_roles(self.window, background="WHITE")
        self.window.protocol("WM_DELETE_WINDOW", self.dismiss)
        self.window.bind("<Escape>", lambda _event: self.dismiss())
        if parent.winfo_viewable():
            self.window.transient(parent.winfo_toplevel())
        width = min(pixels(self.window, 690), self.window.winfo_screenwidth() - 40)
        height = min(pixels(self.window, 680), self.window.winfo_screenheight() - 80)
        self.window.geometry(f"{width}x{height}")
        self.window.minsize(min(width, pixels(self.window, 480)), min(height, pixels(self.window, 480)))
        self._build()
        self._chrome = RoundedChrome(self.window)
        self._repaint = install_scroll_repaint(self.window)
        self._apply_readiness(readiness)
        self._poll_job = self.window.after(60, self._poll)
        self.show()

    def tr(self, key: str) -> str:
        return _text(key, self.language)

    def set_language(self, language: str) -> None:
        if self._closed or language == self.language:
            return
        previous = self.language
        replacements = {_text(key, previous): _text(key, language) for key in _COPY}
        self.language = language
        self.window.title(self.tr('title'))
        def visit(widget):
            if isinstance(widget, (tk.Label, tk.Button, tk.Radiobutton)):
                text = str(widget.cget('text'))
                if text in replacements:
                    widget.configure(text=replacements[text])
            for child in widget.winfo_children():
                visit(child)
        visit(self.window)
        status = self.status.get()
        if status in replacements:
            self.status.set(replacements[status])
        elif status.startswith(_text('missing', previous)):
            self._apply_readiness(self._readiness)


    def _build(self) -> None:
        # Actions stay reachable on short displays; content uses the same
        # coalesced native repaint path as the application's scrolling pages.
        footer = _frame(self.window)
        footer.pack(side="bottom", fill="x", padx=22, pady=(4, 14))
        shell = _frame(self.window)
        shell.pack(fill="both", expand=True, padx=22, pady=(18, 4))
        self.canvas = ScrollCanvas(shell, bd=0, highlightthickness=0, width=1)
        set_color_roles(self.canvas, background="WHITE")
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = body = _frame(self.canvas)
        self._body_item = self.canvas.create_window(0, 0, anchor="nw", window=body)
        self.canvas.bind("<Configure>", self._schedule_layout, add=True)
        body.bind("<Configure>", self._schedule_layout, add=True)
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.window.bind(sequence, self._wheel, add=True)
        self.window.bind("<FocusIn>", self._reveal_focus, add=True)
        _label(body, self.tr("heading"), size=19, bold=True).pack(anchor="w")
        self._wrapped_labels = []

        def wrapped(text: str, *, muted=True):
            label = _label(body, text, muted=muted)
            label.configure(justify="left", wraplength=600)
            label.pack(fill="x", pady=(6, 10))
            self._wrapped_labels.append(label)
            return label

        wrapped(self.tr("intro"))
        _label(body, self.tr("environment"), size=12, bold=True).pack(anchor="w", pady=(8, 0))
        wrapped(self.tr("environment_help"))
        device_row = _frame(body)
        device_row.pack(fill="x")
        self.device = tk.StringVar(self.window, "cpu")
        self._device_buttons = []
        for value in ("cpu", "cuda"):
            button = tk.Radiobutton(device_row, text=self.tr(value), value=value,
                                    variable=self.device, relief="flat", bd=0,
                                    font=(theme.BODY_FONT, 10), takefocus=True)
            set_color_roles(button, background="WHITE", foreground="INK",
                            activebackground="WHITE", selectcolor="WHITE")
            button.pack(anchor="w")
            self._device_buttons.append(button)
        wrapped(self.tr("device_help"))
        wrapped(self.tr("ffmpeg_linux" if sys.platform.startswith("linux") else "ffmpeg_windows"))
        environment_actions = _Toolbar(body)
        environment_actions.pack(fill="x", pady=(0, 12))
        self.install_button = environment_actions.add(self.tr("install"), self.install, primary=True)
        environment_actions.add(self.tr("ffmpeg"), self.open_ffmpeg)
        _label(body, self.tr("pack"), size=12, bold=True).pack(anchor="w")
        wrapped(self.tr("pack_help"))
        materials = _Toolbar(body)
        materials.pack(fill="x")
        self.material_button = materials.add(self.tr("materials"), self.open_materials)
        materials.add(self.tr("folder"), self.open_downloads)
        self.import_button = materials.add(self.tr("import"), self.choose_pack, primary=True)
        try:
            self._url = _material_url()
        except (OSError, ValueError, TypeError):
            self._url = ""
        if not self._url:
            self.material_button.configure(state="disabled")
            wrapped(self.tr("no_link"))
        self.status = tk.StringVar(self.window)
        self.status_label = wrapped("", muted=False)
        self.status_label.configure(textvariable=self.status)
        _label(body, self.tr("logs"), muted=True).pack(anchor="w")
        self.log_box = tk.Text(body, wrap="word", height=4, relief="flat", bd=0,
                               font=(theme.BODY_FONT, 9), state="disabled", takefocus=True)
        set_color_roles(self.log_box, background="APP_BG", foreground="MUTED",
                        selectbackground="ACCENT_SOFT", selectforeground="INK")
        self.log_box.pack(fill="x", pady=(5, 8))
        self.busy_label = wrapped(self.tr("busy_help"))
        actions = _Toolbar(footer)
        actions.pack(fill="x")
        self.check_button = actions.add(self.tr("check"), self.check)
        self.enable_button = actions.add(self.tr("enable"), self.enable, primary=True)
        self.dismiss_button = actions.add(self.tr("later"), self.dismiss)
        body.bind("<Configure>", lambda event: [label.configure(wraplength=max(200, event.width))
                                               for label in self._wrapped_labels], add=True)

    def _schedule_layout(self, _event=None) -> None:
        if self._layout_job is None:
            self._layout_job = self.window.after_idle(self._layout)

    def _layout(self) -> None:
        self._layout_job = None
        width = max(1, self.canvas.winfo_width())
        self.canvas.itemconfigure(self._body_item, width=width)
        self.canvas.configure(scrollregion=(0, 0, width, self.body.winfo_reqheight()))

    def _wheel(self, event):
        widget = event.widget
        if isinstance(widget, (tk.Text, tk.Listbox, ttk.Treeview, tk.Scrollbar, ttk.Scrollbar)):
            return None
        while widget is not None:
            if widget is self.body or widget is self.canvas:
                upward = getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0
                self.canvas.yview_scroll(-3 if upward else 3, "units")
                return "break"
            widget = getattr(widget, "master", None)
        return None

    def _reveal_focus(self, event) -> None:
        widget = event.widget
        owner = widget
        while owner is not None and owner is not self.body:
            owner = getattr(owner, "master", None)
        if owner is None or not widget.winfo_viewable():
            return
        height = max(1, self.body.winfo_height())
        y = widget.winfo_rooty() - self.body.winfo_rooty()
        first, last = self.canvas.yview()
        if y < first * height:
            self.canvas.yview_moveto(y / height)
        elif y + widget.winfo_height() > last * height:
            self.canvas.yview_moveto(max(0, (y + widget.winfo_height() - self.canvas.winfo_height()) / height))

    def show(self) -> None:
        if not self._closed:
            self._dismissed = False
            self.window.deiconify()
            self.window.lift()
            self.window.focus_set()

    def _apply_readiness(self, readiness: dict) -> None:
        self._readiness = readiness
        ready = readiness.get("ready") is True
        missing = []
        for value in readiness.get("missing", []):
            label = str(value)
            translations = _MISSING_COPY.get(label)
            if self.language == 'zh-Hant':
                label = _MISSING_HANT.get(label, label)
            elif translations and self.language in {"en", "ja"}:
                label = translations[0 if self.language == "en" else 1]
            missing.append(label)
        self.status.set(self.tr("ready") if ready else self.tr("missing") + "\n" + " · ".join(missing))
        self.enable_button.configure(state="normal" if ready and not self._busy else "disabled")

    def _log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", message[:4000] + "\n")
        if int(self.log_box.index("end-1c").split(".")[0]) > 220:
            self.log_box.delete("1.0", "40.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _start(self, operation: Callable, *, enable_after: bool = False) -> None:
        if self._closed or self._busy:
            return
        self._busy = True
        self.status.set(self.tr("working"))
        for button in (self.install_button, self.import_button, self.check_button, self.enable_button,
                       *self._device_buttons):
            button.configure(state="disabled")
        self.dismiss_button.configure(text=self.tr("hide"))
        events = self._events

        def work() -> None:
            try:
                operation(lambda value: events.put(("log", str(value))))
                from .voice_setup import inspect_voice_readiness
                events.put(("result", (inspect_voice_readiness(self.config), enable_after)))
            except Exception as error:
                events.put(("error", str(error)))

        threading.Thread(target=work, name="remworkspace-voice-setup", daemon=True).start()

    def install(self) -> None:
        from .voice_setup import install_voice_environment
        device = self.device.get()
        self._start(lambda log: install_voice_environment(self.config, device=device, log=log))

    def choose_pack(self) -> None:
        if self._busy:
            return
        path = filedialog.askopenfilename(parent=self.window, title=self.tr("choose"),
                                         filetypes=[("Voice inference pack", "*.zip *.voice-parts.json"),
                                                    ("ZIP", "*.zip"),
                                                    ("Voice parts manifest", "*.voice-parts.json")])
        if path:
            from .voice_setup import import_voice_pack
            archive = Path(path)
            self._start(lambda log: import_voice_pack(self.config, archive, log=log))

    def check(self) -> None:
        self._start(lambda _log: None)

    def enable(self) -> None:
        # Recheck before enabling: a package may have been removed since the
        # dialog's last inspection. File readiness is not a listening test.
        self._start(lambda _log: None, enable_after=True)

    def open_materials(self) -> None:
        try:
            # Validate again; only a verified publisher link is ever opened.
            url = _material_url()
            if not url:
                self.status.set(self.tr("no_link"))
                return
            native_module("shell").open_uri(url)
        except (OSError, ValueError) as error:
            self.status.set(self.tr("error"))
            self._log(str(error))

    def open_downloads(self) -> None:
        try:
            folder = Path.home() / "Downloads"
            if not folder.is_dir():
                folder = Path.home()
            native_module("shell").open_directory(folder)
        except OSError as error:
            self.status.set(self.tr("error"))
            self._log(str(error))

    def open_ffmpeg(self) -> None:
        try:
            native_module("shell").open_uri("https://ffmpeg.org/download.html")
        except (OSError, ValueError) as error:
            self.status.set(self.tr("error"))
            self._log(str(error))

    def _poll(self) -> None:
        self._poll_job = None
        if self._closed:
            return
        try:
            for _ in range(60):
                kind, value = self._events.get_nowait()
                if kind == "log":
                    self._log(value)
                    continue
                self._busy = False
                for button in (self.install_button, self.import_button, self.check_button,
                               *self._device_buttons):
                    button.configure(state="normal")
                self.dismiss_button.configure(text=self.tr("later"))
                if kind == "error":
                    self.status.set(self.tr("error"))
                    self._log(value)
                    self.enable_button.configure(state="disabled")
                else:
                    readiness, enable_after = value
                    self._apply_readiness(readiness)
                    if enable_after and readiness.get("ready") is True and not self._dismissed:
                        self.on_ready()
                        self.close()
                        return
                if self._dismissed:
                    self.close()
                    return
        except queue.Empty:
            pass
        self._poll_job = self.window.after(60, self._poll)

    def dismiss(self) -> None:
        self._dismissed = True
        if self._busy:
            self.window.withdraw()
        else:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._poll_job is not None:
            self.window.after_cancel(self._poll_job)
            self._poll_job = None
        if self._layout_job is not None:
            self.window.after_cancel(self._layout_job)
            self._layout_job = None
        self.window.destroy()
        self.on_close()


class VoiceEnableGate:
    """Hold the shared playback switch off until user-requested setup is ready."""

    def __init__(self, root: tk.Misc, audio_enabled: tk.BooleanVar, config, *,
                 parent: Callable[[], tk.Misc] | None = None,
                 language: Callable[[], str] = lambda: "zh-Hans",
                 on_status: Callable[[str], None] = lambda _text: None,
                 on_change: Callable[[], None] = lambda: None) -> None:
        self.root = root
        self.audio_enabled = audio_enabled
        self.config = config
        self.parent = parent or (lambda: root)
        self.language = language
        self.on_status = on_status
        self.on_change = on_change
        self._closed = False
        self._muted = False
        self._checking = False
        self._dialog = None
        self._events: queue.SimpleQueue = queue.SimpleQueue()
        self._job = None
        self._refresh_job = None
        audio_enabled.set(False)
        self._trace = audio_enabled.trace_add("write", self._changed)
        self._destroy_binding = root.bind("<Destroy>", self._destroyed, add=True)

    def retranslate(self) -> None:
        if self._dialog is not None:
            self._dialog.set_language(self.language())

    def _set_audio(self, value: bool) -> None:
        self._muted = True
        try:
            self.audio_enabled.set(value)
        finally:
            self._muted = False
        # Tk suppresses nested variable traces. Refresh the visible control
        # after the guard restores OFF instead of leaving a stale ON label.
        if not self._closed and self._refresh_job is None:
            self._refresh_job = self.root.after_idle(self._refresh)

    def _refresh(self) -> None:
        self._refresh_job = None
        if not self._closed:
            self.on_change()

    def _changed(self, *_args) -> None:
        if self._closed or self._muted or not self.audio_enabled.get():
            return
        self._set_audio(False)
        self.on_status(_text("pending", self.language()))
        if self._dialog is not None:
            self._dialog.show()
            return
        if self._checking:
            return
        self._checking = True
        events = self._events

        def work() -> None:
            try:
                from .voice_setup import inspect_voice_readiness
                events.put(inspect_voice_readiness(self.config))
            except Exception as error:
                events.put({"ready": False, "missing": [str(error)]})

        threading.Thread(target=work, name="remworkspace-voice-check", daemon=True).start()
        self._job = self.root.after(40, self._poll)

    def _poll(self) -> None:
        self._job = None
        if self._closed:
            return
        try:
            readiness = self._events.get_nowait()
        except queue.Empty:
            self._job = self.root.after(40, self._poll)
            return
        self._checking = False
        if readiness.get("ready") is True:
            self._enable()
            return
        self._dialog = VoiceSetupDialog(self.parent(), self.config, readiness,
                                       language=self.language(), on_ready=self._enable,
                                       on_close=self._dialog_closed)

    def _enable(self) -> None:
        if not self._closed:
            self._set_audio(True)
            self.on_status(_text("enabled", self.language()))

    def _dialog_closed(self) -> None:
        self._dialog = None

    def _destroyed(self, event) -> None:
        if event.widget is self.root:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._job is not None:
            self.root.after_cancel(self._job)
            self._job = None
        if self._refresh_job is not None:
            self.root.after_cancel(self._refresh_job)
            self._refresh_job = None
        self.audio_enabled.trace_remove("write", self._trace)
        if self._dialog is not None:
            self._dialog.close()
        self.root.unbind("<Destroy>", self._destroy_binding)
