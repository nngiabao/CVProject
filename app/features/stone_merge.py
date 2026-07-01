from __future__ import annotations

import sys
from typing import Any, Optional
from dataclasses import dataclass
from pathlib import Path


DEFAULT_BAG_REGION = (27, 438, 516, 199)
DEFAULT_FIRST_SLOT = (41, 448, 59, 55)
DEFAULT_SLOT_STEP = (62, 64)
DEFAULT_MATCH_THRESHOLD = 0.88
DEFAULT_SLOT_MATCH_THRESHOLD = 0.83
DEFAULT_SLOT_CONFIDENCE_GAP = 0.04
DEFAULT_MIN_DISTANCE = 24
DEFAULT_SLOT_ROWS = 3
DEFAULT_SLOT_COLUMNS = 8
OPENCV_INSTALL_HINT = (
    "OpenCV is required for Merge stones. Install the project requirements with "
    "the Python runtime that starts this app."
)

_cv2: Any = None
_np: Any = None


class OpenCvUnavailableError(RuntimeError):
    pass


class StoneTemplateUnavailableError(RuntimeError):
    pass


class StoneTemplateSelectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScanRegion:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


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
class SlotDetection:
    row: int
    column: int
    x: int
    y: int
    width: int
    height: int
    template_name: Optional[str]
    score: float
    second_score: float
    confident: bool

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2

    @property
    def label(self) -> str:
        if self.template_name is None:
            return "empty"
        if not self.confident:
            return f"? {self.template_name}"
        return self.template_name


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


@dataclass(frozen=True)
class DebugOverlayResult:
    path: Path
    match_count: int
    template_count: int
    uncertain_count: int
    slot_count: int


class StoneMergeScanner:
    def __init__(
        self,
        template_dir: Path,
        threshold: float = DEFAULT_MATCH_THRESHOLD,
        min_distance: int = DEFAULT_MIN_DISTANCE,
        scan_region: ScanRegion = ScanRegion(*DEFAULT_BAG_REGION),
        first_slot: ScanRegion = ScanRegion(*DEFAULT_FIRST_SLOT),
        slot_step: tuple[int, int] = DEFAULT_SLOT_STEP,
        slot_rows: int = DEFAULT_SLOT_ROWS,
        slot_columns: int = DEFAULT_SLOT_COLUMNS,
        slot_threshold: float = DEFAULT_SLOT_MATCH_THRESHOLD,
        slot_confidence_gap: float = DEFAULT_SLOT_CONFIDENCE_GAP,
    ) -> None:
        self.template_dir = template_dir
        self.threshold = threshold
        self.min_distance = min_distance
        self.scan_region = scan_region
        self.first_slot = first_slot
        self.slot_step = slot_step
        self.slot_rows = slot_rows
        self.slot_columns = slot_columns
        self.slot_threshold = slot_threshold
        self.slot_confidence_gap = slot_confidence_gap
        self.enabled_templates: Optional[set[str]] = None

    def set_enabled_templates(self, names: Optional[set[str]]) -> None:
        self.enabled_templates = names

    def find_merge_candidate(self, screenshot_png: bytes) -> Optional[MergeCandidate]:
        screenshot = decode_png(screenshot_png)
        matches = [
            slot_detection_to_match(slot)
            for slot in self.classify_slots(screenshot)
            if slot.confident and slot.template_name is not None
        ]
        by_template: dict[str, list[TemplateMatch]] = {}
        for match in matches:
            by_template.setdefault(match.template_name, []).append(match)

        for template_matches in by_template.values():
            if len(template_matches) >= 2:
                ordered = sorted(template_matches, key=lambda item: item.score, reverse=True)
                return MergeCandidate(ordered[0].template_name, ordered[0], ordered[1])
        return None

    def classify_slots(self, screenshot: Any) -> list[SlotDetection]:
        template_paths = self._template_paths()
        if not template_paths:
            if self.enabled_templates is not None and not self.enabled_templates:
                raise StoneTemplateSelectionError("No stone templates are enabled for merging.")
            raise StoneTemplateUnavailableError(
                f"No stone templates found in {self.template_dir}. "
                "Add cropped .png/.jpg stone images before running Merge stones."
            )

        templates = []
        for template_path in template_paths:
            template, mask = load_template_image(template_path)
            if template is not None:
                templates.append((template_path.stem, template, mask))

        slots = slot_regions_for_grid(self.first_slot, self.slot_step, self.slot_rows, self.slot_columns, screenshot)
        detections: list[SlotDetection] = []
        for row, column, slot in slots:
            slot_image = screenshot[slot.y:slot.bottom, slot.x:slot.right]
            scores = sorted(
                (
                    (name, best_template_score(slot_image, template, mask))
                    for name, template, mask in templates
                    if template.shape[0] <= slot_image.shape[0] and template.shape[1] <= slot_image.shape[1]
                ),
                key=lambda item: item[1],
                reverse=True,
            )
            best_name = scores[0][0] if scores else None
            best_score = scores[0][1] if scores else 0.0
            second_score = scores[1][1] if len(scores) > 1 else 0.0
            confident = (
                best_name is not None
                and best_score >= self.slot_threshold
                and best_score - second_score >= self.slot_confidence_gap
            )
            detections.append(
                SlotDetection(
                    row=row,
                    column=column,
                    x=slot.x,
                    y=slot.y,
                    width=slot.width,
                    height=slot.height,
                    template_name=best_name,
                    score=best_score,
                    second_score=second_score,
                    confident=confident,
                )
            )
        return detections

    def find_matches(self, screenshot: Any) -> list[TemplateMatch]:
        template_paths = self._template_paths()
        if not template_paths:
            if self.enabled_templates is not None and not self.enabled_templates:
                raise StoneTemplateSelectionError("No stone templates are enabled for merging.")
            raise StoneTemplateUnavailableError(
                f"No stone templates found in {self.template_dir}. "
                "Add cropped .png/.jpg stone images before running Merge stones."
            )
        scan_area, x_offset, y_offset = scan_area_for_region(screenshot, self.scan_region)
        matches: list[TemplateMatch] = []
        for template_path in template_paths:
            template, mask = load_template_image(template_path)
            if template is None:
                continue
            raw_matches = match_template(scan_area, template, mask, template_path.stem, self.threshold, x_offset, y_offset)
            matches.extend(suppress_nearby_matches(raw_matches, self.min_distance))
        return matches

    def template_names(self) -> list[str]:
        return [path.stem for path in self._template_paths()]

    def write_debug_overlay(self, screenshot_png: bytes, output_path: Path) -> DebugOverlayResult:
        cv2, _ = load_opencv()
        screenshot = decode_png(screenshot_png)
        region = clamp_region(self.scan_region, screenshot)
        overlay = screenshot.copy()
        cv2.rectangle(overlay, (region.x, region.y), (region.right, region.bottom), (0, 255, 255), 3)
        slot_region = slot_bounds_for_grid(self.first_slot, self.slot_step, self.slot_rows, self.slot_columns)
        slot_region = clamp_region(slot_region, screenshot)
        cv2.rectangle(
            overlay,
            (slot_region.x, slot_region.y),
            (slot_region.right, slot_region.bottom),
            (0, 120, 255),
            2,
        )
        cv2.putText(
            overlay,
            f"bag {region.x},{region.y} {region.width}x{region.height} | slots {slot_region.x},{slot_region.y} {slot_region.width}x{slot_region.height}",
            (region.x, max(20, region.y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )
        try:
            slots = self.classify_slots(screenshot)
        except StoneTemplateUnavailableError:
            slots = []
        matches = [
            slot_detection_to_match(slot)
            for slot in slots
            if slot.confident and slot.template_name is not None
        ]
        for slot in slots:
            color = (0, 220, 0) if slot.confident else (0, 0, 255)
            cv2.rectangle(
                overlay,
                (slot.x, slot.y),
                (slot.x + slot.width, slot.y + slot.height),
                color,
                1,
            )
            if slot.template_name is None:
                continue
            cv2.putText(
                overlay,
                f"{slot.label} {slot.score:.2f}",
                (slot.x + 2, max(20, slot.y + 14)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.35,
                color,
                1,
                cv2.LINE_AA,
            )
        for match in matches:
            cv2.rectangle(
                overlay,
                (match.x, match.y),
                (match.x + match.width, match.y + match.height),
                (0, 220, 0),
                2,
            )
            cv2.putText(
                overlay,
                match.template_name,
                (match.x, max(20, match.y - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (0, 220, 0),
                1,
                cv2.LINE_AA,
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_path), overlay)
        uncertain_count = len(
            [slot for slot in slots if slot.template_name is not None and not slot.confident]
        )
        return DebugOverlayResult(output_path, len(matches), len(self._template_paths()), uncertain_count, len(slots))

    def _template_paths(self) -> list[Path]:
        if not self.template_dir.is_dir():
            return []
        paths = sorted(
            path
            for path in self.template_dir.iterdir()
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}
        )
        if self.enabled_templates is None:
            return paths
        return [path for path in paths if path.stem in self.enabled_templates]


def load_opencv() -> tuple[Any, Any]:
    global _cv2, _np
    if _cv2 is not None and _np is not None:
        return _cv2, _np
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise OpenCvUnavailableError(
            f"{OPENCV_INSTALL_HINT}\n\n"
            f"Python used by app:\n{sys.executable}\n\n"
            "Install with:\n"
            f"\"{sys.executable}\" -m pip install opencv-python==4.7.0.72 numpy==1.23.5"
        ) from exc
    _cv2 = cv2
    _np = np
    return _cv2, _np


def decode_png(png_bytes: bytes) -> Any:
    cv2, np = load_opencv()
    image = cv2.imdecode(np.frombuffer(png_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Could not decode screenshot PNG")
    return image


def load_template_image(template_path: Path) -> tuple[Any, Any]:
    cv2, np = load_opencv()
    image = cv2.imread(str(template_path), cv2.IMREAD_UNCHANGED)
    if image is None:
        return None, None
    if len(image.shape) < 3 or image.shape[2] < 4:
        return image, None

    alpha = image[:, :, 3]
    visible = np.where(alpha > 0)
    if visible[0].size == 0 or visible[1].size == 0:
        return None, None

    top = int(visible[0].min())
    bottom = int(visible[0].max()) + 1
    left = int(visible[1].min())
    right = int(visible[1].max()) + 1
    cropped = image[top:bottom, left:right]
    return cropped[:, :, :3], cropped[:, :, 3]


def clamp_region(region: ScanRegion, image: Any) -> ScanRegion:
    height, width = image.shape[:2]
    x = max(0, min(region.x, width - 1))
    y = max(0, min(region.y, height - 1))
    right = max(x + 1, min(region.right, width))
    bottom = max(y + 1, min(region.bottom, height))
    return ScanRegion(x, y, right - x, bottom - y)


def scan_area_for_region(image: Any, region: ScanRegion) -> tuple[Any, int, int]:
    clamped = clamp_region(region, image)
    return image[clamped.y:clamped.bottom, clamped.x:clamped.right], clamped.x, clamped.y


def slot_regions_for_grid(
    first_slot: ScanRegion,
    slot_step: tuple[int, int],
    rows: int,
    columns: int,
    image: Any,
) -> list[tuple[int, int, ScanRegion]]:
    slots: list[tuple[int, int, ScanRegion]] = []
    step_x, step_y = slot_step
    for row in range(rows):
        for column in range(columns):
            slot = ScanRegion(
                first_slot.x + column * step_x,
                first_slot.y + row * step_y,
                first_slot.width,
                first_slot.height,
            )
            slots.append((row, column, clamp_region(slot, image)))
    return slots


def slot_bounds_for_grid(
    first_slot: ScanRegion,
    slot_step: tuple[int, int],
    rows: int,
    columns: int,
) -> ScanRegion:
    step_x, step_y = slot_step
    return ScanRegion(
        first_slot.x,
        first_slot.y,
        (columns - 1) * step_x + first_slot.width,
        (rows - 1) * step_y + first_slot.height,
    )


def best_template_score(slot_image: Any, template: Any, mask: Any) -> float:
    cv2, np = load_opencv()
    if template.shape[0] > slot_image.shape[0] or template.shape[1] > slot_image.shape[1]:
        return 0.0
    if mask is not None:
        result = cv2.matchTemplate(slot_image, template, cv2.TM_CCORR_NORMED, mask=mask)
        result = np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
    else:
        result = cv2.matchTemplate(slot_image, template, cv2.TM_CCOEFF_NORMED)
    return float(result.max()) if result.size else 0.0


def slot_detection_to_match(slot: SlotDetection) -> TemplateMatch:
    return TemplateMatch(
        template_name=slot.template_name or "",
        score=slot.score,
        x=slot.x,
        y=slot.y,
        width=slot.width,
        height=slot.height,
    )


def match_template(
    scan_area: Any,
    template: Any,
    mask: Any,
    template_name: str,
    threshold: float,
    x_offset: int,
    y_offset: int,
) -> list[TemplateMatch]:
    cv2, np = load_opencv()
    if template.shape[0] > scan_area.shape[0] or template.shape[1] > scan_area.shape[1]:
        return []

    if mask is not None:
        result = cv2.matchTemplate(scan_area, template, cv2.TM_CCORR_NORMED, mask=mask)
        result = np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
    else:
        result = cv2.matchTemplate(scan_area, template, cv2.TM_CCOEFF_NORMED)
    y_positions, x_positions = np.where(result >= threshold)
    height, width = template.shape[:2]
    matches = [
        TemplateMatch(
            template_name=template_name,
            score=float(result[y, x]),
            x=int(x + x_offset),
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
