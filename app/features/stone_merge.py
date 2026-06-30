from __future__ import annotations

from typing import Any, Optional
from dataclasses import dataclass
from pathlib import Path


BOTTOM_SCAN_RATIO = 1 / 3
DEFAULT_MATCH_THRESHOLD = 0.88
DEFAULT_MIN_DISTANCE = 24
OPENCV_INSTALL_HINT = (
    "OpenCV is required for Merge stones. Install the project requirements with "
    "the supported Python runtime, or run: python -m pip install opencv-python==4.7.0.72 numpy==1.23.5"
)

_cv2: Any = None
_np: Any = None


class OpenCvUnavailableError(RuntimeError):
    pass


class StoneTemplateUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class TemplateMatch:
    template_name: str
    score: float
    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


@dataclass(frozen=True)
class MergeCandidate:
    template_name: str
    first: TemplateMatch
    second: TemplateMatch

    @property
    def drag_from(self) -> tuple[int, int]:
        return self.first.center

    @property
    def drag_to(self) -> tuple[int, int]:
        return self.second.center


class StoneMergeScanner:
    def __init__(
        self,
        template_dir: Path,
        threshold: float = DEFAULT_MATCH_THRESHOLD,
        min_distance: int = DEFAULT_MIN_DISTANCE,
    ) -> None:
        self.template_dir = template_dir
        self.threshold = threshold
        self.min_distance = min_distance

    def find_merge_candidate(self, screenshot_png: bytes) -> Optional[MergeCandidate]:
        screenshot = decode_png(screenshot_png)
        matches = self.find_matches(screenshot)
        by_template: dict[str, list[TemplateMatch]] = {}
        for match in matches:
            by_template.setdefault(match.template_name, []).append(match)

        for template_matches in by_template.values():
            if len(template_matches) >= 2:
                ordered = sorted(template_matches, key=lambda item: item.score, reverse=True)
                return MergeCandidate(ordered[0].template_name, ordered[0], ordered[1])
        return None

    def find_matches(self, screenshot: Any) -> list[TemplateMatch]:
        cv2, _ = load_opencv()
        template_paths = self._template_paths()
        if not template_paths:
            raise StoneTemplateUnavailableError(
                f"No stone templates found in {self.template_dir}. "
                "Add cropped .png/.jpg stone images before running Merge stones."
            )
        scan_area, y_offset = bottom_scan_area(screenshot)
        matches: list[TemplateMatch] = []
        for template_path in template_paths:
            template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
            if template is None:
                continue
            raw_matches = match_template(scan_area, template, template_path.stem, self.threshold, y_offset)
            matches.extend(suppress_nearby_matches(raw_matches, self.min_distance))
        return matches

    def _template_paths(self) -> list[Path]:
        if not self.template_dir.is_dir():
            return []
        return sorted(
            path
            for path in self.template_dir.iterdir()
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}
        )


def load_opencv() -> tuple[Any, Any]:
    global _cv2, _np
    if _cv2 is not None and _np is not None:
        return _cv2, _np
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise OpenCvUnavailableError(OPENCV_INSTALL_HINT) from exc
    _cv2 = cv2
    _np = np
    return _cv2, _np


def decode_png(png_bytes: bytes) -> Any:
    cv2, np = load_opencv()
    image = cv2.imdecode(np.frombuffer(png_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Could not decode screenshot PNG")
    return image


def bottom_scan_area(image: Any) -> tuple[Any, int]:
    height = image.shape[0]
    y_offset = int(height * (1 - BOTTOM_SCAN_RATIO))
    return image[y_offset:, :], y_offset


def match_template(
    scan_area: Any,
    template: Any,
    template_name: str,
    threshold: float,
    y_offset: int,
) -> list[TemplateMatch]:
    cv2, np = load_opencv()
    if template.shape[0] > scan_area.shape[0] or template.shape[1] > scan_area.shape[1]:
        return []

    result = cv2.matchTemplate(scan_area, template, cv2.TM_CCOEFF_NORMED)
    y_positions, x_positions = np.where(result >= threshold)
    height, width = template.shape[:2]
    matches = [
        TemplateMatch(
            template_name=template_name,
            score=float(result[y, x]),
            x=int(x),
            y=int(y + y_offset),
            width=width,
            height=height,
        )
        for y, x in zip(y_positions, x_positions)
    ]
    return sorted(matches, key=lambda item: item.score, reverse=True)


def suppress_nearby_matches(matches: list[TemplateMatch], min_distance: int) -> list[TemplateMatch]:
    kept: list[TemplateMatch] = []
    for match in matches:
        if all(distance(match.center, other.center) >= min_distance for other in kept):
            kept.append(match)
    return kept


def distance(first: tuple[int, int], second: tuple[int, int]) -> float:
    _, np = load_opencv()
    return float(np.hypot(first[0] - second[0], first[1] - second[1]))
