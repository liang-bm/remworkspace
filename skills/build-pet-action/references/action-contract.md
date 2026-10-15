# Action contract

## Required inputs

- A character reference or existing action set that establishes identity.
- A chronological sprite sheet, or enough detail to generate one.
- Exact grid columns and rows.
- Action name using lowercase letters, digits, `_`, or `-`.
- Target playback intent: `once`, `loop`, `pingpong`, or `mirror_second_pass`.

Do not silently guess a grid when cell boundaries are ambiguous.

## Image invariants

- Every output is RGBA PNG.
- All frames share the same canvas dimensions.
- Character scale and foot anchor remain stable unless translation is the action.
- No opaque foreground touches the canvas edge.
- Detached effects belonging to the action remain with their cell.
- Frame labels and sheet-only annotations are removed before slicing.

The corner flood algorithm removes only background connected to a cell edge. This is deliberate: a global black color key would damage character outlines.

Keep RGB straight/unassociated in PNG. Do not flatten to black, the desktop
window's colour key, or an assumed wallpaper; do not save premultiplied RGB as
ordinary RGBA. Antialiased alpha is valid foreground and must be preserved.
Desktop acceptance includes the actual renderer, not just the exported file.
Read [alpha-presentation.md](alpha-presentation.md) for that check.

## Timing

Start near 120–180 ms per frame for chibi pets. Slower frame duration does not create missing in-between motion; add/reorder poses when the trajectory itself is discontinuous. Use `mirror_second_pass` for a genuinely symmetric sway, not as a generic smoothing switch.

## Hook schema

```json
{
  "asset_root": "relative/path/to/action/directories",
  "registry_json": "optional/relative/registry.json"
}
```

Both paths resolve inside the provided project root. `registry_json` is optional and must contain a JSON object. Installation never edits executable source code automatically.
