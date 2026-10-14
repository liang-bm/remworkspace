"""Code-native seasonal materials; all changes are disposable runtime images.

No asset files, world geometry, object alpha or collision data are rewritten.
"""
from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw, ImageOps

from ..plaza.farm_layout import MAP_WIDTH, MAP_HEIGHT, CHERRY_CANOPY_BOUNDS

SEASONS = frozenset({"spring", "summer", "autumn", "winter"})
PERIODS = frozenset({"dawn", "day", "dusk", "night"})
PERIOD_TINT = {"day": (1, 1, 1), "dawn": (.86, .91, .96),
               "dusk": (.92, .76, .70), "night": (.42, .52, .68)}
# These are authored material palettes, not randomized geometry or gameplay rules.
FOLIAGE_PALETTE = {
    "spring": ((7, 26, 5), (.68, 1.04, .32)),
    "summer": ((0, 14, 3), (.44, .92, .38)),
    "autumn": ((40, 7, 0), (1.28, .72, .24)),
    "winter": ((114, 134, 145), (.60, .55, .50)),
}
GROUND_DETAILS = ((207, 112), (287, 118), (318, 123), (427, 128),
                  (177, 172), (150, 216), (171, 247), (451, 249),
                  (279, 297), (306, 293), (137, 309), (222, 315))
WINDOW_BOXES = ((47, 66, 58, 81), (82, 66, 95, 81),
                (135, 66, 147, 81), (119, 39, 131, 50))
LAMP_POINTS = ((115, 184), (439, 216))


def _rgb_material(gray, season):
    offset, gain = FOLIAGE_PALETTE[season]
    return Image.merge("RGB", tuple(gray.point(
        [min(255, round(base + value * factor)) for value in range(256)])
        for base, factor in zip(offset, gain)))


def material_image(image, season, period, *, role="object", origin=(0, 0)):
    """Change surface material in place while retaining alpha byte-for-byte."""
    alpha = image.getchannel("A")
    rgb = image.convert("RGB")
    red, green, blue = rgb.split()
    if role != "soil":
        # Only actual foliage pigments: wood, stone, bridge and walls stay put.
        green_mask = ImageChops.multiply(
            ImageChops.subtract(green, red).point(lambda value: 255 if value > 3 else 0),
            ImageChops.subtract(green, blue).point(lambda value: 255 if value > 8 else 0))
        gray = ImageOps.grayscale(rgb)
        foliage = _rgb_material(gray, season)
        rgb = Image.composite(foliage, rgb, green_mask)
        if role != "ground" and season != "spring":
            pink_mask = ImageChops.multiply(
                ImageChops.subtract(red, green).point(lambda value: 255 if value > 3 else 0),
                ImageChops.subtract(blue, green.point(lambda value: round(value * .72))).point(
                    lambda value: 255 if value > 0 else 0))
            # Cream blossom highlights need recoloring too. Protect brown
            # trunk pigments, not a rectangle which would leave a pink stripe
            # through the lower crown where branches overlap the leaves.
            wood_mask = ImageChops.multiply(
                ImageChops.subtract(green, blue).point(lambda value: 255 if value > 0 else 0),
                ImageChops.subtract(red, green.point(lambda value: min(255, round(value * 1.25)))).point(
                    lambda value: 255 if value > 0 else 0))
            pink_mask = ImageChops.subtract(pink_mask, wood_mask)
            # A material belongs to the whole source object, not whichever
            # depth rectangle happens to own a fragment of its silhouette.
            # Clip in map coordinates so neighboring cherry/woodland/rock
            # pieces receive precisely the same transformation at their join.
            crown_region = Image.new("L", image.size)
            left, top, right, bottom = CHERRY_CANOPY_BOUNDS
            ImageDraw.Draw(crown_region).rectangle((left - origin[0], top - origin[1],
                right - origin[0] - 1, bottom - origin[1] - 1), fill=255)
            pink_mask = ImageChops.multiply(pink_mask, crown_region)
            rgb = Image.composite(foliage, rgb, pink_mask)
        if season == "winter" and role in {"house", "well"}:
            # Snow settles only on upper blue roof facets, not stone or windows.
            roof_mask = ImageChops.multiply(
                ImageChops.subtract(blue, red).point(lambda value: 255 if value > 10 else 0),
                green.point(lambda value: 255 if value > 60 else 0))
            roof_region = Image.new("L", image.size)
            ImageDraw.Draw(roof_region).rectangle((0, 0, image.width,
                64 if role == "house" else 30), fill=255)
            roof_mask = ImageChops.multiply(roof_mask, roof_region)
            rgb = Image.composite(_rgb_material(gray, "winter"), rgb, roof_mask)
        if role == "ground":
            detail = ImageDraw.Draw(rgb)
            for x, y in GROUND_DETAILS:
                if season == "spring":
                    detail.rectangle((x, y - 1, x + 1, y + 1), fill=(250, 205, 217))
                    detail.rectangle((x - 1, y, x + 2, y), fill=(255, 237, 229))
                    detail.point((x, y), fill=(232, 165, 75))
                elif season == "autumn":
                    detail.line((x - 2, y, x + 2, y + 1), fill=(190, 76, 28), width=2)
                    detail.point((x, y), fill=(248, 176, 56))
                elif season == "winter":
                    detail.rectangle((x - 2, y, x + 2, y + 1), fill=(239, 248, 250))
    if period != "day":
        rgb = Image.merge("RGB", tuple(channel.point(
            [round(value * factor) for value in range(256)])
            for channel, factor in zip(rgb.split(), PERIOD_TINT[period])))
    result = rgb.convert("RGBA")
    result.putalpha(alpha)
    return result


def light_image(season, period, source_pieces, phase=0):
    """Localized amber window/lantern pixels, never a map-wide night scrim."""
    result = Image.new("RGBA", (MAP_WIDTH, MAP_HEIGHT))
    if period not in {"dusk", "night", "dawn"}:
        return result
    draw = ImageDraw.Draw(result)
    strength = 48 if period == "night" else 27
    for x, y in LAMP_POINTS:
        # A small downward pool belongs to the hanging lantern, not the whole map.
        draw.polygon(((x - 3, y + 4), (x + 3, y + 4), (x + 13, y + 23),
                      (x + 8, y + 26), (x - 8, y + 26), (x - 13, y + 23)),
                     fill=(255, 198, 89, strength))
        draw.rectangle((x - 1, y - 2, x + 1, y + 1), fill=(255, 232, 146, 226))
    house = next((piece for piece in source_pieces if piece.id == "house"), None)
    if house is not None:
        # Restore only the actual gold source pixels inside authored window areas.
        pixels, output = house.image.load(), result.load()
        for left, top, right, bottom in WINDOW_BOXES:
            for y in range(top, bottom):
                for x in range(left, right):
                    px, py = x - house.origin[0], y - house.origin[1]
                    if 0 <= px < house.image.width and 0 <= py < house.image.height:
                        red, green, blue, alpha = pixels[px, py]
                        if red > 150 and green > 85 and red > blue * 1.4 and alpha:
                            output[x, y] = (min(255, red + 24), min(255, green + 16), blue, alpha)
    return result
