# Transparent frames without renderer halos

Use this when action frames look clean in a PNG viewer but develop a dark or
light contour in a desktop window, or when an action has more semitransparent
edge pixels than existing actions.

## Locate the defect before changing art

1. Compare the original action and a known-good action on both a light and a
   dark background, at the actual playback size. Inspect the alpha channel.
2. If the contour exists in these correctly alpha-composited previews, it may
   be an authored sticker border or a matte baked into RGB. Regenerate/edit the
   artwork with the original character reference; never erase every dark pixel.
3. If previews are clean but the desktop is not, inspect the window compositor.
   Tk `-transparentcolor` removes one exact colour, not fractional opacity.
   Drawing RGBA over near-black first bakes a black matte into soft edges; a
   later colour-key operation cannot reconstruct the lost opacity.

## Frame and presentation contracts

- Keep the original PNG as straight RGBA with its full alpha channel.
- On Windows use per-pixel alpha (`UpdateLayeredWindow`, `ULW_ALPHA`,
  `AC_SRC_ALPHA`). Convert straight RGBA to premultiplied BGRA **once at that
  native boundary**, not in the on-disk asset and not twice.
- Microsoft documents that an existing `SetLayeredWindowAttributes`/colour-key
  presentation requires clearing and resetting `WS_EX_LAYERED` before using
  `UpdateLayeredWindow`. Do not mix both modes on the same active surface.
- Cache decoded frame bytes and reuse native drawing objects. Do not decode,
  resize, write PNGs or allocate a new DC on every animation tick. Bound caches
  and release native objects on resize/reload/exit.
- Linux silhouette masks and colour-key-only hosts are separate presentation
  targets. Check their actual output; do not claim smooth fractional alpha
  from a binary shape mask.

## Verification

Run `validate_action.py` with `--alpha-preview PATH` to create a light/dark
contact sheet. `--renderer color-key` reports an error when partial alpha
cannot be represented; `--renderer unknown` reports an unverified-renderer
warning. Use `per-pixel-alpha` only after checking the actual host, not as a way
to silence warnings.

Check black line art, pale hair highlights, detached effects and every supported
size. Check half-transparent synthetic pixels over known light/dark surfaces,
then play the real full action, including movement, hide/show and wake-up. A
correct numerical export is not a substitute for checking the desktop result.

Primary API references:

- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-updatelayeredwindow
- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setlayeredwindowattributes
