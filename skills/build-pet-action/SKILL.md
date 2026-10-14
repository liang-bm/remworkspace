---
name: build-pet-action
description: Generate, slice, clean, validate, and safely install animated desktop-pet action frames from a natural-language action request or an existing sprite sheet. Use when adding a new pet emotion/action, converting a generated grid into transparent PNG frames, preserving detached effects such as hearts or puffs, fixing unstable frame canvases, or packaging an action for a hooked pet project.
---

# Build Pet Action

Turn an action idea into reproducible pet frames without letting generative output edit project code directly.

Requires Python 3.10+ and Pillow for the deterministic image scripts. Use the
project/host Python environment when it already provides Pillow.

## Workflow

1. Inspect the target character reference, existing action frames, canvas size, naming pattern, and project hook. Read [action-contract.md](references/action-contract.md).
2. Write an explicit generation brief using [prompting.md](references/prompting.md). Preserve character identity and describe one chronological pose per grid cell.
3. Detect the host's image-generation capability using [host-adapters.md](references/host-adapters.md):
   - If an image tool is available, use it to create the sprite sheet.
   - If it is unavailable, output the complete prompt and expected grid, then pause until the user supplies the image.
   - Never draw the character programmatically. Python/Pillow is only for deterministic processing after artwork exists.
4. Visually inspect the supplied/generated sheet. Confirm grid order, complete limbs, consistent scale, and that effect graphics belong to their intended cells.
5. Run `scripts/build_action.py`. Set the exact grid and any bottom label strip. Use `mirror_second_pass` only when the motion is intentionally symmetric.
6. Run `scripts/validate_action.py`. Treat errors as blockers. Inspect `contact-sheet.png` for identity drift, foot jitter, missing effects, and incorrect ordering.
7. Run `scripts/install_action.py` with a reviewed hook. The installer validates paths, stages files, preserves the previous action as a timestamped backup, and updates only an optional JSON registry.

## Build Command

```bash
python scripts/build_action.py \
  --source action-sheet.png \
  --output out/happy \
  --name happy \
  --columns 5 \
  --rows 2 \
  --label-strip-px 32 \
  --background-mode corner \
  --sequence mirror_second_pass \
  --frame-duration-ms 150
```

Use `--background-mode alpha` for an already transparent sheet. Corner flood removal preserves black outlines and detached foreground effects; adjust `--background-tolerance` only when background pixels remain.

## Validate and Install

```bash
python scripts/validate_action.py out/happy --report out/happy-report.json
python scripts/install_action.py \
  --project-root /path/to/project \
  --name happy \
  --frames out/happy \
  --hook hooks/generic.json
```

Never install a failing sequence. Never infer an unknown grid from wishful thinking: inspect the image or ask for the missing grid dimensions.

## Output Contract

The builder creates:

- `<action>_001.png` onward, RGBA with one stable canvas;
- `action.json` containing timing and build provenance;
- `contact-sheet.png` for visual QA;
- a timestamped sibling backup if the output already existed.

Detached action graphics are valid foreground and must remain. Bottom frame numbers, sheet labels, watermarks, and background decoration are not.
