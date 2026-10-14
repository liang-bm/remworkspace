"""主聊天区背景图片的发现、等比裁切与淡化处理。"""

from __future__ import annotations

from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:  # 源码模式未安装 Pillow 时保持聊天功能可用。
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]


BACKGROUND_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}
DEFAULT_BACKGROUND_FADE = 62
# Compatibility name: this is a quiet local-date check, not a slideshow.
BACKGROUND_ROTATION_MS = 60_000


def pillow_available() -> bool:
    """返回当前运行时是否具备背景解码与高质量缩放能力。"""

    return Image is not None and ImageOps is not None


def discover_background_files(root: Path) -> list[Path]:
    """Discover resource backgrounds and the read-only legacy directory."""
    found = {}
    for directory in (root / "resources" / "backgrounds", root / "background"):
        if directory.is_dir():
            for path in directory.iterdir():
                if path.is_file() and path.suffix.lower() in BACKGROUND_EXTENSIONS:
                    found.setdefault(path.name.casefold(), path)
    return sorted(found.values(), key=lambda path: path.name.casefold())


def render_faded_cover(
    path: Path,
    size: tuple[int, int],
    *,
    base_color: str,
    fade_percent: float = DEFAULT_BACKGROUND_FADE,
):
    """按 cover 规则等比缩放、居中裁切，再与界面底色混合淡化。

    Pillow 会依据文件头而不是扩展名识别格式，因此即使图片实际为 AVIF、
    但文件名暂时写成 ``.png``，仍然可以正常载入。
    """

    if not pillow_available():
        raise RuntimeError("当前运行时没有安装 Pillow")
    width, height = size
    if width <= 0 or height <= 0:
        raise ValueError("背景渲染尺寸必须大于 0")
    fade = min(100.0, max(0.0, float(fade_percent)))
    # 对用户暴露的是“淡化量”：0% 为原图，100% 为纯界面底色。
    image_strength = 1.0 - fade / 100.0
    assert Image is not None
    assert ImageOps is not None
    with Image.open(path) as source:
        cover = ImageOps.fit(
            ImageOps.exif_transpose(source).convert("RGB"),
            (width, height),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
    base = Image.new("RGB", cover.size, base_color)
    return Image.blend(base, cover, image_strength)
