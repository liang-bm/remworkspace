# Sprite-sheet prompting

Build one prompt from observed character facts, not assumptions.

## Prompt structure

1. **Identity lock:** character, costume, proportions, eye/hair details, rendering style, outline treatment. Match the existing line weight: no extra sticker border, outer glow or opaque black/white matte.
2. **Technical layout:** exact `COLUMNS × ROWS`, equal cells, chronological left-to-right then top-to-bottom order, full body, consistent camera and scale.
3. **Motion beats:** one unambiguous pose per cell. Describe weight shift, hands, face, and effect graphics.
4. **Background:** transparent when supported; otherwise one flat removable color that does not appear in the character.
5. **Exclusions:** no captions, numbers, watermark, cell borders, duplicate limbs, cropped effects, perspective change, or costume redesign.

## Reusable prompt

```text
Create a single [COLUMNS]×[ROWS] chronological sprite sheet for a desktop-pet action named “[ACTION]”.

Identity reference: [OBSERVED CHARACTER DETAILS].
Keep the same chibi proportions, costume, face, line weight, palette, and camera in every cell.

Cell order is left-to-right, then top-to-bottom:
1. [POSE]
2. [POSE]
...

Use equal cells and a stable full-body scale. Keep both feet and all action effects inside each cell. Detached hearts, puffs, sparkles, or motion accents specified above are intentional foreground and must be retained.

Background: [TRANSPARENT OR FLAT COLOR].
No text, frame numbers, watermark, panel border, scenery, extra limbs, missing accessories, costume changes, or cropped effects.
```

For symmetric sways, generate the complete first half with readable center and extreme poses. Deterministic mirroring can create the second pass after slicing.
