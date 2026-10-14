"""Source-pixel scene catalog shared by collision, interaction and rendering.

The map is 512×384; navigation nodes are foot positions every 8 source pixels.
Short lawn is walkable. Solid trunks, large brush, stones, walls and water are not.
Sprite rectangles are visual bounds, NOT the collision bounds of tree canopies.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

MAP_WIDTH, MAP_HEIGHT = 512, 384
NAV_STEP = 8
SOIL_SIZE = 32
FOOT_RADIUS_X, FOOT_RADIUS_Y = 5, 3
# Persistent IDs are append-only: original crops keep their nine coordinates.
# These are persistent slot coordinates, not a declaration that every cell is
# cultivable. Visual foliage/furniture can cover a walkable foot position.
LEGACY_PLOTS = tuple((240 + column * 64, 144 + row * 64) for row in range(3) for column in range(3))
FARMLAND_BOUNDS = (192, 128, 448, 288)
PLOTS = LEGACY_PLOTS + tuple((x, y) for y in range(144, 288, SOIL_SIZE)
    for x in range(208, 448, SOIL_SIZE)
    # The bottom-left cell touches the gate's solid right pillar, not pure lawn.
    if (x, y) not in LEGACY_PLOTS and (x, y) != (208, 272))
# One-based cells 14, 22, 27, 35, 39 overlap the east-side scene art.
# Tombstone their IDs rather than deleting/reindexing persistent crop slots.
BLOCKED_PLOTS = frozenset({13, 21, 26, 34, 38})
PLOT_INDEX = {point: index for index, point in enumerate(PLOTS) if index not in BLOCKED_PLOTS}
DIRECTIONS = {"down": (0, 1), "left": (-1, 0), "right": (1, 0), "up": (0, -1)}


def rectangle(left, top, right, bottom):
    return ((left, top), (right, top), (right, bottom), (left, bottom))


@dataclass(frozen=True)
class InteractionPad:
    point: tuple[int, int]
    facing: str


@dataclass(frozen=True)
class FarmObject:
    id: str
    name: str
    visual: tuple[int, int, int, int]
    depth: int
    footprints: tuple[tuple[tuple[int, int], ...], ...] = ()
    pads: tuple[InteractionPad, ...] = ()
    interaction: str | None = None
    visual_priority: int = 0
    ground_surface: bool = False
    # Authored source/placement metadata, like a native map object layer. A
    # moved object retains its complete sprite; copies reuse complete panels.
    source_visual: tuple[int, int, int, int] | None = None
    copies: tuple[tuple[int, int], ...] = ()


# Footprints and positions are visible game layout rules, not model constants.
# Rectangles may overlap visually: the renderer assigns every alpha pixel to
# exactly one catalog object, highest visual_priority first.
OBJECTS = (
    # The unfinished indoor scene is withdrawn. The house remains scene art
    # with its physical wall, not an advertised interactive destination.
    FarmObject("house", "小屋", (0, 0, 181, 116), 111,
        (rectangle(22, 31, 174, 110),), visual_priority=100),
    FarmObject("orchard", "苹果树与树前篱笆", (173, 0, 240, 117), 108,
        (rectangle(191, 83, 215, 98), rectangle(177, 98, 230, 111)),
        visual_priority=125),
    FarmObject("upper-rock", "池塘西侧石头", (332, 74, 365, 101), 98,
        (((340, 86), (356, 82), (364, 96), (340, 100)),), visual_priority=130),
    FarmObject("upper-woodland", "上方树林与篱笆", (0, 0, 512, 128), 118,
        (rectangle(0, 0, 512, 24), rectangle(243, 8, 255, 97),
         rectangle(309, 27, 329, 52), rectangle(337, 28, 370, 48),
         rectangle(406, 28, 433, 40), rectangle(444, 39, 467, 55),
         rectangle(473, 20, 512, 116)), visual_priority=10),
    FarmObject("west-tree", "西侧大树", (0, 116, 81, 217), 214,
        (rectangle(30, 186, 52, 213),
         ((0, 134), (19, 133), (23, 181), (8, 211), (0, 211))), visual_priority=70),
    FarmObject("west-stump", "树桩", (60, 193, 107, 231), 227,
        (rectangle(70, 212, 94, 225),), visual_priority=90),
    FarmObject("west-rock", "西侧石头", (79, 151, 106, 178), 176,
        (((82, 162), (95, 159), (106, 173), (86, 176)),), visual_priority=95),
    FarmObject("west-brush", "高草丛", (7, 107, 136, 162), 153,
        (((92, 133), (120, 132), (128, 149), (103, 156)),), visual_priority=35),
    FarmObject("well", "水井", (8, 226, 77, 304), 295,
        (((18, 263), (52, 257), (66, 273), (62, 293), (26, 299), (14, 282)),),
        # Front-right entrance runs past the remaining stone and through the
        # matching visible fence opening. Keep the old pads for saved positions.
        (InteractionPad((72, 296), "left"), InteractionPad((8, 296), "right"),
         InteractionPad((56, 248), "down")),
        "refill", 150),
    FarmObject("well-east-rock", "水井右侧石头", (77, 226, 112, 261), 258,
        (((83, 243), (99, 239), (110, 253), (95, 259), (81, 251)),), visual_priority=120),
    FarmObject("dock", "池塘码头", (430, 47, 486, 116), 111,
        (rectangle(433, 64, 439, 97), rectangle(465, 49, 475, 114)),
        (InteractionPad((448, 88), "up"), InteractionPad((448, 112), "up")),
        "refill", 140, True),
    FarmObject("gate", "桥头小门", (331, 278, 391, 338), 334,
        (rectangle(332, 294, 341, 337), rectangle(380, 294, 390, 337)),
        (InteractionPad((360, 296), "down"), InteractionPad((360, 344), "up")),
        "gate", 160, source_visual=(147, 270, 207, 330)),
    FarmObject("entry-fence", "原入口围栏", (126, 288, 218, 323), 318,
        (rectangle(126, 299, 207, 316),), source_visual=(208, 288, 258, 323),
        copies=((128, 288), (168, 288))),
    FarmObject("west-fence", "西侧围栏", (64, 250, 150, 330), 320,
        # Keep the two real end posts solid at their ground contact, not their
        # tall foreground artwork. The complete middle panel/post is absent.
        (((70, 242), (86, 254), (86, 260), (78, 260), (64, 252)),
         rectangle(86, 268, 96, 274), rectangle(118, 295, 128, 311),
         rectangle(42, 325, 96, 343)), visual_priority=45),
    FarmObject("south-fence", "南侧围栏", (199, 282, 335, 335), 318,
        (rectangle(202, 299, 333, 316),), visual_priority=45),
    FarmObject("east-fence", "东侧围栏", (389, 280, 451, 338), 322,
        (rectangle(390, 297, 444, 317),), visual_priority=45),
    FarmObject("southwest-rock", "西南石头", (73, 305, 128, 354), 343,
        (((81, 320), (106, 318), (120, 337), (96, 347), (78, 338)),), visual_priority=110),
    FarmObject("south-rock", "南侧石头", (253, 319, 303, 362), 357,
        (((264, 333), (285, 328), (295, 346), (275, 357), (259, 351)),), visual_priority=110),
    FarmObject("southeast-rock", "东南石头", (407, 270, 459, 342), 331,
        (((421, 296), (446, 291), (455, 317), (431, 334), (415, 318)),), visual_priority=110),
    FarmObject("east-rock", "东侧石头", (485, 184, 512, 215), 212,
        (((491, 196), (507, 192), (512, 209), (495, 215)),), visual_priority=110),
    FarmObject("east-woodland", "东侧树林", (452, 121, 512, 292), 283,
        (rectangle(483, 167, 504, 190), rectangle(461, 123, 472, 239),
         rectangle(478, 221, 512, 285)), visual_priority=55),
    FarmObject("cherry-tree", "樱花树", (448, 262, 512, 384), 373,
        (rectangle(480, 333, 503, 378),), visual_priority=115),
    FarmObject("bridge", "木桥", (329, 302, 390, 384), 384,
        (rectangle(335, 321, 345, 369), rectangle(377, 321, 387, 369)),
        (), None, 145, True),
    FarmObject("lower-woodland", "外围花草与岩岸", (0, 302, 512, 384), 353,
        (rectangle(0, 302, 67, 352), rectangle(450, 292, 512, 342)), visual_priority=5),
)
BY_ID = {item.id: item for item in OBJECTS}
ENTRY_GATE = (360, 320)
GATE_DOOR = (341, 305, 380, 335)
# The arched door artwork starts above its ground-contact collision box.
# Keeping these distinct prevents a floating crown when the door swings open
# without making the inner interaction pad collide with an overhead arch.
GATE_DOOR_VISUAL = (341, 301, 380, 335)
# Semantic surface coordinates, independent from render/depth ownership. The
# blossom crown extends left of the cherry-tree rectangle into adjacent pieces.
CHERRY_CANOPY_BOUNDS = (430, 262, 512, 384)
# Whole middle fence post plus the attached rails, leaving both original end
# posts intact. This is an authored object-span mask, not a rectangular cut
# through the heads and feet of several unrelated sprites.
WELL_FENCE_OPENING = (
    ((97, 258), (105, 263), (105, 276), (97, 268)),
    ((102, 265), (109, 264), (113, 268), (113, 288),
     (106, 293), (101, 288), (101, 271)),
    ((110, 278), (117, 282), (117, 295), (110, 289)),
)
# The remaining stone silhouette protects its touching rounded edges while
# the former post/rails are removed. It is not an extra walkable surface.
WELL_STONE_VISUALS = (
    ((84, 236), (99, 232), (108, 239), (109, 250), (102, 259),
     (89, 260), (80, 250), (80, 242)),
)
# Authored source-object span: the user removed this stone to open the well's
# front-right approach. Clear its art along with its absent collision above.
# The native ground layer is revealed; original PNG resources stay untouched.
WELL_STONE_REMOVAL = (78, 270, 109, 303)
WATER_BANK = BY_ID["dock"].pads[0].point
WELL_BANK = BY_ID["well"].pads[0].point

WATER_POLYGONS = (
    ((335, 40), (425, 32), (459, 55), (458, 103), (405, 115), (333, 102)),
    ((0, 355), (45, 350), (73, 331), (105, 337), (151, 334), (189, 338),
     (238, 329), (290, 327), (324, 341), (338, 350), (379, 352),
     (418, 341), (453, 348), (481, 356), (512, 341), (512, 384), (0, 384)),
)
# The wooden walkways override water only while the ENTIRE foot box stays on wood.
WALK_SURFACES = (rectangle(436, 70, 470, 122), rectangle(345, 329, 377, 365))


def point_in_polygon(point, polygon):
    """Boundary-inclusive ray test for source-pixel layout polygons."""
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = previous
        x2, y2 = current
        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        if cross == 0 and min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2):
            return True
        if (y1 > y) != (y2 > y):
            crossing = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < crossing:
                inside = not inside
        previous = current
    return inside


def _bounds(polygon):
    return (min(point[0] for point in polygon), min(point[1] for point in polygon),
            max(point[0] for point in polygon), max(point[1] for point in polygon))


def _foot_samples(x, y):
    return tuple((x + dx, y + dy) for dx in (-FOOT_RADIUS_X, 0, FOOT_RADIUS_X)
                 for dy in (-FOOT_RADIUS_Y, 0, FOOT_RADIUS_Y))


def _segments_intersect(a, b, c, d):
    def cross(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    first, second, third, fourth = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
    if first == 0 and min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(a[1], b[1]):
        return True
    if second == 0 and min(a[0], b[0]) <= d[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= d[1] <= max(a[1], b[1]):
        return True
    if third == 0 and min(c[0], d[0]) <= a[0] <= max(c[0], d[0]) and min(c[1], d[1]) <= a[1] <= max(c[1], d[1]):
        return True
    if fourth == 0 and min(c[0], d[0]) <= b[0] <= max(c[0], d[0]) and min(c[1], d[1]) <= b[1] <= max(c[1], d[1]):
        return True
    return (first > 0) != (second > 0) and (third > 0) != (fourth > 0)


def foot_overlaps(x, y, polygon):
    """Conservative sole rectangle test; never use a tall sprite's visual rectangle."""
    left, top, right, bottom = _bounds(polygon)
    foot = rectangle(x - FOOT_RADIUS_X, y - FOOT_RADIUS_Y, x + FOOT_RADIUS_X, y + FOOT_RADIUS_Y)
    if right < foot[0][0] or left > foot[1][0] or bottom < foot[0][1] or top > foot[2][1]:
        return False
    if any(point_in_polygon(point, polygon) for point in _foot_samples(x, y)):
        return True
    if any(foot[0][0] <= px <= foot[1][0] and foot[0][1] <= py <= foot[2][1] for px, py in polygon):
        return True
    return any(_segments_intersect(foot[index - 1], foot[index], polygon[other - 1], polygon[other])
               for index in range(4) for other in range(len(polygon)))


@lru_cache(maxsize=8192)
def walkable(x, y, gate_open=False):
    if not (FOOT_RADIUS_X < x < MAP_WIDTH - FOOT_RADIUS_X and FOOT_RADIUS_Y < y < MAP_HEIGHT - FOOT_RADIUS_Y):
        return False
    if any(foot_overlaps(x, y, polygon) for item in OBJECTS for polygon in item.footprints):
        return False
    if not gate_open and foot_overlaps(x, y, rectangle(*GATE_DOOR)):
        return False
    on_wood = any(all(point_in_polygon(point, polygon) for point in _foot_samples(x, y)) for polygon in WALK_SURFACES)
    if not on_wood and any(foot_overlaps(x, y, polygon) for polygon in WATER_POLYGONS):
        return False
    return True


def object_at(point):
    """Hit only physical/catalog objects, not whole decorative woodland rectangles."""
    candidates = [item for item in OBJECTS if item.interaction and
        item.visual[0] <= point[0] <= item.visual[2] and item.visual[1] <= point[1] <= item.visual[3]]
    if candidates:
        return max(candidates, key=lambda item: item.visual_priority)
    if point_in_polygon(point, WATER_POLYGONS[0]):
        return BY_ID["dock"]
    return None


def plot_at(point):
    """Half-open cell bounds give shared edges one unambiguous target."""
    left, top, right, bottom = FARMLAND_BOUNDS
    px, py = point
    if not (left <= px < right and top <= py < bottom):
        return None
    center = (left + int((px - left) // SOIL_SIZE) * SOIL_SIZE + SOIL_SIZE // 2,
              top + int((py - top) // SOIL_SIZE) * SOIL_SIZE + SOIL_SIZE // 2)
    return PLOT_INDEX.get(center)
