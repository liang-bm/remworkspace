"""RemChat 语义主题；浅色值保持兼容，悬浮框和透明宠物独立配色。"""

from __future__ import annotations

import sys
from typing import Any


LIGHT = {
    "APP_BG": "#F7F9FC", "SIDEBAR": "#F0F4F9", "WHITE": "#FFFFFF",
    "INK": "#20344A", "MUTED": "#596B7D", "SUBTLE": "#657487",
    "LINE": "#DBE4ED", "ACCENT": "#328FCB", "ACCENT_DEEP": "#216D9F",
    "ACCENT_SOFT": "#E3F1FC", "ACCENT_HOVER": "#1A5B88", "ROSE": "#AD4C70", "SUCCESS": "#247553",
    "ERROR": "#BA3D58", "USER_BUBBLE": "#E5F2FC", "REM_BUBBLE": "#FFFFFF",
    "COMPOSER_BG": "#F1F5FA", "ON_ACCENT": "#FFFFFF",
}
DARK = {
    "APP_BG": "#202733", "SIDEBAR": "#252E3C", "WHITE": "#2B3544",
    "INK": "#E0E7EF", "MUTED": "#B1BCCB", "SUBTLE": "#9DAAB9",
    "LINE": "#465264", "ACCENT": "#9AC5E4", "ACCENT_DEEP": "#416F91",
    "ACCENT_SOFT": "#33485E", "ACCENT_HOVER": "#355E7E", "ROSE": "#D6A1B7", "SUCCESS": "#9CC6B2",
    "ERROR": "#EAA5AC", "USER_BUBBLE": "#354B61", "REM_BUBBLE": "#2B3544",
    "COMPOSER_BG": "#293342", "ON_ACCENT": "#F5F8FC",
}
BODY_FONT = "Microsoft YaHei UI"
_mode = "light"

# 明确列出真正导入这些 token 的模块，不更新 overlay/pet_window，也不按
# 值扫整个 sys.modules。别名不能只凭变量名猜：接入面板 ACCENT 实为 DEEP。
_MODULE_ALIASES: dict[str, dict[str, str]] = {
    "remcompanion.chat_window": {},
    "remcompanion.ui.chat_messages": {},
    "remcompanion.ui.chat_settings": {},
    "remcompanion.ui.model_settings": {},
    "remcompanion.ui.provider_management": {},
    "remcompanion.ui.composer_context": {},
    "remcompanion.ui.project_process": {},
    "remcompanion.ui.chat_background_view": {},
    "remcompanion.ui.focus_timer": {},
    "remcompanion.ui.memory_panel": {},
    "remcompanion.ui.action_panel": {},
    "remcompanion.ui.plaza_panel": {
        "BG": "APP_BG", "BLUE": "ACCENT_DEEP", "SOFT": "ACCENT_SOFT",
        "FONT": "BODY_FONT", "GREEN": "SUCCESS",
    },
    "remcompanion.ui.integrations_panel": {
        "BG": "APP_BG", "ACCENT": "ACCENT_DEEP", "SOFT": "ACCENT_SOFT",
    },
    "remcompanion.ui.integration_draft": {},
    "remcompanion.ui.mcp_form": {},
    "remcompanion.ui.mcp_panel": {},
    "remcompanion.ui.mcp_import": {},
    "remcompanion.ui.skill_panel": {},
}


def palette(mode: str | None = None) -> dict[str, str]:
    selected = _mode if mode is None else mode
    if selected not in {"light", "dark"}:
        raise ValueError("主题必须为 light 或 dark")
    return dict(DARK if selected == "dark" else LIGHT)


def get_mode() -> str:
    return _mode


def register_module(module_name: str, aliases: dict[str, str] | None = None) -> None:
    """为新主聊天页面显式注册导入别名；禁止透明窗口接入此配色。"""
    if module_name in {"remcompanion.overlay", "remcompanion.pet_window"}:
        raise ValueError("透明宠物与悬浮框使用独立配色")
    if not (module_name == "remcompanion.chat_window" or module_name.startswith("remcompanion.ui.")):
        raise ValueError("只能注册 RemChat 主聊天 UI 模块")
    aliases = dict(aliases or {})
    if any(target not in LIGHT and target != "BODY_FONT" for target in aliases.values()):
        raise ValueError("别名必须指向已定义的主题 token")
    _MODULE_ALIASES[module_name] = aliases
    _update_module(module_name, aliases)


def _update_module(name: str, aliases: dict[str, str]) -> None:
    module: Any = sys.modules.get(name)
    if module is None:
        return
    tokens = {**palette(), "BODY_FONT": BODY_FONT}
    for token, value in tokens.items():
        if token in module.__dict__ and token not in aliases:
            module.__dict__[token] = value
    for alias, target in aliases.items():
        if alias in module.__dict__:
            module.__dict__[alias] = tokens[target]


def set_mode(mode: str) -> None:
    """更新未来构造组件使用的 token，已存在组件交给 ThemeController。"""
    global _mode
    colors = palette(mode)  # 先校验，非法 mode 不产生半更新。
    _mode = mode
    globals().update(colors)
    for name, aliases in _MODULE_ALIASES.items():
        _update_module(name, aliases)


globals().update(LIGHT)
