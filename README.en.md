# RemWorkspace

[中文](README.md) | English | [日本語](README.ja.md)

<img width="2569" height="1667" alt="image" src="https://github.com/user-attachments/assets/b4dff924-4ce3-4f90-b01f-83fcb2484d24" />


RemWorkspace is a local-first desktop workspace with chat, Rem as a desktop companion, a diary, daily tasks and a focus timer. Use the main chat window or the small speech bubble that follows her, then take a break with the farm or Gomoku.

The repository is named `remworkspace`. The app's interface and executables are still named RemChat, and its Python package is `remcompanion`. Button labels, commands and paths below use those current names.

It runs on Windows and Linux x86_64. Choose Simplified Chinese, Traditional Chinese, English or Japanese in Settings > General. The interface follows your choice; your chats, journal entries and filenames stay as you wrote them.

Version 0.1.1 fixes dark animation halos on Windows, fills translation gaps in newer screens and dialogs, and keeps task tracking rings visible over the focus curtain. Rings you have hidden stay hidden.

For everyday use, download your system's package from [Releases](https://github.com/liang-bm/remworkspace/releases). It includes the pet animations. You can open the app, write a diary, manage tasks, focus and use the farm without installing Python or speech models first. Chat needs your own model service. Speech starts off; install it only when you want spoken replies.

The source repository contains no personal records, API keys, voice weights or model runtime. To run from source, follow [Run from source](#run-from-source); the animation asset pack in Releases can be extracted into the project root.

[Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

## First launch

If you have a complete program package, open `desktop-app/RemChat.exe` on Windows. On Linux, use RemChat in the application menu after installation, or run the package's `desktop-app/RemChat`.

1. If only Rem appears, right-click her and choose “显示 / 隐藏主聊天窗口” to show the main chat window.
2. Open Settings (设置), then General (通用), and choose your Interface language (界面语言).
3. Open Models & API (模型与 API). For an online model, enter your Base URL, API key and model ID, then choose “保存并启用” (save and enable). Check the model selector below the chat input before sending.
4. You can also connect an existing local compatible service. To install the local chat model, select “本地 Qwen” and click “安装本地模型”. Follow the page's installation and startup status. Local models need additional disk space and memory.
5. Return to chat. Enter sends a message; Shift + Enter adds a line. “播放” (speech playback) starts off. Turning it on for the first time opens the speech setup guide; text chat does not need it.

Saving a provider does not test it. “测试连接” and “获取模型” send requests when you click them; your provider may charge for a test. Coding subscriptions have their own usage restrictions and may not work as general chat APIs.

## Where to find things

| What you want to do | Where to go |
| --- | --- |
| Start or switch a conversation | Left conversation list, “新对话” |
| Switch the chat model | Model selector below the input |
| Configure an API or local model | Settings > Models & API |
| Change interface language or chat text size | Settings > General |
| Change the wallpaper, theme or pet size | Settings > Appearance & motion (外观与动作) |
| Change the companion's instructions | Settings > Chat & personality (对话与人格) |
| Review or edit remembered information | Settings > Memory & storage > Memory management |
| Use speech input | Settings > 语音输入, then a microphone button |
| Write a diary or manage daily tasks | 功能广场 (feature hub) > 日记 (diary) |
| Farm, study words or play Gomoku | The matching entry in 功能广场 |
| Configure gentle reminders | 功能广场 > 主动陪伴 |
| Read tool sessions or add desktop tracking | 功能广场 > 项目过程 |
| Manage apps, MCP servers, skills and hooks | Settings > 接入资源 |
| View collected token usage | Settings > 接入资源 > 全部用量 |
| Edit countdown dates or start focus | Top of the main chat |
| Import or preview frame animations | Settings > Action studio (动作工作台) |
| Open this guide | “指南” at the top left |

“返回功能广场” returns to the feature hub. In narrow windows, some settings navigation moves into a selector at the top.

## Chat, models and memory

Use the main window for longer replies and the floating bubble for a quick message. Both share the current conversation. “悬浮窗” at the top shows or hides the bubble.

Messages support Markdown headings, lists, quotes, tables and code blocks. Copy a code block on its own; Markdown examples can switch between source and preview. Expand a long reply when needed. Reading older messages does not force you back to the bottom. Right-click a message to copy its original Markdown. Code and HTML are not executed.

The input's “＋” attaches material to the current request. “选择工作空间” selects a relevant directory; selecting it does not scan or modify all its files. Check the attachments before sending. Removing an attachment from the list leaves the original file intact. Ask / Plan modes use the capabilities described in the interface; they do not grant unrestricted command execution.

A provider can hold several models. Use a model ID that the service accepts, and leave unknown context or output limits blank. The 中文 / 日本語 buttons control speech playback. Changing the interface language does not translate stored messages or change the voice. Chat replies use Chinese by default.

Ask Rem to remember a preference, then check Memory management to see what was saved. You can review, edit, delete, import or export it there. Put preferences you want her to follow consistently in Chat & personality. Conversations, remembered information and diary records have separate storage.

## Rem and appearance

Right-click Rem to show the main window, choose an idle activity or exit. She can idle, doze or walk slowly by herself. Approaching her while she sleeps does not make her jump. A gentle click plays her wake-up animation; dragging follows the drag interaction. Chatting and focus take priority over idle activities.

Settings > Appearance & motion controls the theme, wallpaper, wallpaper fade and pet size. Background management can import your pictures and change them at startup or by date. Ctrl + / Ctrl - changes the main chat text size without changing the bubble.

Action studio imports, previews and manages your own frame animations. RemChat uses image frames for the pet. Follow the page's frame and animation requirements when importing assets.

## Speech input and playback

Enable a recognition method in Settings > 语音输入. Speech-to-text (ASR) and reading a reply aloud are separate features.

- Main chat and floating bubble: click the microphone. A level waveform appears while recording. Pause, resume, stop and transcribe, or cancel. The transcript goes into your draft; review it before sending.
- Diary editor: click “语音记录”. Record in the timer dialog, then stop and transcribe into the current diary.

Switching conversations or diaries cancels the current recording. A late result is not inserted elsewhere. Recording or transcription errors leave the original draft in place. Temporary recordings are deleted after use and do not become diary attachments automatically.

Online recognition needs an endpoint and model. One configuration example is SiliconFlow's `https://api.siliconflow.cn/v1/audio/transcriptions` with `TeleAI/TeleSpeechASR`. Save a separate ASR key or use a provider key you already stored. Stopping and transcribing sends the recording to that service and may incur a charge. Supported recognition languages depend on the chosen model.

For local recognition, choose a Whisper.cpp `whisper-cli` executable and matching model, or connect your own running compatible transcription service. RemChat does not download ASR models automatically. Local services use a loopback address, such as `http://127.0.0.1:8080/v1/audio/transcriptions`.

On Windows, allow access to the default microphone. On Linux, install at least one of `pw-record`, `parec` or `arecord`, and start RemChat in a desktop session.

### Hear Rem read a reply

Click “播放” at the top of chat, or ♪ in the floating bubble. Both use the same switch and open the speech setup guide. You can choose Not now and keep using text chat.

1. Prepare FFmpeg and a C/C++ compiler. On Windows, get a Windows build from [FFmpeg's official download page](https://ffmpeg.org/download.html), place its extracted `bin` in `engines/ffmpeg/bin` inside the app's installation folder, and keep its DLLs and licenses. Install the C++ workload of [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/). On Ubuntu / Debian, run `sudo apt install ffmpeg build-essential`. Two speech dependencies need compilation. RemChat never runs sudo or installs system tools for you.
2. Choose CPU or NVIDIA GPU and click Install environment. Choose CPU if unsure; GPU needs a driver supporting CUDA 12.8. The installer downloads pinned GPT-SoVITS, a separate Python environment and base models, verifies the files, and checks dependencies. Allow a few GB and some time for the first download. Closing the progress window does not cancel an active install, and speech stays off.
3. Open Google Drive materials, or visit the [voice download folder](https://drive.google.com/drive/folders/1RvbxWWKFjzlHd4PHGXQU9_3EExOs4DgM). Download `rem-voice-inference-v2pro.voice-parts.json` and all three `rem-voice-inference-v2pro.zip.001`, `.002`, `.003` files into one folder. Keep their names; do not extract them yourself.
4. Click Import inference pack and select the `.voice-parts.json` manifest. The app verifies and joins the parts, imports the trained weights, reference audio and transcript, then encrypts them with your local account's key. Once ready, choose Enable speech. Use “播放” or ♪ to switch it on and off later.

The inference pack is about 256 MiB. It has produced Chinese and Japanese audio on CPU in a real synthesis check. It contains no original training dataset, and playback needs no retraining. A complete installation on a new machine still depends on system tools, network access and drivers. Setup shows errors if a step fails; text chat remains available.

The logical import paths are `models/tts/rem/gpt/rem-gpt.ckpt`, `models/tts/rem/sovits/rem-sovits.pth`, `voice-source/reference.wav` and `voice-source/reference.txt`. After import, they are kept in the resource vault; your downloaded pack is not deleted. The base engine lives in `engines/GPT-SoVITS` and its separate environment in `runtime/gpt-sovits`. Import the pack on each device rather than copying another account's encrypted files.

中文 / 日本語 chooses Chinese or Japanese playback. Your chat API and the local voice are separate. You can leave speech off permanently if you only want text.

## Focus and countdown dates

Click the countdown display at the top of the main chat to edit it. Enter an event and a date in `YYYY-MM-DD` format. Save several events and choose “展示” for the one to display. Without a manual choice, the nearest upcoming date takes priority. The display also handles today and elapsed dates.

Set the duration in Focus and click Start. Your chat wallpaper becomes a frontmost desktop curtain with a dark overlay and animated countdown. Rem stays visible. The bubble is hidden temporarily and returns to its previous setting afterwards.

Pause or resume on the curtain. End focus or press Esc to leave at any time. When the timer finishes, it returns you to the desktop and gives the break reminder. Multiple displays share one timer. Focus does not change the system wallpaper, lock the computer or disable system shortcuts.

On Linux, topmost windows and display boundaries depend on X11 / XWayland and the window manager. Native Wayland can restrict these operations.

## Diary and daily tasks

Open 功能广场 > 日记 for the day-and-time timeline. Use the calendar to find a date and “今天” to return to today. “写一刻” creates an entry; “阅读 / 编辑” opens an existing one. “返回总览页面” takes you back to the timeline.

The toolbar applies bold, marker highlighting, headings, lists and quotes, and inserts colorful emoji or attachments. Selecting text shows a small toolbar. Right-click or press Shift + F10 for the full formatting menu. You can also type Markdown: formatting appears in the same editor while you write, without a separate preview page.

Click “代码块” to choose a language and enter code. Syntax colors help with reading. “复制” copies the whole block without its fences. Diary code is stored as text and never executed.

Set tags beside the date. “搜索日记” searches titles, text, dates and tags across days, including multiple keywords and some fuzzy matches. Click a result to open and locate the content. Clear the search to return to the timeline. Search runs locally.

Daily tasks appear before that day's entries. Several entries on the same day share one checklist. “设置日记模板” at the top right of the overview lets you choose weekdays, a body template and recurring tasks, one per line. Opening a matching date creates its checklist once. New entries use the enabled template; existing text is left alone.

Attach images, audio, video or ordinary files. Pictures have thumbnails; audio and video open in the system player when clicked. Ordinary files open their folder rather than execute. An entry allows up to 64 attachments, each up to 128 MiB. Attachments stay on your computer and are not sent to a model automatically.

Writing pauses trigger autosave, and “保存” saves explicitly. A failed save keeps the draft; resolve the message before leaving. Deleted entries go to the recycle bin and can be restored. Use “导入 / 导出” for older text or Markdown, or `.remjournal` to preserve formatting, timestamps, tags and attachments.

## Farming, word cards and Gomoku

### Pixel garden

Open 功能广场 > 像素花园. Click grass to walk to it. Move with the arrow keys or WASD, choose tools with number keys 1 to 4, and use Space or E to act. The tools are till, plant, water and harvest. Water your crops after planting and harvest them when ready.

Choose radishes, carrots, bluebells, tomatoes, corn, pumpkins, strawberries or sunflowers. Crops grow in real time and keep their progress while the app is closed. Refill at the lower-left well or upper-right pond dock. E opens or closes the entrance gate. Trees, rocks, fences and water block movement; the bridge is passable.

The normal view follows Rem. “全图视角” shows the whole farm; scene previews switch season and time of day. The house is not open in this release. Farming takes place outdoors.

### Word cards

Open 功能广场 > 单词卡片 to import a local dictionary or your own cards. Select a dictionary for definitions and examples. “记住了 / 还没记住” records your review; “我的笔记” holds your own explanation. Cards and notes can be imported and exported.

“AI帮记” calls the current model when clicked. Its explanations and examples are shown first. Save them with “解释加入笔记” or “添加所选例句”. Your saved material is separate from dictionary text.

### Gomoku

Open 功能广场 > 五子棋. New boards are 22×22. Black starts; five in a row wins. Undo, ask for a hint or start another game.

The default opponent is a local algorithm. “让当前聊天模型参与落子（耗 Token）” opts into model moves; game explanations are also requested explicitly. Ordinary local games do not need an API.

## Reminders, project sessions and tracking rings

功能广场 > 主动陪伴 sets the reminder interval and quiet hours. Automatic reminders start off. Try “体验一次提醒” first if you want. Idle time means no interaction inside RemChat; it does not monitor keyboard or mouse activity across the computer. Replies, playback and focus postpone automatic reminders.

功能广场 > 项目过程 reads session records left by local tools. Choose the tool, date and main session to see public user inputs, stage information and token activity. Supported sources are marked in the list. A “暂不支持” label means the source is not connected. “跟随最新” follows new records; choosing an older session pauses following.

Use “接入与会话管理” to add selected sessions to desktop tracking. Rings show synchronization state. Restore hidden rings from “恢复跟踪环” in the sidebar or Settings > General. Stopping observation does not stop the host tool's task. Goals appear only when the host supplies a native goal or public `/goal`; ordinary chat is not converted into a goal.

Project instructions and prompt archives come from readable local records. The instruction view points to actual files in the selected workspace, so editing them affects that project. It does not expose hidden system prompts or internal model reasoning.

## Apps, MCP, skills and usage

Open Settings > 接入资源. You choose which connections or actions to enable.

“应用” cards use registered `codex://` links to open Codex connection or app pages. Log in and authorize there. A compatible Codex installation is required. RemChat does not copy or store its login tokens. Opening a page does not confirm a connection, and opening an app draft does not send it.

“MCP 服务” configures external tools. Presets include Context7, Playwright, Sequential Thinking, Filesystem, Memory, Fetch, Git and read-only GitHub. Selecting one fills an unsaved, disabled draft. Review its paths, arguments and permissions, save, then connect explicitly. Selecting a preset does not install a package or start a server.

“技能库” manages local skill files. “事件 Hook” and “接入草稿” hold actions you explicitly configure. Importing a configuration does not enable a connection or command. Use services you understand and trust.

“全部用量” shows locally collected token calendars and trends. Filter by model, date range or metric. A missing date is unknown, not zero. Recorded counts and observed increments are separate and are not added twice. Cost appears only when the source supplies it.

## Data, backups and privacy

Chat and remembered information live in `data/memory.sqlite3`; diary and activity records are in `data/plaza/`. For a diary backup, keep both `data/plaza/diary.sqlite3` and `data/plaza/diary-media/`. The database alone does not include attachment files. Countdown dates live in `data/countdown-days.json`.

Chat and diary databases do not currently use database-level encryption. Protect your system account and backups on a shared computer. Remote chat sends messages and the relevant history and memory context to your selected provider. Online ASR sends the recording; web search sends the query. Diary entries and attachments are not uploaded automatically.

API keys use current-user Windows DPAPI or Linux Secret Service. Provider exports exclude keys. Changing the endpoint host requires reconfirming credentials.

The resource vault uses AES-256-GCM for private voice resources and keeps keys in account-protected storage. Protected resources allow local playback by default and deny training and raw export. Files are decrypted temporarily for use and removed on ordinary completion. This does not grant asset rights or prevent the logged-in account or an administrator from inspecting active files. Forced termination or power loss can leave an account-only temporary directory.

Keep the vault and key recovery material in a separate local backup. Windows account changes or a lost Linux keyring can make encrypted files unrecoverable. Linux keyring entries also belong to the original installation path. Do not commit live `data/`, configuration, models, voices, vaults or recovery packages to a public repository.

Memory uses SQLite + FTS5 by default. Optional sqlite-vec retrieval needs a real local embedding service. It starts disabled and does not download a model. You do not need a vector service for everyday use.

## Run from source

Use Python 3.12 with Tk. Supply character assets you have permission to use: idle frames belong in `app/remcompanion/assets/pet/idle/idle_*.png`; other animations belong in `app/remcompanion/assets/pet/actions/`. Existing vault owners can recover assets as described in the next section. These files are not in the public source tree.

### Windows

Open PowerShell in the project root and create a dedicated environment:

```powershell
py -3.12 -m venv runtime\control
.\runtime\control\Scripts\python.exe -m pip install Pillow==12.3.0 Pygments==2.19.2 cryptography==50.0.2 sqlite-vec==0.1.6
if (-not (Test-Path config\rem_companion.toml)) {
    Copy-Item config\defaults\rem_companion.toml config\rem_companion.toml
}
$env:REM_HOME = (Get-Location).Path
$env:PYTHONPATH = Join-Path (Get-Location) 'app'
.\runtime\control\Scripts\python.exe -m remcompanion.desktop
```

An existing configuration is kept. Use this environment's Python on later starts. To use `platforms/windows/从源代码启动.cmd`, activate `runtime\control` first.

### Linux

In a graphical desktop session, open a terminal in the project root:

```bash
test -f config/rem_companion.toml || cp config/defaults/rem_companion.toml config/rem_companion.toml
bash platforms/linux/scripts/Setup-LinuxDesktop.sh
bash platforms/linux/Start-RemChat.sh
```

Setup downloads a separate Linux frontend environment, Tk and fonts. It does not install system packages, chat models or voices. The system needs X11 / XWayland, Xft and fontconfig. Prepare microphone tools, a system keyring and model runtimes separately. Windows runtimes and account-wrapped secrets cannot be reused directly on Linux.

If you have an installable Linux package, extract it and use its `Install.sh`. Packages marked private-local require the indicated `--private-local` option and should stay out of public releases.

## Recover local resources or enable vector retrieval

Set up the environment above with `PYTHONPATH=app`. Here, `python` means that environment's interpreter: `.\runtime\control\Scripts\python.exe` on Windows or `runtime/desktop-linux-xft/bin/python` on Linux.

```text
python -m remcompanion.private_resource_cli status
python -m remcompanion.private_resource_cli verify
python -m remcompanion.private_resource_cli restore-local --vault-directory "YOUR_PRIVATE_VAULT"
```

Replace `YOUR_PRIVATE_VAULT` with the actual absolute path outside the repository. Recovery needs the original account and keys. `restore-local` restores character assets while voice files remain protected for on-demand use. Add `--include-configuration` only when you also want your personal instructions and API settings restored to your own local installation.

To use vector retrieval, run a real local embedding service first and enter its model name and revision:

```text
python -m remcompanion.private_resource_cli semantic --allow --base-url http://127.0.0.1:8081/v1 --model YOUR_EMBEDDING_MODEL --revision YOUR_MODEL_REVISION
```

This saves settings; it does not download or start a model. Update the revision when changing weights. An unavailable service falls back to ordinary memory search.

## If something goes wrong

- Only Rem is visible: right-click her to show the main chat window.
- A model will not connect: check the selected model, Base URL, model ID and key. For a local model, check that its service is running. Turn playback off if speech alone fails.
- No microphone input: check the default device and permissions. On Linux, check the recording tool too.
- A diary will not save: keep the draft, check the page's error, disk space and permissions, then retry.
- Missing Linux characters or failed copy: check CJK fonts. Under WSLg, follow the `wl-clipboard` prerequisite message.
- Protected TTS rejects an unknown port: confirm it is your own service. Do not reuse an unidentified local listener.

When reporting a problem, include your system, steps and the error. Leave out API keys, private chats, diary entries, voice files and recovery packages.

Use [Issues](https://github.com/liang-bm/remworkspace/issues) for ordinary bugs and feature requests. Reports in Chinese, English or Japanese are welcome. Report vulnerabilities, exposed credentials and private-data problems through the private process in [Security](SECURITY.md). For code or documentation changes, read [Contributing](CONTRIBUTING.md).

## License

The application code uses the [MIT License](LICENSE). Character art, voices, models and dependencies have separate licenses; the code license does not grant rights to those assets. Included colorful emoji use Twemoji under CC-BY-4.0, with attribution and license material retained in program packages.
