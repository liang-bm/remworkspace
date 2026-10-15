"""Native offline kitchen, using the farm's existing pixel Rem sprites.

Scene furniture and action effects are Canvas shapes, not generated assets.
Navigation and actions also have ordinary focusable Tk buttons.
"""
from __future__ import annotations

import math
from pathlib import Path
import tkinter as tk

from PIL import Image, ImageTk

from ..plaza.cooking import CookingModel, RECIPES, STATION_NAMES
from . import theme
from .locale_controller import translator_for


ASSETS = Path(__file__).resolve().parents[1] / "assets" / "farm"
SCENE_WIDTH, SCENE_HEIGHT = 640, 360
STATIONS = {
    "sink": (60, 166, 181, 244), "board": (185, 166, 306, 244),
    "stove": (310, 166, 431, 244), "serve": (435, 166, 556, 244),
}
SCENE = {
    "wall": "#F1E5D0", "wall_line": "#DDCFB6", "wood": "#9B6948",
    "wood_light": "#C39468", "wood_dark": "#684735", "floor": "#C5A076",
    "floor_line": "#AA825A", "counter": "#E8D0A4", "tile": "#F9F3E6",
    "sink": "#93B9C5", "sink_inner": "#577E8A", "metal": "#536476",
    "pot": "#43515D", "plate": "#E6F2F3", "tea": "#BE8B49",
    "accent": "#286A8F", "highlight": "#D6EEFC", "ink": "#3B493D",
    "shadow": "#AE9072", "white": "#FFFFFF", "water": "#8CD6EF",
    "carrot": "#E99B42", "radish": "#F9F3DF", "flower": "#D789B1",
    "green": "#628855", "flame": "#F2B45F",
}
# UI feedback timing only; recipe success is never decided by a timer.
EFFECT_INTERVAL_MS, EFFECT_FRAMES = 40, 12


class FarmKitchen(tk.Frame):
    def __init__(self, parent, store, on_exit, environment=None):
        super().__init__(parent, bg=theme.WHITE)
        self.store, self.on_exit = store, on_exit
        self.model = CookingModel(store)
        self.environment = dict(environment) if isinstance(environment, dict) else {}
        self._closed = self._paused = False
        self._timers = set()
        self._effect = None
        self._scale, self._origin = 1.0, (0.0, 0.0)
        self._images, self._photos = {}, {}
        self._asset_errors = []
        self.motion = tk.BooleanVar(self, True)
        self.message = tk.StringVar(self, "雷姆：一起做点暖暖的料理吧。先在水槽洗萝卜。")
        self.step_text = tk.StringVar(self)
        self.history_text = tk.StringVar(self)
        self.notice_text = tk.StringVar(self)
        self._message_source = (self.message.get(), {})
        self._remchat_retranslate = self._refresh
        self._load_assets()
        self._build_widgets()
        self.bind("<Destroy>", self._destroyed, add="+")
        self.bind("<Unmap>", self._unmapped, add="+")
        self.bind("<Map>", self._mapped, add="+")
        self._refresh()

    def _button(self, parent, text, command, *, primary=False):
        colors = theme.palette()
        button = tk.Button(parent, text=text, command=command, font=(theme.BODY_FONT, 10),
            bg=colors["ACCENT_DEEP"] if primary else colors["APP_BG"],
            fg=colors["ON_ACCENT"] if primary else colors["INK"],
            activebackground=colors["ACCENT_SOFT"], activeforeground=colors["INK"],
            disabledforeground=colors["MUTED"], bd=0, padx=12, pady=9,
            highlightthickness=2, highlightbackground=colors["WHITE"],
            highlightcolor=colors["ACCENT_DEEP"], cursor="hand2", takefocus=True)
        button.bind("<Escape>", lambda _: self.exit(), add="+")
        return button

    def _label(self, parent, *, text=None, variable=None, muted=False, bold=False, size=10):
        label = tk.Label(parent, text=text, textvariable=variable, bg=theme.WHITE,
            fg=theme.MUTED if muted else theme.INK, anchor="w", justify="left",
            font=(theme.BODY_FONT, size, "bold" if bold else "normal"))
        label.bind("<Configure>", lambda e: label.configure(wraplength=max(80, e.width)))
        return label

    def _build_widgets(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        header = tk.Frame(self, bg=theme.WHITE)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        header.columnconfigure(0, weight=1)
        self._label(header, text="雷姆的小厨房", bold=True, size=14).grid(row=0, column=0, sticky="w")
        self.exit_button = self._button(header, "← 返回农场", self.exit)
        self.exit_button.grid(row=0, column=1, sticky="e")
        self._label(self, text="练习材料无限 · 不扣农场作物 · 纯本地，不调用模型", muted=True, size=9).grid(
            row=1, column=0, sticky="ew", pady=(0, 8))
        self.recipe_bar = tk.Frame(self, bg=theme.WHITE)
        self.recipe_bar.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.recipe_buttons = {}
        for index, (key, recipe) in enumerate(RECIPES.items()):
            button = self._button(self.recipe_bar, recipe.name, lambda selected=key: self.select_recipe(selected))
            self.recipe_buttons[key] = button
            button.grid(row=0, column=index, sticky="ew", padx=(0, 6 if index < 2 else 0))
            self.recipe_bar.columnconfigure(index, weight=1)
        self.recipe_bar.bind("<Configure>", self._reflow_recipes)
        self.canvas = tk.Canvas(self, width=640, height=300, bg=theme.APP_BG,
            highlightthickness=2, highlightbackground=theme.LINE,
            highlightcolor=theme.ACCENT_DEEP, takefocus=True, cursor="hand2")
        self.canvas._remchat_theme_fixed = True
        self.canvas.grid(row=3, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _: self.draw())
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<space>", self._key_action)
        self.canvas.bind("<Return>", self._key_action)
        self.canvas.bind("<Escape>", lambda _: self.exit())
        detail = tk.Frame(self, bg=theme.WHITE)
        detail.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        detail.columnconfigure(0, weight=1)
        self._label(detail, variable=self.step_text, bold=True).grid(row=0, column=0, sticky="ew")
        self._label(detail, variable=self.message).grid(row=1, column=0, sticky="ew", pady=(5, 0))
        controls = tk.Frame(self, bg=theme.WHITE)
        controls.grid(row=5, column=0, sticky="ew", pady=(8, 4))
        controls.columnconfigure(0, weight=1)
        self.action_button = self._button(controls, "洗萝卜", self.perform_action, primary=True)
        self.action_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.restart_button = self._button(controls, "重新练习", self.restart)
        self.restart_button.grid(row=0, column=1, sticky="e")
        history = tk.Frame(self, bg=theme.WHITE)
        history.grid(row=6, column=0, sticky="ew", pady=(4, 0))
        history.columnconfigure(0, weight=1)
        self._label(history, variable=self.history_text, muted=True, size=9).grid(row=0, column=0, sticky="ew")
        self.motion_button = tk.Checkbutton(history, text="动作反馈", variable=self.motion,
            command=self._motion_changed, bg=theme.WHITE, fg=theme.INK,
            activebackground=theme.WHITE, selectcolor=theme.ACCENT_SOFT,
            font=(theme.BODY_FONT, 9), takefocus=True)
        self.motion_button.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self.motion_button.bind("<Escape>", lambda _: self.exit(), add="+")
        recovery = tk.Frame(self, bg=theme.WHITE)
        recovery.grid(row=7, column=0, sticky="ew", pady=(4, 0))
        recovery.columnconfigure(0, weight=1)
        self.notice_label = self._label(recovery, variable=self.notice_text, muted=True, size=9)
        self.notice_label.grid(row=0, column=0, sticky="ew")
        self.reload_button = self._button(recovery, "重新读取记录", self.reload_records)
        self.reload_button.grid(row=0, column=1, sticky="e", padx=(8, 0))

    def _reflow_recipes(self, event):
        # Wrap the collection before ever shrinking the dish labels.
        required = max(button.winfo_reqwidth() for button in self.recipe_buttons.values()) + 6
        columns = max(1, min(len(RECIPES), event.width // required))
        for column in range(len(RECIPES)):
            self.recipe_bar.columnconfigure(column, weight=1 if column < columns else 0)
        for index, button in enumerate(self.recipe_buttons.values()):
            button.grid(row=index // columns, column=index % columns, sticky="ew",
                padx=(0, 6 if index % columns < columns - 1 else 0), pady=(0, 4 if columns < 3 else 0))

    def _load_assets(self):
        for name in ("rem-down-0", "rem-down-1", "rem-left-0", "rem-right-0",
                     "radish-2", "carrot-2", "flower-2"):
            try:
                with Image.open(ASSETS / f"{name}.png") as source:
                    if source.format != "PNG":
                        raise ValueError("素材应为 PNG")
                    self._images[name] = source.convert("RGBA")
            except (OSError, ValueError, Image.DecompressionBombError):
                self._asset_errors.append(name + ".png")

    def _photo(self, name, scale=1.0):
        image = self._images.get(name)
        if image is None:
            return None
        key = (name, round(scale, 4))
        if key not in self._photos:
            size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
            self._photos[key] = ImageTk.PhotoImage(image.resize(size, Image.Resampling.NEAREST), master=self)
        return self._photos[key]

    def _refresh(self):
        if self._closed:
            return
        colors = theme.palette()
        tr = translator_for(self)
        source, values = self._message_source
        def copy(value):
            for suffix in (' 已记入成品记录。', ' 本次仅练习，成品未写入记录；原文件已保留。'):
                if value.endswith(suffix):
                    return tr.display(value[:-len(suffix)]) + ' ' + tr.display(suffix.strip())
            return tr.display(value)
        self.message.set(tr(source, **{key: copy(value) for key, value in values.items()}))
        for key, button in self.recipe_buttons.items():
            selected = key == self.model.recipe_id
            button.configure(bg=colors["ACCENT_DEEP"] if selected else colors["APP_BG"],
                fg=colors["ON_ACCENT"] if selected else colors["INK"])
        step = self.model.current_step
        if step:
            done = self.model.step_index
            progression = " → ".join(("✓ " if index < done else "") + tr.display(item.label)
                for index, item in enumerate(self.model.recipe.steps))
            self.step_text.set(f"{done + 1}/4 · {tr.display(step.instruction)}\n{progression}")
            self.action_button.configure(text=f"{done + 1}/4  {tr.display(step.label)}", state="normal")
            self.restart_button.configure(text="重新练习")
        else:
            self.step_text.set(tr('完成 · {recipe}{result}', recipe=tr.display(self.model.recipe.name),
                result=tr.display("已加入成品记录。" if self.model.last_saved else "完成了本次练习；记录未保存。")))
            self.action_button.configure(text="再做一份", state="normal")
            self.restart_button.configure(text="从头练习")
        counts = self.model.counts()
        count_text = " · ".join(tr('{recipe} {count}份', recipe=tr.display(recipe.name), count=counts[key])
                                for key, recipe in RECIPES.items())
        records = self.model.records
        if records:
            last = records[-1]
            known_recipe = RECIPES.get(last["recipe"])
            name = last.get("name") or (known_recipe.name if known_recipe else f"未知菜品（{last['recipe']}）")
            if known_recipe and name == known_recipe.name:
                name = tr.display(name)
            recent = tr('最近：{name} · {time}', name=name, time=last['completed_at'].replace('T', ' ')[:16])
        else:
            recent = tr('还没有成品记录，完成一道菜试试吧。')
        self.history_text.set(tr('成品记录 · {counts}\n{recent}', counts=count_text, recent=recent))
        notices = []
        if self.model.error:
            notices.append(f"只读练习：{self.model.error}。修复后点击右侧重新读取。")
        if self._asset_errors:
            notices.append("部分像素素材缺失：" + "、".join(self._asset_errors) + "；可继续用按钮练习。")
        self.notice_text.set("\n".join(notices) or "点击亮起的工位，或聚焦场景后按空格操作；Esc 返回。")
        self.reload_button.configure(state="normal" if self.model.error else "disabled")
        self.draw()

    def _set_message(self, source, **values):
        self._message_source = (source, values)

    def select_recipe(self, recipe_id):
        if self._closed or self._paused:
            return
        self._stop_effect()
        self.model.select_recipe(recipe_id)
        self._set_message('雷姆：来练习{recipe}。{instruction}', recipe=self.model.recipe.name,
                          instruction=self.model.current_step.instruction)
        self._refresh()

    def restart(self):
        if self._closed or self._paused:
            return
        self._stop_effect()
        self.model.restart()
        self._set_message('雷姆：再来一次吧。{instruction}', instruction=self.model.current_step.instruction)
        self._refresh()

    def perform_action(self):
        if self._closed or self._paused:
            return
        if self.model.completed:
            self.restart()
            return
        # An interrupted effect reaches its final state immediately. The
        # recipe state itself never waits on animation completion.
        self._stop_effect()
        result = self.model.perform_action()
        self._set_message('雷姆：{message}', message=result.message)
        self._effect = {"action": result.action, "station": result.station, "frame": 0}
        self._refresh()
        if self.motion.get() and self.winfo_ismapped():
            self._schedule(EFFECT_INTERVAL_MS, self._effect_tick)
        else:
            self._effect = None
            self.draw()

    def reload_records(self):
        if self._closed:
            return
        success = self.model.reload_records()
        self._set_message("雷姆：成品记录重新读好了，手上的练习没有改变。" if success else
                         "雷姆：记录还不能读取，原文件没有改动。练习材料仍然无限。")
        self._refresh()

    def _key_action(self, _event):
        self.perform_action()
        return "break"

    def _click(self, event):
        if self._closed or self._paused:
            return
        self.canvas.focus_set()
        x = (event.x - self._origin[0]) / self._scale
        y = (event.y - self._origin[1]) / self._scale
        if 562 <= x <= 616 and 222 <= y <= 337:
            self.exit()
            return
        for key, (left, top, right, bottom) in STATIONS.items():
            if left <= x <= right and top <= y <= bottom:
                step = self.model.current_step
                if self.model.completed:
                    self._set_message("雷姆：已经做好啦，点“再做一份”开始下一份练习。")
                    self._refresh()
                elif step.station == key:
                    self.perform_action()
                else:
                    self._set_message('雷姆：先去{station}{action}，再来这里吧。',
                                      station=STATION_NAMES[step.station], action=step.label)
                    self._refresh()
                return

    def _point(self, x, y):
        return self._origin[0] + x * self._scale, self._origin[1] + y * self._scale

    def _rect(self, left, top, right, bottom, fill, *, outline="", width=1, tags=()):
        return self.canvas.create_rectangle(*self._point(left, top), *self._point(right, bottom),
            fill=fill, outline=outline, width=max(1, width * self._scale), tags=tags)

    def _oval(self, left, top, right, bottom, fill, *, outline="", width=1, tags=()):
        return self.canvas.create_oval(*self._point(left, top), *self._point(right, bottom),
            fill=fill, outline=outline, width=max(1, width * self._scale), tags=tags)

    def _line(self, points, fill, width=1, *, tags=(), **kwargs):
        translated = [coordinate for x, y in points for coordinate in self._point(x, y)]
        return self.canvas.create_line(*translated, fill=fill, width=max(1, width * self._scale), tags=tags, **kwargs)

    def _text(self, x, y, text, *, fill=None, size=10, bold=False, anchor="center", tags=()):
        return self.canvas.create_text(*self._point(x, y), text=translator_for(self).display(text),
            fill=fill or SCENE["ink"], anchor=anchor, tags=tags,
            font=(theme.BODY_FONT, max(8, round(size * min(1, self._scale))),
                  "bold" if bold else "normal"))

    def _rounded(self, box, radius, fill, *, outline="", tags=()):
        left, top, right, bottom = box
        r = min(radius, (right - left) / 2, (bottom - top) / 2)
        points = ((left+r, top), (right-r, top), (right, top), (right, top+r),
            (right, bottom-r), (right, bottom), (right-r, bottom), (left+r, bottom),
            (left, bottom), (left, bottom-r), (left, top+r), (left, top))
        translated = [coordinate for x, y in points for coordinate in self._point(x, y)]
        return self.canvas.create_polygon(*translated, smooth=True, splinesteps=16,
            fill=fill, outline=outline, width=max(1, self._scale), tags=tags)

    def draw(self):
        if self._closed or not hasattr(self, "canvas"):
            return
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        scale = min(width / SCENE_WIDTH, height / SCENE_HEIGHT)
        if self._scale != scale:
            self._photos.clear()
        self._scale = max(scale, 0.001)
        self._origin = ((width - SCENE_WIDTH * self._scale) / 2,
                        (height - SCENE_HEIGHT * self._scale) / 2)
        self.canvas.delete("all")
        c = SCENE
        self._rounded((8, 10, 634, 354), 18, c["shadow"])
        self._rounded((4, 4, 630, 348), 18, c["wall"], outline=c["wall_line"])
        self._rect(20, 160, 614, 330, c["floor"])
        for y in range(170, 330, 24):
            self._line(((20, y), (614, y)), c["floor_line"])
            for x in range(20 + (48 if y % 48 else 0), 614, 96):
                self._line(((x, y), (x, min(y + 24, 330))), c["floor_line"])
        self._draw_window()
        self._rect(58, 76, 189, 84, c["wood"])
        for x in (76, 105, 145):
            self._rect(x, 54, x + 18, 76, c["tile"], outline=c["wood_light"])
            self._rect(x + 2, 49, x + 16, 55, c["wood_dark"])
        self._rect(389, 72, 534, 80, c["wood"])
        for x in (409, 442, 478):
            self._line(((x, 80), (x, 103)), c["metal"], 3)
            self._oval(x - 7, 97, x + 7, 117, c["metal"])
        self._text(330, 135, "雷姆的小厨房", size=12, bold=True)
        self._rect(54, 163, 563, 246, c["wood_dark"])
        self._rect(56, 165, 560, 184, c["counter"])
        self._rect(58, 184, 558, 238, c["wood_light"])
        for key, box in STATIONS.items():
            left, _, right, _ = box
            self._rect(left + 4, 189, right - 4, 232, c["wood"], outline=c["wood_dark"])
            self._rect((left + right) / 2 - 5, 197, (left + right) / 2 + 5, 201, c["counter"])
            step = self.model.current_step
            active = step is not None and step.station == key
            if active:
                self._rounded((left, 146, right, 167), 7, c["highlight"], outline=c["accent"])
            self._text((left + right) / 2, 155, ("当前 · " if active else "") + STATION_NAMES[key],
                fill=c["accent"] if active else c["ink"], size=9, bold=active, tags=("station", key))
        self._draw_sink()
        self._draw_board()
        self._draw_stove()
        self._draw_serving()
        self._draw_door()
        self._draw_actor()
        self._draw_effect()
        environment_label = self.environment.get("label", "窗外是农场，屋里暖暖的。")
        self._text(36, 316, str(environment_label), size=9, anchor="w", tags="environment")

    def _draw_window(self):
        c = SCENE
        period, season = self.environment.get("period", "day"), self.environment.get("season", "spring")
        sky = {"dawn": "#D8C9D6", "day": "#ADD8E5", "dusk": "#E7B995", "night": "#344A6A"}.get(period, "#ADD8E5")
        foliage = {"spring": "#87A770", "summer": "#6F9B56", "autumn": "#BD935D", "winter": "#D3DCE1"}.get(season, "#87A770")
        self._rect(222, 29, 356, 113, c["wood_dark"])
        self._rect(228, 35, 350, 107, sky)
        self._rect(228, 84, 350, 107, foliage)
        self._oval(244, 45, 260, 61, "#F7EFCD")
        self._rect(286, 35, 292, 107, c["wood_light"])
        self._rect(228, 72, 350, 78, c["wood_light"])
        self._rect(214, 27, 222, 116, "#7EA6B1")
        self._rect(356, 27, 364, 116, "#7EA6B1")

    def _draw_sink(self):
        c = SCENE
        self._oval(76, 169, 163, 182, c["sink"])
        self._oval(86, 173, 155, 181, c["sink_inner"])
        self._line(((144, 173), (144, 155), (133, 155), (133, 166)), c["metal"], 4)
        if self.model.step_index == 0:
            self._draw_ingredient(116, 174, .8)

    def _draw_board(self):
        c = SCENE
        self._rounded((208, 169, 279, 182), 3, c["counter"], outline=c["wood_dark"])
        self._line(((262, 166), (276, 157)), c["metal"], 3)
        if self.model.step_index in (1, 2):
            self._draw_ingredient(237, 171, .9, cut=self.model.step_index == 2)

    def _draw_stove(self):
        c = SCENE
        self._rect(324, 167, 417, 181, c["metal"])
        self._oval(336, 165, 366, 179, c["pot"])
        self._oval(379, 165, 404, 179, c["pot"])
        self._rect(339, 157, 364, 174, c["pot"])
        self._oval(338, 153, 365, 162, c["metal"])
        self._line(((335, 159), (330, 159), (330, 167)), c["pot"], 3)
        self._line(((367, 159), (373, 159), (373, 167)), c["pot"], 3)
        if self.model.step_index >= 3:
            for x in (343, 351, 359):
                self._line(((x, 148), (x - 2, 142), (x + 1, 137)), "#E6EFEA", 2, smooth=True)

    def _draw_ingredient(self, x, y, scale, *, cut=False):
        c = SCENE
        crop = {"radish_soup": "radish", "carrot_stew": "carrot", "flower_tea": "flower"}[self.model.recipe_id]
        if cut:
            for offset in (-10, 0, 10):
                self._rect(x + offset - 4, y - 4, x + offset + 3, y + 1, c[crop])
            return
        # The farm sprite includes the whole mature plant. Keep this tabletop
        # ingredient smaller than the plant in a field, away from station text.
        photo = self._photo(crop + "-2", self._scale * scale * .55)
        if photo:
            self.canvas.create_image(*self._point(x, y + 6), image=photo, anchor="s", tags="ingredient")
        else:
            self._oval(x - 8, y - 10, x + 8, y + 2, c[crop])
            self._line(((x, y - 10), (x, y - 18)), c["green"], 3)

    def _draw_serving(self):
        c = SCENE
        tea = self.model.recipe_id == "flower_tea"
        if tea:
            self._rect(473, 165, 489, 180, c["plate"])
            self._oval(472, 162, 490, 169, c["tea"] if self.model.completed else c["tile"])
            self._line(((490, 168), (497, 168), (497, 175), (490, 175)), c["plate"], 3)
        else:
            self._oval(461, 166, 510, 181, c["plate"], outline=c["sink"])
            if self.model.completed:
                self._oval(470, 168, 501, 176, c["tea"])
                self._draw_ingredient(484, 172, .5, cut=True)
        if self.model.completed:
            self._rounded((437, 121, 555, 146), 7, c["highlight"], outline=c["accent"])
            self._text(496, 134, "完成啦！", fill=c["accent"], size=10, bold=True, tags="dish-complete")

    def _draw_door(self):
        c = SCENE
        self._rect(567, 218, 612, 333, c["wood_dark"])
        self._rect(572, 223, 607, 328, c["wood_light"])
        self._rect(578, 232, 601, 268, c["wood"])
        self._oval(599, 284, 603, 288, c["counter"])
        self._text(589, 340, "回农场", size=9, bold=True, tags="exit-door")

    def _draw_actor(self):
        step = self.model.current_step
        station = self._effect["station"] if self._effect else step.station if step else "serve"
        left, _, right, _ = STATIONS[station]
        x, y = (left + right) / 2, 282
        frame = self._effect["frame"] if self._effect else 0
        bob = math.sin(frame / EFFECT_FRAMES * math.pi) * 2 if self.motion.get() else 0
        self._oval(x - 16, y - 5, x + 16, y + 3, SCENE["shadow"])
        photo = self._photo("rem-down-1" if self._effect and frame % 4 < 2 else "rem-down-0", self._scale * 1.5)
        if photo:
            self.canvas.create_image(*self._point(x, y - bob), image=photo, anchor="s", tags="kitchen-rem")
        else:
            self._text(x, y - 22, "雷姆", bold=True, tags="kitchen-rem-fallback")

    def _draw_effect(self):
        if not self._effect:
            return
        effect, c = self._effect, SCENE
        left, _, right, _ = STATIONS[effect["station"]]
        x, frame = (left + right) / 2, effect["frame"]
        phase = frame / EFFECT_FRAMES
        if effect["action"] == "wash":
            for offset in (-16, -6, 6, 16):
                drop_y = 148 + (frame * 3 + offset) % 24
                self._line(((x + offset, drop_y), (x + offset, drop_y + 5)), c["water"], 2, tags="action-effect")
        elif effect["action"] in {"cut", "prepare"}:
            self._line(((x + 12, 163 - abs(math.sin(phase * math.pi * 3)) * 12),
                        (x + 24, 170 - abs(math.sin(phase * math.pi * 3)) * 12)),
                        c["metal"], 4, tags="action-effect")
        elif effect["action"] == "heat":
            for offset in (-8, 0, 8):
                self._oval(x + offset - 3, 170, x + offset + 3, 177, c["flame"], tags="action-effect")
                self._line(((x + offset, 150 - phase * 18), (x + offset - 3, 144 - phase * 18)),
                           c["white"], 2, tags="action-effect")
        else:
            for offset in (-18, 0, 18):
                self._line(((x + offset, 132 - phase * 14), (x + offset, 140 - phase * 14)),
                           c["white"], 2, tags="action-effect")

    def _schedule(self, delay, callback):
        if self._closed or self._paused:
            return
        timer = None
        def invoke():
            self._timers.discard(timer)
            if not self._closed and not self._paused:
                callback()
        timer = self.after(delay, invoke)
        self._timers.add(timer)

    def _effect_tick(self):
        if not self._effect or not self.motion.get() or not self.winfo_ismapped():
            self._stop_effect()
            self.draw()
            return
        self._effect["frame"] += 1
        if self._effect["frame"] >= EFFECT_FRAMES:
            self._stop_effect()
            self.draw()
            return
        self.draw()
        self._schedule(EFFECT_INTERVAL_MS, self._effect_tick)

    def _stop_effect(self):
        for timer in tuple(self._timers):
            try:
                self.after_cancel(timer)
            except tk.TclError:
                pass
        self._timers.clear()
        self._effect = None

    def _motion_changed(self):
        self._stop_effect()
        self.draw()

    def set_environment(self, environment):
        if self._closed:
            return
        self.environment = dict(environment) if isinstance(environment, dict) else {}
        self.draw()

    def pause(self):
        self._paused = True
        self._stop_effect()

    def resume(self):
        if not self._closed:
            self._paused = False
            self.draw()

    def close(self):
        self._closed = True
        self.pause()

    def exit(self):
        if self._closed:
            return "break"
        self.pause()
        if callable(self.on_exit):
            self.on_exit()
        return "break"

    def _unmapped(self, event):
        if event.widget is self:
            self.pause()

    def _mapped(self, event):
        if event.widget is self:
            self.resume()

    def _destroyed(self, event):
        if event.widget is self:
            self.close()
