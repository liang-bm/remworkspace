"""Legacy import route; the X11 implementation lives only under platforms/linux."""
from __future__ import annotations
from ..platforms import native_module


def create_pet_silhouette(root):
    return native_module("windowing").create_pet_silhouette(root)


def __getattr__(name: str):
    """Explicit diagnostic compatibility, with no Linux import during Windows UI."""
    if name in {"PetSilhouette", "_ShapeConnection", "_opaque_rectangles",
                "_Cookie", "_Rectangle", "_Version"}:
        return getattr(native_module("x11_shape", platform="linux"), name)
    raise AttributeError(name)
