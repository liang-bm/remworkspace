"""Optional, disjoint source-pixel scene layers; never edits original image files."""
from __future__ import annotations

from dataclasses import dataclass
from collections import OrderedDict
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageStat, ImageTk

from ..plaza.farm_layout import (MAP_WIDTH, MAP_HEIGHT, OBJECTS, GATE_DOOR_VISUAL,
    WELL_FENCE_OPENING, WELL_STONE_VISUALS, WELL_STONE_REMOVAL, WATER_POLYGONS, WALK_SURFACES,
    SOIL_SIZE, PLOTS, PLOT_INDEX)
from .farm_scene_effects import SEASONS, PERIODS, material_image, light_image


@dataclass(frozen=True)
class ScenePiece:
    id: str
    origin: tuple[int, int]
    depth: int
    image: Image.Image
    ground_surface: bool = False


class FarmRenderer:
    """A missing/broken layer falls back to the old map without doubled objects."""
    def __init__(self, assets: Path, old_map: Image.Image | None):
        self.layered = False
        self.notice = ""
        self.ground = old_map
        self.pieces = []
        self.photos = OrderedDict()
        self.environment = ("spring", "day")
        self.materials = OrderedDict()
        self.scale = None
        self.soils = OrderedDict()
        self.water_frames = []
        self.soil_dry = self.soil_core = None
        self.wet_ratio = (1, 1, 1)
        try:
            paths = [assets / "ground-v2.png", assets / "objects-v2.png"]
            if not all(path.is_file() for path in paths):
                raise ValueError("分层素材未安装")
            source = []
            for path in paths:
                with Image.open(path) as image:
                    if image.format != "PNG" or image.width * 3 != image.height * 4:
                        raise ValueError("分层素材应为 4:3 PNG")
                    source.append(image.convert("RGBA").resize((MAP_WIDTH, MAP_HEIGHT),
                        Image.Resampling.NEAREST))
            ground, objects = source
            alpha = objects.getchannel("A")
            if alpha.getextrema()[0] != 0 or alpha.getextrema()[1] == 0:
                raise ValueError("物体层缺少透明背景或为空")
            if ground.getchannel("A").getextrema() != (255, 255):
                raise ValueError("地面层必须完全不透明")
            # Author the complete fence span before rectangle-based ownership:
            # its cap/stem previously straddled the neighboring rock's visual
            # bounds. Erasing only the fence layer left floating post fragments.
            opening = Image.new("L", objects.size, 0)
            opening_draw = ImageDraw.Draw(opening)
            for polygon in WELL_FENCE_OPENING:
                opening_draw.polygon(polygon, fill=255)
            for polygon in WELL_STONE_VISUALS:
                opening_draw.polygon(polygon, fill=0)
            left, top, right, bottom = WELL_STONE_REMOVAL
            opening_draw.rectangle((left, top, right - 1, bottom - 1), fill=255)
            alpha = ImageChops.subtract(alpha, opening)
            objects.putalpha(alpha)
            claimed = Image.new("L", objects.size, 0)
            for item in sorted(OBJECTS, key=lambda value: value.visual_priority, reverse=True):
                if item.copies:
                    continue  # Complete native panels are placed after source ownership.
                area = Image.new("L", objects.size, 0)
                # Right/bottom are exclusive everywhere in this renderer.
                left, top, right, bottom = item.source_visual or item.visual
                ImageDraw.Draw(area).rectangle((left, top, right - 1, bottom - 1), fill=255)
                owned = ImageChops.subtract(area, claimed)
                claimed = ImageChops.lighter(area, claimed)
                piece_alpha = ImageChops.multiply(alpha, owned)
                bounds = piece_alpha.getbbox()
                if bounds:
                    piece = objects.crop(bounds)
                    piece.putalpha(piece_alpha.crop(bounds))
                    offset = (item.visual[0] - left, item.visual[1] - top)
                    self.pieces.append(ScenePiece(item.id,
                        (bounds[0] + offset[0], bounds[1] + offset[1]), item.depth,
                                                   piece, item.ground_surface))
            # Preserve every remaining alpha pixel; sparse decorative fragments
            # sit behind the actor rather than becoming accidental collision.
            residual = ImageChops.subtract(alpha, ImageChops.multiply(alpha, claimed))
            bounds = residual.getbbox()
            if bounds:
                piece = objects.crop(bounds)
                piece.putalpha(residual.crop(bounds))
                self.pieces.append(ScenePiece("decorative", bounds[:2], 0, piece, True))
            # Close the obsolete doorway with complete original fence panels,
            # including their caps/feet. Never stretch interior bridge texture.
            for item in OBJECTS:
                if not item.copies:
                    continue
                panel = objects.crop(item.source_visual)
                composed = Image.new("RGBA", objects.size)
                for point in item.copies:
                    composed.alpha_composite(panel, point)
                bounds = composed.getbbox()
                if bounds:
                    self.pieces.append(ScenePiece(item.id, bounds[:2], item.depth,
                        composed.crop(bounds), item.ground_surface))
            self.ground, self.layered = ground, True
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            self.pieces = []
            self.notice = f"{error}；当前使用旧静态地图与新版物理规则，不支持树前树后遮挡。"
            # If only the object layer is unavailable, keep the well approach
            # visually consistent in the static fallback too. The valid native
            # ground crop replaces the removed object, never an invented fill.
            if old_map is not None:
                try:
                    with Image.open(assets / "ground-v2.png") as image:
                        if image.format != "PNG" or image.width * 3 != image.height * 4:
                            raise ValueError("地面层应为 4:3 PNG")
                        ground = image.convert("RGBA").resize((MAP_WIDTH, MAP_HEIGHT),
                            Image.Resampling.NEAREST)
                    if ground.getchannel("A").getextrema() != (255, 255):
                        raise ValueError("地面层必须完全不透明")
                    self.ground = old_map.resize((MAP_WIDTH, MAP_HEIGHT),
                        Image.Resampling.NEAREST).copy()
                    self.ground.paste(ground.crop(WELL_STONE_REMOVAL), WELL_STONE_REMOVAL)
                except (OSError, ValueError, Image.DecompressionBombError):
                    self.notice += " 地面素材也不可用，旧地图中的井边石头不能正确移除，请修复素材。"
        self._prepare_soil(assets)
        self._prepare_water()

    def set_environment(self, season, period):
        """Select a visual-only environment; callers redraw when True is returned."""
        if season == "fall":
            season = "autumn"
        if season not in SEASONS or period not in PERIODS:
            raise ValueError("未知农场季节或时段")
        environment = (season, period)
        if environment == self.environment:
            return False
        self.environment = environment
        # Retain bounded runtime material cache; Tk photos belong to this view.
        self.photos.clear()
        return True

    @staticmethod
    def _remember(cache, key, value, limit):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)
        return value

    def material(self, key, image, role="object", origin=(0, 0)):
        cache_key = (self.environment, key, role)
        if cache_key in self.materials:
            self.materials.move_to_end(cache_key)
            return self.materials[cache_key]
        return self._remember(self.materials, cache_key,
            material_image(image, *self.environment, role=role, origin=origin), 96)

    def sprite_image(self, name, image):
        """Actors/crops share daylight, without changing their seasonal identity."""
        return self.material(("sprite", name), image, "soil")

    def _prepare_soil(self, assets):
        """One source geometry/texture for both moisture states, not two scales."""
        try:
            with Image.open(assets / "soil-1.png") as image:
                dry = image.convert("RGBA")
            with Image.open(assets / "soil-2.png") as image:
                wet = image.convert("RGBA")
            if not dry.getbbox() or not wet.getbbox():
                return
            self.soil_dry = dry.crop(dry.getbbox()).resize((SOIL_SIZE, SOIL_SIZE), Image.Resampling.NEAREST)
            # The outer eighth is the existing ragged rim. Interior texture is
            # sampled in map coordinates, so adjoining cells have no new rim.
            edge = SOIL_SIZE // 8
            self.soil_core = self.soil_dry.crop((edge, edge, SOIL_SIZE - edge, SOIL_SIZE - edge))
            # The compiled atlas carries a nearly-opaque matte (251–253) even
            # inside solid earth. Only the exposed ragged rim needs alpha.
            self.soil_core.putalpha(255)
            dry_mean = ImageStat.Stat(dry.convert("RGB"), dry.getchannel("A")).mean
            wet_mean = ImageStat.Stat(wet.convert("RGB"), wet.getchannel("A")).mean
            self.wet_ratio = tuple(w / d if d else 1 for d, w in zip(dry_mean, wet_mean))
        except (OSError, ValueError, Image.DecompressionBombError):
            return

    def soil_image(self, index, state, soil_states):
        if self.soil_core is None:
            return None, None
        x, y = PLOTS[index]
        neighbors = tuple(bool(soil_states.get(PLOT_INDEX.get(point))) for point in
            ((x - SOIL_SIZE, y), (x + SOIL_SIZE, y), (x, y - SOIL_SIZE), (x, y + SOIL_SIZE)))
        core = self.soil_core
        key = ("soil", state, x % core.width, y % core.height, neighbors)
        if key not in self.soils:
            image = Image.new("RGBA", (SOIL_SIZE, SOIL_SIZE))
            edge = SOIL_SIZE // 8
            for py in range(SOIL_SIZE):
                for px in range(SOIL_SIZE):
                    rim = ((px < edge and not neighbors[0]) or
                        (px >= SOIL_SIZE - edge and not neighbors[1]) or
                        (py < edge and not neighbors[2]) or
                        (py >= SOIL_SIZE - edge and not neighbors[3]))
                    if rim:
                        # Joined sides have no isolated corner notch, even when
                        # the perpendicular side is an exposed outer field edge.
                        source_x = max(edge, px) if neighbors[0] else px
                        source_x = min(SOIL_SIZE - edge - 1, source_x) if neighbors[1] else source_x
                        source_y = max(edge, py) if neighbors[2] else py
                        source_y = min(SOIL_SIZE - edge - 1, source_y) if neighbors[3] else source_y
                        pixel = self.soil_dry.getpixel((source_x, source_y))
                    else:
                        pixel = core.getpixel(((x - SOIL_SIZE // 2 + px) % core.width,
                            (y - SOIL_SIZE // 2 + py) % core.height))
                    if state == 2:
                        pixel = (*tuple(min(255, round(c * ratio)) for c, ratio in
                            zip(pixel[:3], self.wet_ratio)), pixel[3])
                    image.putpixel((px, py), pixel)
            self._remember(self.soils, key, image, 256)
        self.soils.move_to_end(key)
        return key, self.soils[key]

    def draw_soil(self, canvas, index, state, soil_states, origin, scale, master):
        key, image = self.soil_image(index, state, soil_states)
        if image is None:
            return False
        x, y = PLOTS[index]
        # Round shared cell edges, not each center and sprite width independently.
        left, top = (round(origin[0] + (x - SOIL_SIZE // 2) * scale),
                     round(origin[1] + (y - SOIL_SIZE // 2) * scale))
        right, bottom = (round(origin[0] + (x + SOIL_SIZE // 2) * scale),
                        round(origin[1] + (y + SOIL_SIZE // 2) * scale))
        image = self.material(key, image, "soil")
        key = (self.environment, *key, right - left, bottom - top)
        if scale != self.scale:
            self.photos.clear()
            self.scale = scale
        if key not in self.photos:
            self._remember(self.photos, key, ImageTk.PhotoImage(image.resize((max(1, right - left),
                max(1, bottom - top)), Image.Resampling.NEAREST), master=master), 256)
        self.photos.move_to_end(key)
        canvas.create_image(left, top, image=self.photos[key], anchor="nw", tags="soil")
        return True

    def _prepare_water(self):
        """Eight downstream flow phases, masked to actual water, never banks/art."""
        if self.ground is None or self.ground.size != (MAP_WIDTH, MAP_HEIGHT):
            return
        mask = Image.new("L", self.ground.size, 0)
        draw = ImageDraw.Draw(mask)
        for polygon in WATER_POLYGONS:
            draw.polygon(polygon, fill=255)
        for polygon in WALK_SURFACES:
            draw.polygon(polygon, fill=0)
        for y in range(MAP_HEIGHT):
            for x in range(MAP_WIDTH):
                red, green, blue = self.ground.getpixel((x, y))[:3]
                if blue <= red or blue <= green:
                    mask.putpixel((x, y), 0)
        self.water_mask = mask
        for phase in range(8):
            image = Image.new("RGBA", self.ground.size)
            draw = ImageDraw.Draw(image)
            # Every crest moves two pixels DOWN per tick. Uniform 16px spacing
            # makes the loop wrap continuously instead of jumping upstream.
            for y in range(32, MAP_HEIGHT + 16, 16):
                for x in range(0, MAP_WIDTH, 27):
                    cy = y + phase * 2 + (x // 27 % 3) * 3
                    draw.line((x + 3, cy, x + 18, cy), fill=(141, 222, 246, 140), width=1)
                    draw.line((x + 7, cy + 1, x + 21, cy + 1), fill=(203, 246, 252, 168), width=1)
                    draw.line((x + 11, cy + 3, x + 17, cy + 3), fill=(36, 129, 171, 100), width=1)
            image.putalpha(ImageChops.multiply(image.getchannel("A"), mask))
            self.water_frames.append(image)

    def draw_water(self, canvas, origin, scale, master, phase=0):
        if self.water_frames:
            index = phase % len(self.water_frames)
            image = self.material(("water", index), self.water_frames[index], "soil")
            canvas.create_image(*map(round, origin), image=self.photo(("water", index),
                image, scale, master), anchor="nw", tags="water")

    def update_water(self, canvas, scale, master, phase):
        if self.water_frames and canvas.find_withtag("water"):
            index = phase % len(self.water_frames)
            image = self.material(("water", index), self.water_frames[index], "soil")
            canvas.itemconfigure("water", image=self.photo(("water", index), image, scale, master))

    def draw_atmosphere(self, canvas, origin, scale, master, phase=0):
        if self.environment[1] not in {"dawn", "dusk", "night"}:
            return
        key = ("atmosphere", self.environment)
        if key not in self.materials:
            self._remember(self.materials, key,
                light_image(*self.environment, self.pieces, phase), 96)
        canvas.create_image(*map(round, origin), image=self.photo("atmosphere", self.materials[key],
            scale, master), anchor="nw", tags="atmosphere")

    def update_atmosphere(self, canvas, origin, scale, master, phase=0):
        # Light is steady; updating water is the scene's only ambient loop.
        # This API remains separate from actor motion and causes no new timer.
        if canvas.find_withtag("atmosphere"):
            canvas.coords("atmosphere", *map(round, origin))

    def _gate_image(self, piece, openness):
        """Swing the ORIGINAL transparent door crop; no flat floor/color patch."""
        if openness <= 0:
            return piece.image
        image = piece.image.copy()
        left, top, right, bottom = GATE_DOOR_VISUAL
        box = (left - piece.origin[0], top - piece.origin[1],
               right - piece.origin[0], bottom - piece.origin[1])
        door = piece.image.crop(box)
        image.paste((0, 0, 0, 0), box)
        narrow = max(3, round(door.width * (1 - openness * .88)))
        swung = door.resize((narrow, door.height), Image.Resampling.NEAREST)
        # Alpha composite, not paste(mask), preserves the source alpha exactly.
        image.alpha_composite(swung, (max(0, box[0]), max(0, box[1])))
        return image

    def photo(self, key, image, scale, master):
        if scale != self.scale:
            self.photos.clear()
            self.scale = scale
        key = (self.environment, key)
        if key not in self.photos:
            size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
            self._remember(self.photos, key, ImageTk.PhotoImage(image.resize(size, Image.Resampling.NEAREST), master=master), 256)
        self.photos.move_to_end(key)
        return self.photos[key]

    def draw_ground(self, canvas, origin, scale, master):
        if self.ground is None:
            return False
        image = self.material("ground", self.ground, "ground")
        canvas.create_image(*map(round, origin), image=self.photo("ground", image, scale, master), anchor="nw", tags="ground")
        return True

    def draw_piece(self, canvas, piece, origin, scale, master, gate_openness=0):
        openness = round(max(0, min(1, gate_openness)) * 10) / 10
        image = self._gate_image(piece, openness) if piece.id == "gate" else piece.image
        image = self.material((piece.id, openness if piece.id == "gate" else 0),
            image, piece.id, piece.origin)
        key = (piece.id, openness if piece.id == "gate" else 0)
        # Every source crop uses the SAME map-to-screen sampling grid. Resizing
        # each rectangle independently used a different nearest-neighbor phase,
        # visibly breaking bridge rails and tree branches at ownership seams.
        if scale != self.scale:
            self.photos.clear()
            self.scale = scale
        key = (self.environment, "world-piece", key)
        if key not in self.photos:
            left, top, scaled = self.world_piece_image(image, piece.origin, scale)
            self._remember(self.photos, key,
                (left, top, ImageTk.PhotoImage(scaled, master=master)), 256)
        self.photos.move_to_end(key)
        left, top, photo = self.photos[key]
        canvas.create_image(round(origin[0]) + left, round(origin[1]) + top,
            image=photo, anchor="nw", tags=("object", piece.id))

    @staticmethod
    def world_piece_image(image, source_origin, scale):
        """Sample a crop on the resized whole-map grid, with shared pixel edges."""
        # Resize a source-grid canvas rather than each crop's local rectangle.
        # Apart from different phases, crop-local affine arithmetic disagrees
        # with Pillow's whole-image nearest rounding at half-pixel ties. This
        # bounded 512x384 staging canvas keeps even those ties identical. Its
        # result is cached as a Tk photo, not rebuilt on each animation tick.
        source = Image.new("RGBA", (MAP_WIDTH, MAP_HEIGHT))
        source.paste(image, source_origin)
        scaled = source.resize((max(1, round(MAP_WIDTH * scale)),
            max(1, round(MAP_HEIGHT * scale))), Image.Resampling.NEAREST)
        bounds = scaled.getchannel("A").getbbox()
        if bounds is None:
            return 0, 0, Image.new("RGBA", (1, 1))
        return bounds[0], bounds[1], scaled.crop(bounds)
