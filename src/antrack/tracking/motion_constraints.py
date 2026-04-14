"""Helpers for forbidden-range parsing and constrained AZ/EL routing."""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple


RangeList = List[Tuple[float, float]]
_RANGE_PATTERN = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*-\s*([+-]?\d+(?:\.\d+)?)\s*$")
_EPS = 1e-9


def parse_forbidden_ranges(raw, default: Optional[Iterable[Tuple[float, float]]] = None) -> RangeList:
    if raw is None:
        return list(default or [])
    if isinstance(raw, (list, tuple)):
        out: RangeList = []
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                try:
                    out.append((float(item[0]), float(item[1])))
                except Exception:
                    pass
        return out if out else list(default or [])
    if not isinstance(raw, str):
        return list(default or [])

    out: RangeList = []
    for chunk in raw.split(","):
        match = _RANGE_PATTERN.match(chunk.strip())
        if not match:
            continue
        out.append((float(match.group(1)), float(match.group(2))))
    return out if out else list(default or [])


def normalize_angle(angle_deg: float) -> float:
    return float(angle_deg) % 360.0


def _normalize_azimuth_ranges(ranges: Iterable[Tuple[float, float]]) -> RangeList:
    out: RangeList = []
    for start, end in ranges:
        a = normalize_angle(start)
        b = normalize_angle(end)
        if abs(a - b) < _EPS:
            out.append((0.0, 360.0))
        elif a < b:
            out.append((a, b))
        else:
            out.append((a, 360.0))
            out.append((0.0, b))
    return out


def _strict_overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    return max(a_start, b_start) < (min(a_end, b_end) - _EPS)


def point_in_linear_ranges(value: float, ranges: Iterable[Tuple[float, float]]) -> bool:
    val = float(value)
    for start, end in ranges:
        lo = min(float(start), float(end))
        hi = max(float(start), float(end))
        if lo < val < hi:
            return True
    return False


def point_in_azimuth_ranges(angle_deg: float, ranges: Iterable[Tuple[float, float]]) -> bool:
    angle = normalize_angle(angle_deg)
    for start, end in _normalize_azimuth_ranges(ranges):
        if start < angle < end:
            return True
    return False


def _azimuth_path_segments(current: float, target: float, direction: str) -> RangeList:
    current = normalize_angle(current)
    target = normalize_angle(target)
    if direction == "CW":
        if target >= current:
            return [(current, target)]
        return [(current, 360.0), (0.0, target)]
    if target <= current:
        return [(target, current)]
    return [(0.0, current), (target, 360.0)]


def azimuth_path_clear(current: float, target: float, direction: str, ranges: Iterable[Tuple[float, float]]) -> bool:
    if point_in_azimuth_ranges(current, ranges) or point_in_azimuth_ranges(target, ranges):
        return False
    path_segments = _azimuth_path_segments(current, target, direction)
    for forbidden_start, forbidden_end in _normalize_azimuth_ranges(ranges):
        for seg_start, seg_end in path_segments:
            if _strict_overlap(seg_start, seg_end, forbidden_start, forbidden_end):
                return False
    return True


def constrained_azimuth_error(current: float, target: float, ranges: Iterable[Tuple[float, float]]) -> Optional[float]:
    current_n = normalize_angle(current)
    target_n = normalize_angle(target)
    cw_distance = (target_n - current_n) % 360.0
    ccw_distance = (current_n - target_n) % 360.0

    if min(cw_distance, ccw_distance) < _EPS:
        return 0.0

    cw_clear = azimuth_path_clear(current_n, target_n, "CW", ranges)
    ccw_clear = azimuth_path_clear(current_n, target_n, "CCW", ranges)

    if cw_clear and ccw_clear:
        return -cw_distance if cw_distance <= ccw_distance else ccw_distance
    if cw_clear:
        return -cw_distance
    if ccw_clear:
        return ccw_distance
    return None


def constrained_elevation_error(current: float, target: float, ranges: Iterable[Tuple[float, float]]) -> Optional[float]:
    if point_in_linear_ranges(current, ranges) or point_in_linear_ranges(target, ranges):
        return None
    seg_start = min(float(current), float(target))
    seg_end = max(float(current), float(target))
    for start, end in ranges:
        lo = min(float(start), float(end))
        hi = max(float(start), float(end))
        if _strict_overlap(seg_start, seg_end, lo, hi):
            return None
    return float(current) - float(target)
