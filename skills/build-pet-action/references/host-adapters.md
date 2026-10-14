# Host adapters

This skill is intentionally host-neutral. Capability names differ; behavior does not.

## Capability detection

Before generating, inspect the tools exposed in the current CLI/session for an image-generation or image-editing capability.

- **Codex:** use the available image generation tool/skill when present.
- **Claude Code:** use an installed image-generation MCP/skill when present.
- **Other agents:** use their declared image tool only when it can accept the identity reference and sprite-sheet prompt.

Do not claim a tool exists without inspecting the current host.

## No image tool

Return:

1. the completed generation prompt;
2. exact grid dimensions and cell order;
3. recommended flat background color or transparency;
4. the expected input filename;
5. a short request for the user to attach/save the generated sheet.

Pause visual generation at that boundary. Once the sheet exists locally, resume with the deterministic scripts without asking the user to repeat the action description.

## Image tool available

Generate the sheet, inspect it, and regenerate only for concrete defects such as identity drift, wrong grid, missing limb, unreadable chronology, or cropped action effects. Do not use repeated generations as a substitute for deterministic slicing or cleanup.
