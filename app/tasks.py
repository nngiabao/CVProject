from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.emulators.base import EmulatorProvider
from app.features.stone_merge import MergeCandidate, StoneMergeScanner


STONE_TEMPLATE_DIR = Path("assets/templates/stones")


@dataclass(frozen=True, slots=True)
class StoneMergeResult:
    found: bool
    message: str
    candidate: MergeCandidate | None = None


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
