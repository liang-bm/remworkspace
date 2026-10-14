"""Quiet, rounded settings surfaces around native Tk controls.

THESIS: Settings are readable groups, not one bordered slab of controls.
OWN-WORLD: RemChat's blue-white/blue-grey palette, soft offset depth, native inputs.
STORY: Choose a category, scan section names, change one clearly explained setting.
FIRST VIEWPORT: A grouped rail and a single scroll column of separately spaced cards.
FORM: The user's WorkBuddy hierarchy extends the existing Tk settings surface.
"""

from __future__ import annotations

import tkinter as tk
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageTk

from . import theme
from .dpi import pixels
from .theme_controller import set_color_roles


@lru_cache(maxsize=32)
def _chrome_stamp(tile: int, radius: int, edge: int, drop: int,
                  offset: int, blur: int, background: str, surface: str,
                  shadow_alpha: int) -> Image.Image:
    """Blur only a small, reusable corner stamp, never a whole settings page."""
    factor = 2
    size = tile * 2
    image = Image.new("RGBA", (size * factor, size * factor), background)
    bounds = (edge * factor, 0, (size - edge - 1) * factor,
              (size - drop - 1) * factor)
    if shadow_alpha:
        layer = Image.new("RGBA", image.size)
        ImageDraw.Draw(layer).rounded_rectangle(
            (bounds[0], offset * factor, bounds[2], bounds[3] + offset * factor),
            radius=radius * factor, fill=(0, 0, 0, shadow_alpha),
        )
        image = Image.alpha_composite(image, layer.filter(ImageFilter.GaussianBlur(blur * factor)))
    ImageDraw.Draw(image).rounded_rectangle(bounds, radius=radius * factor, fill=surface)
    return image.resize((size, size), Image.Resampling.LANCZOS)


class SettingsCard(tk.Canvas):
    """Resizable chrome; ``body`` keeps native widget behavior and tab order."""

    def __init__(self, parent: tk.Misc, *, surface_role: str = "WHITE",
                 background_role: str = "APP_BG", shadow: bool = True,
                 inset: int = 12, radius: int = 16, stretch: bool = False) -> None:
        super().__init__(parent, width=pixels(parent, 320),
                         height=pixels(parent, 240) if stretch else 1,
                         highlightthickness=0, bd=0, takefocus=False)
        self._surface_role = surface_role
        self._background_role = background_role
        self._shadow = shadow
        self._inset = inset
        self._radius = radius
        self._stretch = stretch
        self._job: str | None = None
        self._image: ImageTk.PhotoImage | None = None
        self._images: list[ImageTk.PhotoImage] = []
        self._image_key: tuple | None = None
        self._sizes: dict[tk.Misc, tuple[int, int]] = {}
        self._chrome_tiles: dict[tuple[int, int], tuple[tuple, int, ImageTk.PhotoImage]] = {}
        self._center: int | None = None
        set_color_roles(self, background=background_role)
        self.body = tk.Frame(self, bd=0)
        set_color_roles(self.body, background=surface_role)
        self._window = self.create_window(0, 0, window=self.body, anchor="nw")
        self.bind("<Configure>", self.refresh, add=True)
        self.body.bind("<Configure>", self.refresh, add=True)
        self.bind("<<ThemeChanged>>", self.refresh, add=True)
        self.bind("<Map>", self.refresh, add=True)
        self.bind("<Destroy>", self._destroy, add=True)
        self.refresh()

    def set_surface(self, role: str) -> None:
        if role == self._surface_role:
            return
        self._surface_role = role
        set_color_roles(self.body, background=role)
        self.refresh()

    def refresh(self, _event=None) -> None:
        if _event is not None and _event.type == tk.EventType.Configure:
            size = (_event.width, _event.height)
            if self._sizes.get(_event.widget) == size:
                return
            self._sizes[_event.widget] = size
        if self._job is None:
            self._job = self.after_idle(self._layout)

    def _layout(self) -> None:
        self._job = None
        if not self.winfo_exists():
            return
        inset = pixels(self, self._inset)
        drop = pixels(self, 8) if self._shadow else 0
        width = self.winfo_width()
        if width < pixels(self, 32):
            return
        if self.coords(self._window) != [float(inset), float(inset)]:
            self.coords(self._window, inset, inset)
        body_width = max(1, width - inset * 2)
        if int(self.itemcget(self._window, "width")) != body_width:
            self.itemconfigure(self._window, width=body_width)
        height = self.winfo_height() if self._stretch else self.body.winfo_reqheight() + inset * 2 + drop
        if self._stretch:
            body_height = max(1, height - inset * 2 - drop)
            if int(self.itemcget(self._window, "height")) != body_height:
                self.itemconfigure(self._window, height=body_height)
        elif int(self.cget("height")) != height:
            self.configure(height=height)
        if not self.winfo_ismapped():
            return
        colors = theme.palette()
        key = (width, height, theme.get_mode(), self._surface_role, self._background_role)
        if key == self._image_key:
            return
        # Nine-slice chrome: a native flat center and eight thin edge/corner
        # images. Large pages no longer allocate, blur and downsample a 2x
        # full-page bitmap every first mapping or resize.
        radius = pixels(self, self._radius)
        edge = pixels(self, 4) if self._shadow else 0
        if height < max(2, drop + edge * 2 + 2):
            # A stretch card can briefly be one pixel high while its parent
            # maps. Wait for real geometry rather than create inverted PIL
            # shadow bounds; the next Configure schedules this layout again.
            return
        tile = max(1, min(radius + pixels(self, 16), width // 2, height // 2))
        stamp = _chrome_stamp(tile, min(radius, tile), edge, min(drop, tile),
                              pixels(self, 3), pixels(self, 4),
                              colors[self._background_role], colors[self._surface_role],
                              (62 if theme.get_mode() == "dark" else 24) if self._shadow else 0)
        # Keep unchanged corners/images alive while resizing. Recreating all
        # eight PhotoImages on every width event makes even a small drag pay
        # for four identical corners and unchanged vertical edges repeatedly.
        if self._center is None:
            self._center = self.create_rectangle(tile, tile, width - tile, height - tile,
                                                  fill=colors[self._surface_role], outline="", tags="surface-image")
        else:
            self.coords(self._center, tile, tile, width - tile, height - tile)
            self.itemconfigure(self._center, fill=colors[self._surface_role])
        active_tiles = set()
        starts, ends = (0, tile, tile), (tile, tile + 1, tile * 2)
        positions_x, positions_y = (0, tile, width - tile), (0, tile, height - tile)
        lengths_x, lengths_y = (tile, width - tile * 2, tile), (tile, height - tile * 2, tile)
        for row in range(3):
            for column in range(3):
                if row == column == 1 or lengths_x[column] < 1 or lengths_y[row] < 1:
                    continue
                slot = (row, column)
                active_tiles.add(slot)
                tile_key = (tile, min(radius, tile), edge, min(drop, tile),
                            theme.get_mode(), colors[self._background_role], colors[self._surface_role],
                            lengths_x[column], lengths_y[row])
                previous = self._chrome_tiles.get(slot)
                if previous is not None and previous[0] == tile_key:
                    self.coords(previous[1], positions_x[column], positions_y[row])
                    continue
                # Center strips take one straight-edge pixel; stretching a
                # complete half-stamp would distort the rounded corner.
                patch = stamp.crop((starts[column], starts[row], ends[column], ends[row]))
                patch = patch.resize((lengths_x[column], lengths_y[row]), Image.Resampling.NEAREST)
                photo = ImageTk.PhotoImage(patch, master=self)
                if previous is None:
                    item = self.create_image(positions_x[column], positions_y[row], image=photo,
                                             anchor="nw", tags="surface-image")
                else:
                    item = previous[1]
                    self.coords(item, positions_x[column], positions_y[row])
                    self.itemconfigure(item, image=photo)
                self._chrome_tiles[slot] = (tile_key, item, photo)
        for slot in set(self._chrome_tiles) - active_tiles:
            self.delete(self._chrome_tiles.pop(slot)[1])
        self._images = [entry[2] for entry in self._chrome_tiles.values()]
        self._image = self._images[0] if self._images else None
        self.tag_lower("surface-image", self._window)
        self._image_key = key

    def _destroy(self, event) -> None:
        if event.widget is not self:return
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        # Release Tk PhotoImages on the owning UI thread. A popup's widget /
        # command cycles may otherwise be collected by a parsing worker;
        # ImageTk.__del__ would then call Tcl from that worker while GC holds
        # the cycle, blocking both threads. The reusable PIL stamp is plain
        # image data and can safely remain in its bounded cache.
        self._image=None;self._images.clear();self._chrome_tiles.clear();self._sizes.clear()
