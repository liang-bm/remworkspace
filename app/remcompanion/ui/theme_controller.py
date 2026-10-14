"""给已存在的 Tk 控件换主题，不销毁会话或重建窗口。

主题映射保留每个控件的原始语义，反复应用也能覆盖之后新建的旧式组件。
透明宠物/气泡子树和标记为 theme-fixed 的画布内容不参与换色。
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable
import tkinter as tk
from tkinter import ttk
from weakref import WeakKeyDictionary

from . import theme


# 旧页面中尚未 token 化的色值；只映射明确的 UI 背景/文字，避免重绘像素画。
LEGACY_ROLES = {
    "#eef1f4": "SIDEBAR", "#eef2f5": "SIDEBAR", "#f7f9fa": "APP_BG",
    "#f4f8fc": "APP_BG", "#e8ebee": "ACCENT_SOFT", "#e2e7ea": "ACCENT_SOFT",
    "#e3e9ed": "ACCENT_SOFT", "#e5ebef": "ACCENT_SOFT", "#dceffa": "ACCENT_SOFT",
    "#d0e8f8": "ACCENT_SOFT", "#ddebf4": "ACCENT_SOFT", "#cce7f8": "ACCENT_SOFT",
    "#bbddf3": "ACCENT_SOFT", "#d7dde2": "LINE", "#a7c6df": "LINE",
    "#f0f0f0": "SIDEBAR", "#d9d9d9": "LINE", "#a3a3a3": "MUTED",
    "#596570": "MUTED", "#4b5762": "MUTED", "#56626d": "MUTED",
    "#d5e4ed": "SUBTLE", "#21699a": "ACCENT_DEEP", "#b84460": "ERROR",
    "#fff1f5": "ROSE_SOFT", "#fbe4eb": "ROSE_SOFT", "#000000": "INK",
}
COLOR_OPTIONS = (
    "background", "foreground", "activebackground", "activeforeground",
    "disabledforeground", "insertbackground", "selectbackground", "selectforeground",
    "highlightbackground", "highlightcolor", "selectcolor", "troughcolor",
    "readonlybackground", "disabledbackground",
)
_FOREGROUND_OPTIONS = {
    "foreground", "activeforeground", "disabledforeground", "insertbackground", "selectforeground",
}


@dataclass
class _ColorState:
    role: str | None
    literal: str
    expected: str = ""


def _color_value(role: str | None, literal: str, option: str, mode: str,
                 *, text_item: bool = False) -> str:
    if role is None:
        return literal
    if role == "ROSE_SOFT":
        return "#3D2B3B" if mode == "dark" else literal
    colors = theme.palette(mode)
    # DEEP fills carry white text; blue text is brighter in the dark palette.
    if mode == "dark" and role == "ACCENT_DEEP" and (option in _FOREGROUND_OPTIONS or text_item):
        return colors["ACCENT"]
    return colors[role]


def set_color_roles(widget: tk.Misc, **roles: str) -> None:
    """Apply current semantic colors now and retain their roles for theme flips.

    Components created after a theme change and stateful callbacks must not
    depend on a module's earlier ``from theme import ...`` snapshot. Only color
    options are configured here; editor contents, focus and history are intact.
    """
    if any(option not in COLOR_OPTIONS or role not in theme.LIGHT for option, role in roles.items()):
        raise ValueError("颜色配置必须使用已定义的控件选项与主题角色")
    mode = theme.get_mode()
    widget.configure(**{option: _color_value(role, "", option, mode) for option, role in roles.items()})
    explicit = dict(getattr(widget, "_remchat_color_roles", {}))
    explicit.update(roles)
    widget._remchat_color_roles = explicit


class ThemeController:
    def __init__(self, root: tk.Misc, *, exclude: Iterable[tk.Misc] = ()) -> None:
        self.root = root
        self.exclude = {str(widget) for widget in exclude}
        self._widgets: WeakKeyDictionary = WeakKeyDictionary()
        self._canvas: WeakKeyDictionary = WeakKeyDictionary()
        self._text_tags: WeakKeyDictionary = WeakKeyDictionary()
        self._native_ttk_theme: str | None = None
        self._canonical_colors: dict[str, str] = {}
        self._ttk_mode: str | None = None
        from .rounded_controls import RoundedChrome
        self._rounded_chrome = RoundedChrome(root)
        from .scroll_surface import install_scroll_repaint
        self._scroll_repaint = install_scroll_repaint(root)

    def _canonical(self, color: str) -> str:
        if not color:
            return ""
        cached = self._canonical_colors.get(color)
        if cached is not None:
            return cached
        try:
            red, green, blue = self.root.winfo_rgb(color)
            normalized = f"#{red // 257:02x}{green // 257:02x}{blue // 257:02x}"
            self._canonical_colors[color] = normalized
            return normalized
        except tk.TclError:
            return str(color).lower()

    def _role(self, color: str, option: str, widget: tk.Misc, *, text_item: bool = False) -> str | None:
        normalized = self._canonical(color)
        if not normalized:
            return None
        foreground = option in _FOREGROUND_OPTIONS or text_item
        # WHITE 是 surface；用于按钮/文字的纯白则是恒定 ON_ACCENT。
        if normalized == "#ffffff" and foreground:
            return "ON_ACCENT"
        if isinstance(widget, tk.Text) and option == "background" and normalized == theme.DARK["USER_BUBBLE"].lower():
            return "USER_BUBBLE"
        if normalized in LEGACY_ROLES:
            return LEGACY_ROLES[normalized]
        for colors in (theme.palette(), theme.LIGHT, theme.DARK):
            for role, value in colors.items():
                if normalized == value.lower():
                    return role
        return None

    def _value(self, state: _ColorState, option: str, mode: str, *, text_item: bool = False) -> str:
        return _color_value(state.role, state.literal, option, mode, text_item=text_item)

    def _state(self, states: dict, key: object, color: str, option: str, widget: tk.Misc,
               *, text_item: bool = False, use_explicit: bool = True) -> _ColorState:
        state = states.get(key)
        # Stateful provider rows explicitly name their semantic role. Literal
        # colors cannot distinguish freshly reselected rows after a theme flip.
        explicit = getattr(widget, "_remchat_color_roles", {}).get(option) if use_explicit else None
        if explicit in theme.LIGHT:
            if state is None or state.role != explicit:
                state = _ColorState(explicit, color)
                states[key] = state
            return state
        # 控件可能由真实交互改色。仅当值仍是我们上次写入的值时保留原角色。
        if state is None or self._canonical(color) != self._canonical(state.expected):
            state = _ColorState(self._role(color, option, widget, text_item=text_item), color)
            states[key] = state
        return state

    def _excluded(self, widget: tk.Misc) -> bool:
        if str(widget) in self.exclude or getattr(widget, "_remchat_theme_exclude", False):
            return True
        if isinstance(widget, (tk.Tk, tk.Toplevel)):
            try:
                if widget.attributes("-transparentcolor"):
                    return True
            except tk.TclError:
                pass
        return False

    def _walk(self, widget: tk.Misc):
        if self._excluded(widget):
            return
        yield widget
        try:
            children = widget.winfo_children()
        except tk.TclError:
            return
        for child in children:
            yield from self._walk(child)

    def apply(self, mode: str) -> None:
        # Invalid input must not partially recolor or mutate globals.
        theme.palette(mode)
        widgets = list(self._walk(self.root))
        for widget in widgets:
            self._apply_widget(widget, mode)
        theme.set_mode(mode)
        if self._ttk_mode != mode:
            self.configure_ttk(mode)

    def _apply_widget(self, widget: tk.Misc, mode: str) -> None:
        try:
            options = widget.keys()
        except tk.TclError:
            return
        states = self._widgets.setdefault(widget, {})
        updates = {}
        current = {}
        fixed_canvas = isinstance(widget, tk.Canvas) and getattr(widget, "_remchat_theme_fixed", False)
        for option in COLOR_OPTIONS:
            if option not in options or (fixed_canvas and option == "background"):
                continue
            color = str(widget.cget(option))
            current[option] = color
            if not color:
                continue
            state = self._state(states, option, color, option, widget)
            updates[option] = self._value(state, option, mode)
        if isinstance(widget, (tk.Text, tk.Entry, tk.Listbox, tk.Spinbox)) and "selectbackground" in options:
            updates["selectbackground"] = theme.palette(mode)["ACCENT_DEEP"]
            if "selectforeground" in options:
                updates["selectforeground"] = theme.palette(mode)["ON_ACCENT"]
        if isinstance(widget, (tk.Checkbutton, tk.Radiobutton)) and "selectcolor" in options:
            updates["selectcolor"] = theme.palette(mode)["WHITE"]
        if isinstance(widget, tk.Button) and mode == "dark":
            # Old widgets used WHITE for primary text; correct that usage too.
            for prefix in ("", "active"):
                fg_key, bg_key = prefix + "foreground", prefix + "background"
                source_fg = self._canonical(current.get(fg_key, ""))
                target_bg = self._canonical(updates.get(bg_key, ""))
                if source_fg in {"#ffffff", theme.DARK["WHITE"].lower()} and target_bg in {
                    theme.DARK["ACCENT"].lower(), theme.DARK["ACCENT_DEEP"].lower(),
                    theme.DARK["ERROR"].lower(), theme.DARK["ROSE"].lower(),
                }:
                    updates[fg_key] = theme.DARK["ON_ACCENT"]
                    if target_bg == theme.DARK["ERROR"].lower():
                        updates[bg_key] = "#903D54"
                    elif target_bg == theme.DARK["ROSE"].lower():
                        updates[bg_key] = "#874C66"
                    else:
                        updates[bg_key] = theme.DARK["ACCENT_DEEP"]
        changed = {option: value for option, value in updates.items() if current.get(option) != value}
        try:
            if changed:
                widget.configure(**changed)
        except tk.TclError:
            # A native control may reject one otherwise advertised option;
            # retain the previous per-option fallback for that control only.
            for option, value in changed.items():
                try:
                    widget.configure(**{option: value})
                except tk.TclError:
                    continue
        for option, value in updates.items():
            if option in states:
                states[option].expected = value
        if isinstance(widget, tk.Canvas) and not fixed_canvas:
            self._apply_canvas(widget, mode)
        if isinstance(widget, tk.Text):
            self._apply_text_tags(widget, mode)
        if isinstance(widget, tk.Checkbutton):
            from .rounded_controls import style_checkbox
            style_checkbox(widget, mode)
        if getattr(widget, '_remchat_soft_field', False):
            widget._field_host.schedule()

    def _apply_canvas(self, canvas: tk.Canvas, mode: str) -> None:
        item_states = self._canvas.setdefault(canvas, {})
        existing = set(canvas.find_all())
        for identifier in list(item_states):
            if identifier not in existing:
                del item_states[identifier]
        for identifier in existing:
            if "theme-fixed" in canvas.gettags(identifier):
                continue
            states = item_states.setdefault(identifier, {})
            kind = canvas.type(identifier)
            for option in ("fill", "outline", "activefill", "activeoutline", "disabledfill", "disabledoutline"):
                try:
                    color = str(canvas.itemcget(identifier, option))
                except tk.TclError:
                    continue
                if not color:
                    continue
                state = self._state(states, option, color, option, canvas,
                                    text_item=kind == "text", use_explicit=False)
                value = self._value(state, option, mode, text_item=kind == "text")
                try:
                    if color != value:
                        canvas.itemconfigure(identifier, **{option: value})
                    state.expected = value
                except tk.TclError:
                    continue

    def _apply_text_tags(self, text: tk.Text, mode: str) -> None:
        states = self._text_tags.setdefault(text, {})
        for tag in text.tag_names():
            if tag == "sel":
                continue
            for option in ("foreground", "background"):
                try:
                    color = str(text.tag_cget(tag, option))
                except tk.TclError:
                    continue
                if not color:
                    continue
                state = self._state(states, (tag, option), color, option, text, use_explicit=False)
                value = self._value(state, option, mode)
                if color != value:
                    text.tag_configure(tag, **{option: value})
                state.expected = value

    def configure_ttk(self, mode: str | None = None) -> None:
        selected = theme.get_mode() if mode is None else mode
        self._ttk_mode = selected
        colors = theme.palette(selected)
        style = ttk.Style(self.root)
        if self._native_ttk_theme is None:
            self._native_ttk_theme = style.theme_use()
        # Native Windows themes ignore important dark field colors.
        desired = "clam" if "clam" in style.theme_names() else self._native_ttk_theme
        if style.theme_use() != desired:
            style.theme_use(desired)
        style.configure(".", background=colors["WHITE"], foreground=colors["INK"],
                        fieldbackground=colors["WHITE"], bordercolor=colors["LINE"],
                        lightcolor=colors["LINE"], darkcolor=colors["LINE"],
                        selectbackground=colors["ACCENT_DEEP"], selectforeground=colors["ON_ACCENT"])
        style.configure("Treeview", background=colors["WHITE"], fieldbackground=colors["WHITE"],
                        foreground=colors["INK"], bordercolor=colors["LINE"])
        style.map("Treeview", background=[("selected", colors["ACCENT_DEEP"])],
                  foreground=[("selected", colors["ON_ACCENT"])])
        style.configure("Treeview.Heading", background=colors["SIDEBAR"], foreground=colors["INK"])
        style.map("Treeview.Heading", background=[("active", colors["ACCENT_SOFT"])])
        for name in ("TCombobox", "TEntry", "TSpinbox"):
            style.configure(name, background=colors["SIDEBAR"], fieldbackground=colors["WHITE"],
                            foreground=colors["INK"], arrowcolor=colors["MUTED"], bordercolor=colors["LINE"])
            style.map(name, fieldbackground=[("readonly", colors["WHITE"]), ("disabled", colors["SIDEBAR"])],
                      foreground=[("disabled", colors["MUTED"]), ("readonly", colors["INK"])],
                      selectbackground=[("!disabled", colors["ACCENT_DEEP"])],
                      selectforeground=[("!disabled", colors["ON_ACCENT"])])
        # ttk 下拉框的弹出列表是 Tcl 内部 Listbox，不在 Python child 树中。
        for option, role in (("background", "WHITE"), ("foreground", "INK"),
                             ("selectBackground", "ACCENT_DEEP"), ("selectForeground", "ON_ACCENT")):
            self.root.option_add(f"*TCombobox*Listbox.{option}", colors[role])
        self._refresh_combobox_popdowns(colors)
        for name in ("TScrollbar", "Vertical.TScrollbar", "Horizontal.TScrollbar"):
            style.configure(name, background=colors["LINE"], troughcolor=colors["APP_BG"],
                            arrowcolor=colors["MUTED"], bordercolor=colors["APP_BG"])
            style.map(name, background=[("active", colors["ACCENT_DEEP"])])
        style.configure("TNotebook", background=colors["APP_BG"], bordercolor=colors["LINE"])
        style.configure("TNotebook.Tab", background=colors["SIDEBAR"], foreground=colors["MUTED"],
                        font=(theme.BODY_FONT, 10), padding=(12, 8))
        style.map("TNotebook.Tab", background=[("selected", colors["WHITE"])],
                  foreground=[("selected", colors["INK"])])
        from .rounded_controls import configure_ttk_rounding
        configure_ttk_rounding(self.root, colors)

    def _refresh_combobox_popdowns(self, colors: dict[str, str]) -> None:
        """Recolor already-created Tcl popdowns without posting or recreating them.

        option_add supplies defaults only at creation. Tk caches each combobox's
        popup after its first opening, outside Python's widget tree. Updating
        that existing Listbox preserves the current value, selection and focus.
        """
        for widget in self._walk(self.root):
            if not isinstance(widget, ttk.Combobox):
                continue
            listbox = f"{widget}.popdown.f.l"
            try:
                if not widget.tk.getboolean(widget.tk.call("winfo", "exists", listbox)):
                    continue
                widget.tk.call(listbox, "configure",
                    "-background", colors["WHITE"], "-foreground", colors["INK"],
                    "-selectbackground", colors["ACCENT_DEEP"], "-selectforeground", colors["ON_ACCENT"])
            except tk.TclError:
                # An unsupported native popup or a concurrently destroyed one
                # keeps its platform behavior; do not break the settings page.
                continue
