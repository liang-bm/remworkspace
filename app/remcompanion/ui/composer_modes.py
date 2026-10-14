"""Immutable, read-only main-chat modes; never an execution capability.

THESIS: name what this turn is for, not pretend that chat is a shell agent.
OWN-WORLD: existing blue/grey composer tools and native keyboard controls.
STORY: choose one purpose, review the model boundary, then send unchanged text.
FIRST VIEWPORT: a compact current-mode entry; full explanations live in +/Mode.
FORM: local extension of the established WorkBuddy-style grouped tools.
"""
from __future__ import annotations

from dataclasses import dataclass


MODE_IDS = ("ask", "plan", "collaborate")
COPY = {
    "zh-Hans": {
        "title": "模式", "ask": "Ask · 问答", "plan": "Plan · 计划", "collaborate": "协作 · 只读",
        "ask_help": "直接回答问题，不自动把聊天变成项目任务。",
        "plan_help": "分析目标、步骤和风险；只给计划，不执行。",
        "collaborate_help": "高质量模型一起讨论与拟稿；仍不执行命令或修改文件。",
        "locked_help": "协作需先在模型设置确认高质量 API 模型；本地小模型适合问答与轻监工。",
        "locked": "请先在模型设置确认高质量 API 模型，再使用只读协作",
        "busy": "当前回答完成后再更改模式", "changed": "模式已切换；下次发送生效",
        "settings": "模型设置", "settings_help": "选择模型，并确认高级协作能力。",
    },
    "zh-Hant": {
        "title": "模式", "ask": "Ask · 問答", "plan": "Plan · 計畫", "collaborate": "協作 · 唯讀",
        "ask_help": "直接回答問題，不自動把聊天變成專案任務。",
        "plan_help": "分析目標、步驟與風險；只給計畫，不執行。",
        "collaborate_help": "與高品質模型討論與擬稿；仍不執行命令或修改檔案。",
        "locked_help": "協作須先在模型設定確認高品質 API 模型；本地小模型適合問答與輕監工。",
        "locked": "請先在模型設定確認高品質 API 模型，再使用唯讀協作",
        "busy": "目前回答完成後再變更模式", "changed": "模式已切換；下次傳送生效",
        "settings": "模型設定", "settings_help": "選擇模型，並確認進階協作能力。",
    },
    "en": {
        "title": "Mode", "ask": "Ask · Q&A", "plan": "Plan · Read-only", "collaborate": "Collaborate · Read-only",
        "ask_help": "Answer directly. Do not turn casual chat into a project task.",
        "plan_help": "Analyze goals, steps and risks. Make a plan, without executing it.",
        "collaborate_help": "Discuss and draft with a capable model. No commands or file changes.",
        "locked_help": "Confirm a high-quality API model in settings first. Small local models suit chat and light supervision.",
        "locked": "Confirm a high-quality API model in settings before using read-only Collaboration",
        "busy": "Wait for the current reply before changing mode", "changed": "Mode changed; applies to the next send",
        "settings": "Model settings", "settings_help": "Choose a model and confirm its advanced collaboration capability.",
    },
    "ja": {
        "title": "モード", "ask": "Ask · 質問", "plan": "Plan · 計画", "collaborate": "共同作業 · 読み取り専用",
        "ask_help": "質問に直接回答。雑談を自動でプロジェクトの作業に変えません。",
        "plan_help": "目標・手順・リスクを分析。計画のみで、実行しません。",
        "collaborate_help": "高品質モデルと相談・下書き。コマンド実行やファイル変更はしません。",
        "locked_help": "設定で高品質 API モデルを確認してください。小型ローカルモデルは会話と軽い監視向けです。",
        "locked": "読み取り専用の共同作業には、設定で高品質 API モデルの確認が必要です",
        "busy": "現在の回答が終わってからモードを変更してください", "changed": "モードを変更しました。次の送信から適用します",
        "settings": "モデル設定", "settings_help": "モデルを選択し、高度な共同作業の能力を確認します。",
    },
}


def mode_copy(language: str) -> dict[str, str]:
    return COPY.get(language, COPY["zh-Hans"])


@dataclass(frozen=True)
class RequestModeSnapshot:
    mode_id: str = "ask"
    is_local: bool = True
    advanced_enabled: bool = False

    def validate(self) -> "RequestModeSnapshot":
        if self.mode_id not in MODE_IDS:
            raise ValueError("聊天模式无效，请重新选择 Ask、Plan 或只读协作")
        if self.mode_id == "collaborate" and (self.is_local or not self.advanced_enabled):
            raise ValueError(COPY["zh-Hans"]["locked"])
        return self

    def prompt(self) -> str:
        self.validate()
        boundary = (
            "以下是用户在 RemChat 中为本轮明确选择的互动模式。保持既有输出协议和中文回答规则。"
            "本应用没有文件写入、命令执行或自主 Agent 权限；模式不授予这些权限。"
            "只能依据本轮实际提供的文本、附件和工具资料，不声称已读取未提供的文件、"
            "执行命令、修改项目或完成外部任务。"
        )
        instruction = {
            "ask": "本轮为 Ask / 问答：直接回答用户的问题。除非用户明确要求，不把日常聊天自动扩展成长任务、项目计划或操作清单。",
            "plan": "本轮为 Plan / 计划：分析用户目标，给出关键步骤、依赖、风险和需要确认的选择。仅提供可供用户检查的计划；不执行、不把拟议步骤表述成已经完成，不伪造当前项目进度。",
            "collaborate": "本轮为只读协作：与用户共同澄清问题，说明下一步建议及理由；需要时提供代码或修改草稿供检查。所有草稿都尚未应用，实际执行由用户或其外部 CLI 决定。不要声称拥有 shell、MCP 执行权或自动完成了项目修改。",
        }[self.mode_id]
        if self.is_local:
            instruction += " 当前为本地小模型，适合问答、解释和轻量监工；承认复杂推理和长任务能力限制，不冒充高能力 Agent。"
        return boundary + "\n" + instruction


def capture_mode(mode_id: str, provider) -> RequestModeSnapshot:
    return RequestModeSnapshot(mode_id, provider.is_local, provider.advanced_model_enabled).validate()
