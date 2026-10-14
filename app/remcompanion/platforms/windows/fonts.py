"""Windows uses the installed native UI fonts; no fontconfig is modified."""
from pathlib import Path

def configure_fonts(root: Path) -> bool:
    return False
