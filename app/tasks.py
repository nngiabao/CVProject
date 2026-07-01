from __future__ import annotations

from typing import Optional
from dataclasses import dataclass
from pathlib import Path

from app.emulators.base import EmulatorProvider
from app.features.stone_merge import MergeCandidate, StoneMergeScanner
from app.paths import STONE_TEMPLATE_DIR


@dataclass(frozen=True)
class StoneMergeResult:
    found: bool
    message: str
    candidate: Optional[MergeCandidate] = None


class StoneMergeTaskRunner:
    def __init__(self, provider: EmulatorProvider, template_dir: Path = STONE_TEMPLATE_DIR) -> None:
        self.provider = provider
        self.scanner = StoneMergeScanner(template_dir)

    def scan(self, instance_index: int) -> StoneMergeResult:
        screenshot = self.provider.screenshot_png(instance_index)
        candidate = self.scanner.find_merge_candidate(screenshot)
        if candidate is None:
            return StoneMergeResult(False, "No matching stone pair found")
        return StoneMergeResult(
            True,
            f"Merge {candidate.template_name}: {candidate.drag_from} -> {candidate.drag_to}",
            candidate,
        )
