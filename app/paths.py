from __future__ import annotations

from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
STONE_TEMPLATE_DIR = APP_ROOT / "assets" / "templates" / "stones"
OUTPUT_DIR = APP_ROOT / "outputs"
