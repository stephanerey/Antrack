"""Helpers for editable SDRplay-style gain tables and LNA attenuation lookup."""

from __future__ import annotations

from typing import Iterable


AUTO_GAIN_LEVELS_DBM = [-100, -90, -80, -70, -60, -50, -40, -30, -20, -10, 0, 10, 20]
AUTO_GAIN_BANDS_MHZ = [
    (0.0, 2.0),
    (2.0, 12.0),
    (12.0, 30.0),
    (30.0, 60.0),
    (60.0, 120.0),
    (120.0, 250.0),
    (250.0, 300.0),
    (300.0, 380.0),
    (380.0, 420.0),
    (420.0, 1000.0),
    (1000.0, 2000.0),
]
AUTO_GAIN_BAND_LABELS = [
    "0 - 2",
    "2 - 12",
    "12 - 30",
    "30 - 60",
    "60 - 120",
    "120 - 250",
    "250 - 300",
    "300 - 380",
    "380 - 420",
    "420 - 1000",
    "1000 - 2000",
]
DEFAULT_AUTO_GAIN_LEVEL_DBM = -60

_DEFAULT_AUTO_GAIN_PROFILES = {
    -100: [(0, 40), (0, 40), (0, 40), (0, 40), (0, 40), (0, 40), (0, 40), (0, 40), (0, 40), (0, 40), (0, 40)],
    -90: [(0, 40), (0, 40), (0, 40), (0, 40), (0, 40), (0, 40), (0, 40), (0, 40), (0, 40), (0, 40), (0, 40)],
    -80: [(0, 40), (0, 40), (0, 40), (0, 40), (0, 40), (0, 40), (0, 40), (0, 40), (0, 40), (0, 40), (0, 40)],
    -70: [(0, 40), (0, 40), (0, 40), (0, 40), (0, 59), (1, 59), (1, 50), (0, 50), (0, 50), (1, 59), (1, 40)],
    -60: [(1, 40), (1, 40), (1, 40), (1, 40), (4, 59), (4, 59), (0, 50), (0, 50), (0, 50), (3, 59), (1, 59)],
    -50: [(2, 59), (2, 59), (2, 59), (2, 59), (6, 59), (6, 59), (1, 59), (1, 59), (1, 59), (6, 59), (3, 59)],
    -40: [(3, 59), (3, 59), (3, 59), (3, 59), (9, 59), (9, 59), (4, 59), (4, 59), (4, 59), (9, 59), (5, 59)],
    -30: [(4, 59), (4, 59), (4, 59), (4, 59), (12, 59), (12, 59), (7, 59), (7, 59), (7, 59), (12, 59), (8, 59)],
    -20: [(7, 59), (7, 59), (7, 59), (7, 59), (16, 59), (16, 59), (10, 59), (10, 59), (10, 59), (15, 59), (12, 59)],
    -10: [(10, 59), (10, 59), (10, 59), (10, 59), (19, 59), (19, 59), (13, 59), (13, 59), (13, 59), (19, 59), (15, 59)],
    0: [(13, 59), (13, 59), (13, 59), (13, 59), (22, 59), (22, 59), (16, 59), (16, 59), (16, 59), (20, 59), (18, 59)],
    10: [(15, 59), (15, 59), (15, 59), (15, 59), (25, 59), (25, 59), (20, 59), (20, 59), (20, 59), (20, 59), (18, 59)],
    20: [(17, 59), (17, 59), (17, 59), (17, 59), (26, 59), (26, 59), (23, 59), (23, 59), (23, 59), (20, 59), (18, 59)],
}

_RSPDX_LNA_ATTENUATION_TABLES = [
    ("0-2", 0.0, 2.0, [0, 3, 6, 9, 12, 15, 18, 21, 24, 25, 27, 30, 33, 36, 39, 42, 45, 48, 51, 54, 57, 60]),
    ("0-12", 0.0, 12.0, [0, 3, 6, 9, 12, 15, 24, 27, 30, 33, 36, 39, 42, 45, 48, 51, 54, 57, 60]),
    ("12-50", 12.0, 50.0, [0, 3, 6, 9, 12, 15, 18, 24, 27, 30, 33, 36, 39, 42, 45, 48, 51, 54, 57, 60]),
    ("50-60", 50.0, 60.0, [0, 3, 6, 9, 12, 20, 23, 26, 29, 32, 35, 38, 44, 47, 50, 53, 56, 59, 62, 65, 68, 71, 74, 77, 80]),
    ("60-250", 60.0, 250.0, [0, 3, 6, 9, 12, 15, 24, 27, 30, 33, 36, 39, 42, 45, 48, 51, 54, 57, 60, 63, 66, 69, 72, 75, 78, 81, 84]),
    ("250-420", 250.0, 420.0, [0, 3, 6, 9, 12, 15, 18, 24, 27, 30, 33, 36, 39, 42, 45, 48, 51, 54, 57, 60, 63, 66, 69, 72, 75, 78, 81, 84]),
    ("420-1000", 420.0, 1000.0, [0, 7, 10, 13, 16, 19, 22, 25, 31, 34, 37, 40, 43, 46, 49, 52, 55, 58, 61, 64, 67]),
    ("1000-2000", 1000.0, 2000.0, [0, 5, 8, 11, 14, 17, 20, 32, 35, 38, 41, 44, 47, 50, 53, 56, 59, 62, 65]),
]


def band_label_for_index(index: int) -> str:
    return AUTO_GAIN_BAND_LABELS[int(index)]


def find_band_index(freq_hz: float) -> int:
    freq_mhz = float(freq_hz) / 1_000_000.0
    for index, (low_mhz, high_mhz) in enumerate(AUTO_GAIN_BANDS_MHZ):
        if low_mhz <= freq_mhz < high_mhz:
            return index
    return len(AUTO_GAIN_BANDS_MHZ) - 1


def band_label_for_frequency(freq_hz: float) -> str:
    return band_label_for_index(find_band_index(freq_hz))


def _lna_attn_table_for_frequency(freq_mhz: float) -> list[int]:
    for _, low_mhz, high_mhz, values in _RSPDX_LNA_ATTENUATION_TABLES:
        if low_mhz <= freq_mhz < high_mhz:
            return values
    return _RSPDX_LNA_ATTENUATION_TABLES[-1][3]


def max_lna_state_for_frequency(freq_hz: float) -> int:
    table = _lna_attn_table_for_frequency(float(freq_hz) / 1_000_000.0)
    return len(table) - 1


def clamp_lna_state(freq_hz: float, lna_state: int) -> int:
    return int(max(0, min(int(lna_state), max_lna_state_for_frequency(freq_hz))))


def lna_attenuation_db(freq_hz: float, lna_state: int) -> int:
    table = _lna_attn_table_for_frequency(float(freq_hz) / 1_000_000.0)
    index = int(max(0, min(int(lna_state), len(table) - 1)))
    return int(table[index])


def build_default_auto_gain_profiles() -> dict[int, list[tuple[int, int]]]:
    return {
        int(level_dbm): [(int(lna_state), int(if_gain)) for lna_state, if_gain in band_pairs]
        for level_dbm, band_pairs in _DEFAULT_AUTO_GAIN_PROFILES.items()
    }


def pair_text(pair: Iterable[int]) -> str:
    lna_state, if_gain = [int(value) for value in pair]
    return f"{lna_state}/{if_gain}"


def parse_pair_text(text: str) -> tuple[int, int] | None:
    raw = str(text).strip().replace("|", "/").replace("\\", "/")
    if not raw:
        return None
    parts = [chunk.strip() for chunk in raw.split("/") if chunk.strip()]
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None

