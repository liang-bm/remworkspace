"""Linux 桌面字体配置：只影响本进程，不改系统或用户 fontconfig。"""

from __future__ import annotations

from pathlib import Path
import os
import sys
import xml.etree.ElementTree as ET


def configure_fonts(root: Path) -> bool:
    """使用安装脚本准备的 CJK 字体；尊重调用者显式的 FONTCONFIG_FILE。"""

    if os.environ.get("FONTCONFIG_FILE"):
        return False
    root = root.resolve()
    # Frozen releases contain read-only resources, not the developer runtime.
    bundled = Path(__file__).resolve().parent / "resources" / "fonts"
    fonts = bundled if (bundled / "NotoSansCJKsc-Regular.otf").is_file() else root / "runtime" / "linux-fonts"
    if not (fonts / "NotoSansCJKsc-Regular.otf").is_file():
        return False
    cache = root / "cache" / "fontconfig-linux"
    cache.mkdir(parents=True, exist_ok=True)
    document = ET.Element("fontconfig")
    for directory in (fonts, Path(sys.prefix) / "fonts", Path("/usr/share/fonts"), Path("/usr/local/share/fonts")):
        if directory.is_dir():
            ET.SubElement(document, "dir").text = str(directory)
    # Tk 8.6/Xft can abort the process when uploading CBDT colour bitmap
    # glyphs (RenderAddGlyphs/BadLength). Only this app's native text fallback
    # excludes colour font faces. Its offline Twemoji PNG presentation remains
    # coloured, and neither the system fonts nor other programs are changed.
    rejected=ET.SubElement(ET.SubElement(document,'selectfont'),'rejectfont')
    # Old bundled fontconfig reports the colour property as DontCare. Its
    # filename/family filter still works, so cover the confirmed CBDT face.
    ET.SubElement(rejected,'glob').text='*/NotoColorEmoji*.ttf'
    known=ET.SubElement(rejected,'pattern')
    ET.SubElement(ET.SubElement(known,'patelt',name='family'),'string').text='Noto Color Emoji'
    pattern=ET.SubElement(rejected,'pattern')
    ET.SubElement(ET.SubElement(pattern,'patelt',name='color'),'bool').text='true'
    ET.SubElement(document, "cachedir").text = str(cache)
    for family in ("Microsoft YaHei UI", "sans-serif"):
        alias = ET.SubElement(document, "alias")
        ET.SubElement(alias, "family").text = family
        ET.SubElement(ET.SubElement(alias, "prefer"), "family").text = "Noto Sans CJK SC"
    # Windows font names in shared code remain genuine monospace on Linux.
    # Do not let a missing Consolas face match a proportional Noto Sans font.
    for family in ('Consolas','Cascadia Mono','monospace'):
        alias=ET.SubElement(document,'alias')
        ET.SubElement(alias,'family').text=family
        preferred=ET.SubElement(alias,'prefer')
        for fallback in ('DejaVu Sans Mono','Liberation Mono','Noto Sans Mono'):
            ET.SubElement(preferred,'family').text=fallback
    content = '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
    content += ET.tostring(document, encoding="unicode") + "\n"
    target = cache / "fontconfig-remchat.conf"
    # 路径与解释器可能改变；原子替换只更新这一份应用生成的文件。
    if not target.is_file() or target.read_text(encoding="utf-8") != content:
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache, prefix="fontconfig-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        try:
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    os.environ["FONTCONFIG_FILE"] = str(target)
    return True
