"""可操控像素雷姆的小农场；Canvas 只在用户动作/生长刷新时更新。

THESIS: 雷姆真正走到地块前，用工具耕作，而不是遥控九个按钮。
OWN-WORLD: 原创高密度像素地图、方向行走精灵，周边沿用蓝白 UI。
STORY: 移动、选工具、耕种、等待生长、收获，旧存档继续可用。
FIRST VIEWPORT: 地图占主区，工具栏和操作状态紧邻地图。
FORM: 替换现有花园绘制器，模型/棋盘/其他页面不变。
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import math
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from ..plaza.farm_world import FarmWorld, PLOTS, ENTRY_GATE, viewport_geometry
from ..plaza.farm_layout import BLOCKED_PLOTS, NAV_STEP, SOIL_SIZE, OBJECTS, BY_ID, object_at, plot_at
from ..plaza.garden import CROPS
from ..plaza.farm_calendar import calendar_state
from .farm_renderer import FarmRenderer
from . import theme


ASSETS = Path(__file__).resolve().parents[1] / "assets" / "farm"
TOOLS = ("耕地", "播种", "浇水", "收获")
KEYS = {"w": "up", "Up": "up", "s": "down", "Down": "down",
        "a": "left", "Left": "left", "d": "right", "Right": "right"}


class PixelFarm(tk.Frame):
    def __init__(self, parent, host):
        super().__init__(parent, bg=theme.WHITE)
        self.host = host
        self.store = host.store
        self._world_error = None
        self._world_file = self.store.path / "farm-world.json"
        self._world_bytes = None
        try:
            if self._world_file.is_symlink():
                raise ValueError("人物存档是符号链接，已停止写入")
            self._world_bytes = self._world_file.read_bytes() if self._world_file.exists() else None
            raw = json.loads(self._world_bytes.decode("utf-8-sig")) if self._world_bytes is not None else {}
            self.world = FarmWorld.restore(raw)
        except (ValueError, OSError) as error:
            self.world = FarmWorld()
            self._world_error = str(error)
        self.tool = tk.IntVar(self, 0)
        self.crop = tk.StringVar(self, "radish")
        self.message = tk.StringVar(self, "中央整片草地都能耕种；点击空草地走过去，按空格耕地。")
        self._timer = None
        self._path = []
        self._arrival = None
        self._frame = 0
        self._images = {}
        self._asset_errors = []
        self._photos = {}
        self._scale = 1
        self._origin = (0, 0)
        self._anim = None
        self._effect = None
        self._effect_timer = None
        self._water_timer = None
        self._water_phase = 0
        self._clock_timer = None
        self._environment = calendar_state()
        self.overview = tk.BooleanVar(self, False)
        self.motion = tk.BooleanVar(self, True)
        self._closed = False
        self._load_assets()
        self._renderer = FarmRenderer(ASSETS, self._images.get("map"))
        self._renderer.set_environment(self._environment["season"], self._environment["period"])
        bar = tk.Frame(self, bg=theme.WHITE)
        bar.pack(fill="x", pady=(0, 10))
        self.buttons = []
        for index, label in enumerate(TOOLS):
            button = tk.Button(bar, text=f"{index + 1}  {label}",
                command=lambda n=index: self.choose_tool(n), font=(theme.BODY_FONT, 10),
                bd=0, padx=12, pady=8, cursor="hand2", takefocus=True)
            self.buttons.append(button)
        def reflow_tools(event):
            needed = max(button.winfo_reqwidth() for button in self.buttons) + 6
            columns = max(1, min(4, event.width // needed))
            for column in range(4):
                bar.columnconfigure(column, weight=1 if column < columns else 0)
            for n, button in enumerate(self.buttons):
                button.grid(row=n // columns, column=n % columns, sticky="ew", padx=(0, 6), pady=(0, 5))
        bar.bind("<Configure>", reflow_tools)
        for n, button in enumerate(self.buttons):
            button.grid(row=0, column=n, padx=(0, 6), pady=(0, 5))
        view_bar = tk.Frame(self, bg=theme.WHITE)
        view_bar.pack(fill="x", pady=(0, 8))
        tk.Checkbutton(view_bar, text="全图视角", variable=self.overview, command=self.draw,
            bg=theme.WHITE, fg=theme.INK, activebackground=theme.WHITE,
            selectcolor=theme.ACCENT_SOFT, font=(theme.BODY_FONT, 9)).pack(side="left")
        tk.Checkbutton(view_bar, text="动作反馈", variable=self.motion,
            command=self._motion_changed, bg=theme.WHITE, fg=theme.INK,
            activebackground=theme.WHITE, selectcolor=theme.ACCENT_SOFT,
            font=(theme.BODY_FONT, 9)).pack(side="right")
        environment_bar = tk.Frame(self, bg=theme.WHITE)
        environment_bar.pack(fill="x", pady=(0, 8))
        self.environment_label = tk.Label(environment_bar, bg=theme.WHITE, fg=theme.INK,
            font=(theme.BODY_FONT, 9), anchor="w", justify="left")
        self.environment_label.pack(fill="x")
        choices = tk.Frame(environment_bar, bg=theme.WHITE)
        choices.pack(fill="x", pady=(4, 0))
        tk.Label(choices, text="场景预览", bg=theme.WHITE, fg=theme.MUTED,
            font=(theme.BODY_FONT, 9)).pack(side="left", padx=(0, 6))
        self.season_choice = ttk.Combobox(choices, state="readonly", width=9,
            values=("自动季节", "春", "夏", "秋", "冬"))
        self.season_choice._remchat_ui_choice_labels = ("自动季节", "春", "夏", "秋", "冬")
        self.season_choice.current(0)
        self.season_choice.pack(side="left", padx=(0, 6))
        self.period_choice = ttk.Combobox(choices, state="readonly", width=9,
            values=("自动昼夜", "清晨", "白天", "黄昏", "夜间"))
        self.period_choice._remchat_ui_choice_labels = ("自动昼夜", "清晨", "白天", "黄昏", "夜间")
        self.period_choice.current(0)
        self.period_choice.pack(side="left", padx=(0, 6))
        for widget in (self.season_choice, self.period_choice):
            widget.bind("<<ComboboxSelected>>", lambda _event: self.draw())
        self.canvas = tk.Canvas(self, bg="#477443", highlightthickness=0,
            width=512, height=384, takefocus=True, cursor="hand2")
        self.canvas._remchat_theme_fixed = True
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _: self.draw())
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<KeyPress>", self._key)
        self.canvas.bind("<FocusOut>", lambda _: self._stop_walk())
        self.canvas.bind("<Unmap>", lambda _: self._pause_surface())
        self.canvas.bind("<Map>", lambda _: self.draw())
        caption = tk.Label(self, textvariable=self.message, bg=theme.WHITE,
            fg=theme.INK, font=(theme.BODY_FONT, 10), justify="left", anchor="w")
        caption.pack(fill="x", pady=(10, 4))
        caption.bind("<Configure>", lambda e: caption.configure(wraplength=max(80, e.width)))
        if self._asset_errors:
            tk.Label(self, text="农场素材缺失或损坏；存档未更改。请修复：" + "、".join(self._asset_errors),
                     bg=theme.WHITE, fg=theme.ERROR, font=(theme.BODY_FONT, 9),
                     wraplength=450, anchor="w", justify="left").pack(fill="x", pady=5)
        notices = [self._world_error, self._renderer.notice]
        if self.world.source_version < 3 or host.garden.source_version < 2:
            notices.append("旧存档已在内存扩展为连续耕地；首次保存各自存档前会备份原文件，已有作物保留。")
        if self.world.relocated:
            notices.append("旧位置落在新障碍上，雷姆暂时移到最近的可达位置；原文件尚未更改。")
        if any(notices):
            tk.Label(self, text="\n".join(filter(None, notices)), bg=theme.WHITE,
                fg=theme.MUTED, font=(theme.BODY_FONT, 9), anchor="w",
                justify="left", wraplength=480).pack(fill="x", pady=5)
        self.choose_tool(0, focus=False)
        self.bind("<Destroy>", self._destroyed, add="+")

    def _load_assets(self):
        names = ["map", "soil-0", "soil-1", "soil-2", "hoe", "seeds", "watering-can"]
        for direction in ("down", "left", "right", "up"):
            for index in range(3):
                names.append(f"rem-{direction}-{index}")
        for crop in CROPS:
            for index in range(3):
                names.append(f"{crop}-{index}")
        for name in names:
            try:
                with Image.open(ASSETS / f"{name}.png") as image:
                    sprite = image.convert("RGBA")
                    if name.split("-")[0] in CROPS:
                        bounds = sprite.getchannel("A").getbbox()
                        if bounds and bounds[3] < sprite.height:
                            grounded = Image.new("RGBA", sprite.size)
                            grounded.alpha_composite(sprite, (0, sprite.height - bounds[3]))
                            sprite = grounded
                    self._images[name] = sprite
            except (OSError, ValueError, Image.DecompressionBombError):
                self._asset_errors.append(name + ".png")

    def _photo(self, name):
        if name not in self._photos and name in self._images:
            image = self._images[name]
            size = (max(1, round(image.width * self._scale)),
                    max(1, round(image.height * self._scale)))
            image = self._renderer.sprite_image(name, image)
            self._photos[name] = ImageTk.PhotoImage(image.resize(size, Image.Resampling.NEAREST), master=self)
        return self._photos.get(name)

    def choose_tool(self, index, *, focus=True):
        self.tool.set(index)
        colors = theme.palette()
        for n, button in enumerate(self.buttons):
            button.configure(bg=colors["ACCENT_DEEP"] if n == index else colors["APP_BG"],
                             fg=colors["ON_ACCENT"] if n == index else colors["INK"],
                             activebackground=colors["ACCENT_SOFT"], activeforeground=colors["INK"])
        if focus:
            self.canvas.focus_set()

    def draw(self):
        if self._closed:
            return
        self._refresh_environment()
        self._sync_clock_timer()
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        # 主视角铺满画布且保持像素比例；摄像机受地图边缘约束。
        new_scale, self._origin = viewport_geometry(width, height,
            self._anim or (self.world.x, self.world.y), overview=self.overview.get())
        if new_scale != self._scale:
            self._scale = new_scale
            self._photos.clear()
        x0, y0 = self._origin
        tile = SOIL_SIZE * self._scale
        self.canvas.delete("all")
        if not self._renderer.draw_ground(self.canvas, self._origin, self._scale, self):
            self.message.set("农场素材尚未安装；存档已保留。")
            return
        self._renderer.draw_water(self.canvas, self._origin, self._scale, self, self._water_phase)
        entities = []
        gate_openness = float(self.world.gate_open)
        if self._effect and self._effect["kind"] == "gate" and self.motion.get():
            fraction = self._effect["frame"] / 10
            gate_openness = fraction if self.world.gate_open else 1 - fraction
        for piece in self._renderer.pieces:
            callback = lambda p=piece: self._renderer.draw_piece(self.canvas, p,
                self._origin, self._scale, self, gate_openness)
            if piece.ground_surface:
                callback()
            else:
                entities.append((piece.depth, 0, callback))
        soil_states = {index: (2 if plot.get("watered") else 1)
            for index, plot in enumerate(self.host.garden.plots)
            if index not in BLOCKED_PLOTS and (plot or index in self.world.tilled)}
        for index, (x, y) in enumerate(PLOTS):
            if index in BLOCKED_PLOTS:
                continue  # Old data remains in its slot, but is neither drawn nor usable.
            plot = self.host.garden.plots[index]
            soil = soil_states.get(index, 0)
            px, py = self._point((x, y))
            if soil:
                self._renderer.draw_soil(self.canvas, index, soil, soil_states,
                    self._origin, self._scale, self)
            if plot:
                progress = self.host.garden.progress(index)
                stage = 2 if progress >= 1 else 1 if progress >= .3 else 0
                entities.append((y + 12, 1, lambda px=px, py=py, plot=plot,
                    stage=stage, ready=progress >= 1: self._draw_crop(px, py, plot, stage, ready)))
        target = self.world.target_plot()
        if target is not None:
            x, y = PLOTS[target]
            px, py = self._point((x, y))
            self.canvas.create_rectangle(px - tile / 2, py - tile / 2,
                px + tile / 2, py + tile / 2, outline="#F7DE82", width=2, tags="plot-target")
            self.host._selected_plot = target
        entities.append(((self._anim or (self.world.x, self.world.y))[1], 2, self._draw_avatar))
        for _depth, _order, callback in sorted(entities, key=lambda item: item[:2]):
            callback()
        self._renderer.draw_atmosphere(self.canvas, self._origin, self._scale, self, self._water_phase)
        self._draw_landmarks()
        self._draw_effect()
        self._sync_water_timer()

    def _draw_crop(self, px, py, plot, stage, ready):
        tile = SOIL_SIZE * self._scale
        photo = self._photo(f"{plot['crop']}-{stage}")
        if photo:
            # Compiled crop sprites already share a bottom baseline. Center
            # anchoring made larger growth stages appear to change the ground.
            self.canvas.create_image(px, py + tile * .25, image=photo, anchor="s", tags="crop")
        if ready:
            self.canvas.create_text(px, py - tile * .55, text="✓", fill="#FFFFFF",
                font=(theme.BODY_FONT, 11, "bold"), tags="crop")

    def _point(self, position):
        return (self._origin[0] + position[0] * self._scale,
                self._origin[1] + position[1] * self._scale)

    def _draw_landmarks(self):
        """Hints only; wells, dock and gate are the actual transparent scene art."""
        if self._effect:
            return  # The success caption already explains the current action.
        for item in OBJECTS:
            if not item.interaction or not any(abs(self.world.x - pad.point[0]) +
                abs(self.world.y - pad.point[1]) <= 32 for pad in item.pads):
                continue
            pad = min(item.pads, key=lambda pad: abs(self.world.x - pad.point[0]) + abs(self.world.y - pad.point[1]))
            x, y = self._point((pad.point[0], pad.point[1] + 12))
            label = "开关小门 · E" if item.id == "gate" else "取水 · E"
            anchor = "w" if x < 90 else "e" if x > self.canvas.winfo_width() - 90 else "center"
            self.canvas.create_text(x + 1, y + 1, text=label, fill="#263B32",
                font=(theme.BODY_FONT, 9, "bold"), anchor=anchor, tags="landmark-label-shadow")
            self.canvas.create_text(x, y, text=label, fill="#FFFFFF",
                font=(theme.BODY_FONT, 9, "bold"), anchor=anchor, tags="landmark-label")

    def _draw_avatar(self):
        self.canvas.delete("avatar")
        photo = self._photo(f"rem-{self.world.facing}-{self._frame}")
        if not photo:
            return
        x, y = self._anim or (self.world.x, self.world.y)
        tile = SOIL_SIZE * self._scale
        bob = 0
        if self._effect and self.motion.get():
            bob = math.sin(self._effect["frame"] / 10 * math.pi) * tile * .07
        px, py = self._point((x, y))
        self.canvas.create_image(px, py + bob, image=photo, anchor="s", tags="avatar")

    def _draw_effect(self):
        self.canvas.delete("feedback")
        if not self._effect:
            return
        kind, position, frame = (self._effect[key] for key in ("kind", "position", "frame"))
        tile = SOIL_SIZE * self._scale
        px, py = self._point(position)
        ax, ay = self._point((self.world.x, self.world.y))
        fraction = frame / 10
        # Existing tool sprites move between the character and target; no generated new assets.
        asset = {"till": "hoe", "plant": "seeds", "water": "watering-can",
                 "refill": "watering-can"}.get(kind)
        if asset:
            photo = self._photo(asset)
            if photo:
                reach = math.sin(fraction * math.pi)
                self.canvas.create_image(ax + (px - ax) * (.3 + .3 * reach),
                    ay + (py - ay) * .4 - tile * (.25 + .18 * reach),
                    image=photo, tags="feedback")
        colors = {"till": "#EBC387", "plant": "#DFFF92", "water": "#B1EEFF",
                  "harvest": "#FFF1A3", "refill": "#B1EEFF", "gate": "#FFF1A3"}
        color = colors[kind]
        for index in range(5):
            spread = (index - 2) * tile * .11
            lift = math.sin(fraction * math.pi) * tile * (.2 + (index % 2) * .12)
            x, y = px + spread * (1 + fraction), py - lift
            radius = max(1, tile * .035)
            if kind in {"water", "refill"}:
                self.canvas.create_line(x, y - radius * 2, x, y + radius,
                    fill=color, width=max(1, round(tile * .045)), tags="feedback")
            else:
                self.canvas.create_rectangle(x - radius, y - radius,
                    x + radius, y + radius, fill=color, outline="", tags="feedback")
        label = {"till": "松土", "plant": "种下啦", "water": "浇好了",
                 "harvest": "收获 +1", "refill": "水壶装满", "gate": "小门已打开" if self.world.gate_open else "小门已关好"}[kind]
        # Keep the completion label above both the target and the character's
        # visible head (especially when standing on a small dock/gate pad).
        label_y = min(py - tile * (.9 + .12 * fraction), ay - tile * 1.65)
        item = self.canvas.create_text(px, label_y, text=label,
            fill="#FFFFFF", font=(theme.BODY_FONT, 10, "bold"), tags="feedback")
        bounds = self.canvas.bbox(item)
        if bounds:
            backdrop = self.canvas.create_rectangle(bounds[0] - 4, bounds[1] - 2,
                bounds[2] + 4, bounds[3] + 2, fill="#263B32", outline="", tags="feedback")
            self.canvas.tag_lower(backdrop, item)
        if kind == "harvest" and self._effect.get("crop"):
            photo = self._photo(f"{self._effect['crop']}-2")
            if photo:
                self.canvas.create_image(px, py - tile * (.25 + .4 * fraction),
                    image=photo, tags="feedback")

    def _start_effect(self, kind, position, *, crop=None):
        self._stop_effect()
        self._effect = {"kind": kind, "position": position, "frame": 0, "crop": crop}
        self.draw()
        self._effect_timer = self.after(34 if self.motion.get() else 420, self._effect_tick)

    def _effect_tick(self):
        self._effect_timer = None
        if self._closed or not self.canvas.winfo_ismapped():
            self._effect = None
            return
        if not self.motion.get() or not self._effect or self._effect["frame"] >= 10:
            self._effect = None
            self._frame = 0
            self.draw()
            return
        self._effect["frame"] += 1
        self._frame = 1 if self._effect["frame"] < 6 else 0
        # Full y-sorted redraw: a tool pose must not jump above all trees/fences.
        self.draw()
        self._effect_timer = self.after(34, self._effect_tick)

    def _stop_effect(self):
        if self._effect_timer:
            self.after_cancel(self._effect_timer)
            self._effect_timer = None
        self._effect = None

    def _motion_changed(self):
        self._stop_walk()
        self._stop_effect()
        self._stop_water()
        self.draw()

    def _pause_surface(self):
        self._stop_walk()
        self._stop_effect()
        self._stop_water()
        if self._clock_timer is not None:
            self.after_cancel(self._clock_timer)
            self._clock_timer = None

    def _refresh_environment(self):
        state = calendar_state()
        season_index, period_index = self.season_choice.current(), self.period_choice.current()
        if season_index > 0:
            state["season"] = ("spring", "summer", "autumn", "winter")[season_index - 1]
        if period_index > 0:
            state["period"] = ("dawn", "day", "dusk", "night")[period_index - 1]
        self._environment = state
        season = {"spring": "春", "summer": "夏", "autumn": "秋", "winter": "冬"}[state["season"]]
        period = {"dawn": "清晨", "day": "白天", "dusk": "黄昏", "night": "夜间"}[state["period"]]
        preview = " · 画面预览（不影响作物）" if season_index > 0 or period_index > 0 else ""
        self.environment_label.configure(text=f"第 {state['year']} 年 · 第 {state['month']} 月 · {season}季 / {period} · 现实一天＝游戏一月{preview}")
        if self._renderer.set_environment(state["season"], state["period"]):
            self._photos.clear()

    def _sync_clock_timer(self):
        if not self._closed and self.winfo_ismapped() and self._clock_timer is None:
            self._clock_timer = self.after(30000, self._clock_tick)

    def _clock_tick(self):
        self._clock_timer = None
        if not self._closed and self.winfo_ismapped():
            self.draw()
            self._sync_clock_timer()

    def _sync_water_timer(self):
        if (not self._closed and self.motion.get() and self.canvas.winfo_ismapped()
                and self._renderer.water_frames and self._water_timer is None):
            self._water_timer = self.after(180, self._water_tick)

    def _water_tick(self):
        self._water_timer = None
        if self._closed or not self.motion.get() or not self.canvas.winfo_ismapped():
            return
        self._water_phase += 1
        self._renderer.update_water(self.canvas, self._scale, self, self._water_phase)
        self._renderer.update_atmosphere(self.canvas, self._origin, self._scale, self, self._water_phase)
        self._sync_water_timer()

    def _stop_water(self):
        if self._water_timer is not None:
            self.after_cancel(self._water_timer)
            self._water_timer = None

    def reload_world(self):
        """Explicit recovery: validate first, then swap state and expected bytes."""
        try:
            if self._world_file.is_symlink():
                raise ValueError("人物存档是符号链接，已停止写入")
            original = self._world_file.read_bytes() if self._world_file.exists() else None
            raw = json.loads(original.decode("utf-8-sig")) if original is not None else {}
            candidate = FarmWorld.restore(raw)
        except (ValueError, OSError) as error:
            self.message.set(f"无法重新读取人物存档，当前位置未改变：{error}")
            return False
        self._pause_surface()
        self.world, self._world_bytes, self._world_error = candidate, original, None
        self.message.set("已重新读取作物与人物存档，可以继续耕作。")
        return True

    def _save_world(self, candidate):
        if self._world_error:
            self.message.set("人物存档损坏，原文件已保留；修复 farm-world.json 后点击“重新读取当前存档”。")
            return False
        try:
            if self._world_file.is_symlink():
                raise OSError("人物存档变成符号链接，已停止写入")
            current = self._world_file.read_bytes() if self._world_file.exists() else None
            if current != self._world_bytes:
                raise OSError("人物存档已被其他程序修改，请点击“重新读取当前存档”后再操作")
            if (self.world.source_version < 3 or self.world.relocated) and self._world_bytes is not None:
                self.store.backup_original("farm-world", self.world.source_version, self._world_bytes)
            self._world_bytes = self.store.save("farm-world", candidate.snapshot())
        except OSError as error:
            self.message.set(f"无法保存移动状态，位置没有改变：{error}")
            return False
        candidate.source_version = 3
        candidate.relocated = False
        self.world = candidate
        return True

    def _step(self, direction):
        if self._timer or self._closed or self._asset_errors:
            return
        self._stop_effect()
        candidate = deepcopy(self.world)
        start = (self.world.x, self.world.y)
        moved = candidate.move(direction)
        if not self._save_world(candidate):
            self._path.clear()
            self._arrival = None
            return
        self.host.record_activity()
        if not moved:
            self.message.set("前面是树木、石头、围栏或水面；绕一下，别穿过去。")
            self._path.clear()
            self._arrival = None
            self.draw()
            return
        self._anim = start
        if not self.motion.get():
            self._anim = None
            self._frame = 0
            self.host._draw_garden()
            if self._path:
                # Schedule to avoid recursion even for a long click-to-walk route.
                self._timer = self.after(1, self._continue_route)
            else:
                self._arrived()
            return
        self._animate(start, (candidate.x, candidate.y), 1)

    def _continue_route(self):
        self._timer = None
        if not self._closed and self._path and self.canvas.winfo_ismapped():
            self._step(self._path.pop(0))

    def _animate(self, start, end, frame):
        self._timer = None
        if self._closed or not self.canvas.winfo_ismapped():
            self._anim = None
            self._frame = 0
            self._path.clear()
            return
        fraction = frame / 3
        self._anim = (start[0] + (end[0] - start[0]) * fraction,
                      start[1] + (end[1] - start[1]) * fraction)
        self._frame = 1 if (self.world.x + self.world.y) // NAV_STEP % 2 else 2
        self.draw()
        if frame < 3:
            self._timer = self.after(22, lambda: self._animate(start, end, frame + 1))
        else:
            self._anim = None
            self._frame = 0
            self.host._draw_garden()
            if self._path:
                self._step(self._path.pop(0))
            else:
                self._arrived()

    def _arrived(self):
        intent, self._arrival = self._arrival, None
        if not intent:
            interaction = self.world.interaction()
            index = self.world.target_plot()
            if interaction == "refill":
                self.message.set(f"已经到{self.world.interaction_object().name}，按空格 / E 装满水壶。")
            elif interaction == "gate":
                self.message.set("已经到小门，按空格 / E 互动。")
            elif index is not None:
                self.message.set(f"面向第 {index + 1} 块地，按空格使用{TOOLS[self.tool.get()]}。")
            else:
                self.message.set("方向键 / WASD 移动；点击地块走到它前面，空格 / E 互动。")
            return
        facing, kind = intent
        candidate = deepcopy(self.world)
        candidate.facing = facing
        if not self._save_world(candidate):
            return
        self.host._draw_garden()
        if kind in {"well", "dock", "gate"}:
            self.interact()
        else:
            index = self.world.target_plot()
            self.message.set(f"已经面向第 {index + 1} 块地；按空格使用{TOOLS[self.tool.get()]}。"
                if index is not None else "站到地块前再使用工具。")

    def _stop_walk(self):
        self._path.clear()
        self._arrival = None
        if self._timer:
            self.after_cancel(self._timer)
            self._timer = None
        self._anim = None
        self._frame = 0
        if not self._closed and self.canvas.winfo_ismapped():
            self.draw()

    def _click(self, event):
        self.canvas.focus_set()
        self._stop_walk()
        self._stop_effect()
        point = ((event.x - self._origin[0]) / self._scale,
                 (event.y - self._origin[1]) / self._scale)
        house = BY_ID["house"]
        if house.visual[0] <= point[0] < house.visual[2] and house.visual[1] <= point[1] < house.visual[3]:
            self.message.set("小屋暂未开放；可以继续在农场耕种、取水和收获。")
            return
        target = tuple(round(n / NAV_STEP) * NAV_STEP for n in point)
        index, item = plot_at(point), object_at(point)
        intent = None
        if index is not None:
            self.host._selected_plot = index
            approach = self.world.approach(PLOTS[index])
            if approach:
                self._path, facing = approach
                intent = (facing, "plot")
        elif item:
            approach = self.world.approach_object(item.id)
            if approach:
                self._path, facing = approach
                intent = (facing, item.id)
        else:
            self._path = self.world.path_to(target)
        self._arrival = intent
        if self._path:
            self.message.set("雷姆正在走过去…按方向键可接管移动。")
            self._step(self._path.pop(0))
        elif intent:
            self._arrived()
        else:
            self.message.set("点击地块、左下水井、右上码头或入口小门；方向键接管移动，空格 / E 互动。")

    def _key(self, event):
        if event.keysym in KEYS:
            self._stop_walk()
            self._step(KEYS[event.keysym])
            return "break"
        if event.char in "1234" and event.char:
            self.choose_tool(int(event.char) - 1)
            return "break"
        if event.keysym in {"space", "Return", "e", "E"}:
            self.interact()
            return "break"

    def use_tool(self):
        self.canvas.focus_set()
        if self._timer or self._asset_errors:
            return
        if self._effect:
            return
        index = self.world.target_plot()
        if index is None:
            self.message.set("走到一块地旁边并面向它，再按空格。")
            return
        tool = self.tool.get()
        crop = self.host.garden.plots[index].get("crop")
        try:
            if tool == 0:
                if self.host.garden.plots[index]:
                    self.message.set("这里已经有作物，不要再锄它啦。")
                    return
                if index in self.world.tilled:
                    self.message.set("这块地已经松好了，切换 2 号工具播种吧。")
                    return
                candidate = deepcopy(self.world)
                candidate.tilled.add(index)
                if not self._save_world(candidate):
                    return
                message = "这块地松好了，可以播种。"
            else:
                if tool == 1 and index not in self.world.tilled:
                    self.message.set("先用 1 号工具耕地，再播种。")
                    return
                if tool == 1:
                    action = lambda g: g.plant(index, self.crop.get())
                    message = f"种下了{CROPS[self.crop.get()].name}，接下来记得浇水。"
                elif tool == 2:
                    if self.host.garden.plots[index].get("watered"):
                        self.message.set("这块地已经浇好水了，等它成熟后再收获吧。")
                        return
                    action = lambda g: g.water(index)
                    message = "浇好水了，小芽会按真实时间慢慢长大。"
                else:
                    action = lambda g: g.harvest(index)
                    message = "收获啦！已经记入你的收获册。"
                saved, _result = self.host._commit_activity("garden", action)
                if not saved:
                    self.message.set("种植操作未保存，地块没有改变。")
                    return
            self.message.set(message)
            self.host.record_activity()
            self.host._draw_garden()
            self._start_effect(("till", "plant", "water", "harvest")[tool], PLOTS[index], crop=crop)
        except ValueError as error:
            self.message.set(str(error))

    def interact(self):
        """Space/E and visible button use the same contextual interaction."""
        self.canvas.focus_set()
        if self._closed or self._timer or self._effect or self._asset_errors:
            return
        action = self.world.interaction()
        if action == "refill":
            item = self.world.interaction_object()
            self.message.set(f"在{item.name}装满水壶啦。回到地块前，选择 3 号工具浇水。")
            self.host.record_activity()
            self._start_effect("refill", ((item.visual[0] + item.visual[2]) / 2,
                item.depth - 12))
        elif action == "gate":
            candidate = deepcopy(self.world)
            candidate.gate_open = not candidate.gate_open
            if not self._save_world(candidate):
                return
            self.message.set("小门打开啦，可以走出去看看。" if self.world.gate_open else "小门关好啦。")
            self.host.record_activity()
            self._start_effect("gate", ENTRY_GATE)
        else:
            self.use_tool()

    def _destroyed(self, event):
        if event.widget is self:
            self._closed = True
            self._pause_surface()

    def move(self, direction):
        """Visible pad and keyboard movement share the same focused surface."""
        self.canvas.focus_set()
        self._stop_walk()
        self._step(direction)
