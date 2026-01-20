# tracking/radiosources.py
import os
import csv
import re
from typing import Dict, List, Optional, Tuple

def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def _norm(s: str) -> str:
    return (s or "").strip()

def _norm_key(s: str) -> str:
    return (s or "").strip().upper()

_HMS_RE = re.compile(r'^\s*(\d+)[h:\s](\d+)[m:\s]([\d\.]+)s?\s*$', re.IGNORECASE)
_DMS_RE = re.compile(r'^\s*([+\-]?\d+)[°:\s](\d+)[\'\s]([\d\.]+)"?\s*$')

def hms_to_hours(s: str) -> Optional[float]:
    s = _norm(s)
    if not s:
        return None
    m = _HMS_RE.match(s.replace('::', ':'))
    if not m:
        # tente HH:MM:SS.S
        try:
            parts = [float(p) for p in re.split(r'[:\s]+', s) if p!='']
            if len(parts) >= 3:
                h, m_, sec = parts[:3]
                return float(h) + float(m_) / 60.0 + float(sec) / 3600.0
        except Exception:
            return None
        return None
    h, m_, sec = m.groups()
    return float(h) + float(m_) / 60.0 + float(sec) / 3600.0

def dms_to_deg(s: str) -> Optional[float]:
    s = _norm(s)
    if not s:
        return None
    m = _DMS_RE.match(s.replace('::', ':'))
    if not m:
        # tente ±DD:MM:SS.S
        try:
            parts = [p for p in re.split(r'[:\s]+', s) if p!='']
            if len(parts) >= 3:
                sign = -1.0 if parts[0].strip().startswith('-') else 1.0
                d = abs(float(parts[0])); m_ = float(parts[1]); sec = float(parts[2])
                return sign * (d + m_/60.0 + sec/3600.0)
        except Exception:
            return None
        return None
    d, m_, sec = m.groups()
    sign = -1.0 if str(d).strip().startswith('-') else 1.0
    d = abs(float(d))
    return sign * (d + float(m_) / 60.0 + float(sec) / 3600.0)

def to_float(v) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return None

class RadioSourceCatalog:
    """
    Charge des CSV dans src/data/radiosources/*.csv
    Fournit :
      - list_groups() -> ['ATNF', 'RFC', ...] (nom = base du fichier)
      - list_sources(group) -> [ '3C 273', '3C 286', ... ]
      - resolve(name) -> (ra_hours, dec_deg) en cherchant dans tous les groupes
    """
    def __init__(self, base_dir: str, logger=None):
        self.base_dir = os.path.abspath(os.path.expanduser(base_dir))
        _ensure_dir(self.base_dir)
        self.logger = logger
        self._by_group: Dict[str, Dict[str, Tuple[float, float]]] = {}  # group -> name_key -> (ra_h, dec_deg)
        self._loaded = False

    def _detect_cols(self, headers: List[str]) -> Dict[str, Optional[int]]:
        h = [c.strip().lower() for c in headers]
        def idx(*cands):
            for c in cands:
                if c.lower() in h:
                    return h.index(c.lower())
            return None
        return {
            'name': idx('name','object','source'),
            'ra_hms': idx('ra_hms','ra'),
            'ra_deg': idx('ra_deg','radeg','ra_deg_deg'),
            'ra_hours': idx('ra_hours','rah'),
            'dec_dms': idx('dec_dms','dec'),
            'dec_deg': idx('dec_deg','decdeg','dec_deg_deg'),
        }

    def _parse_row(self, row: List[str], cols: Dict[str, Optional[int]]) -> Optional[Tuple[str, float, float]]:
        try:
            name = _norm(row[cols['name']]) if cols['name'] is not None else None
            if not name:
                return None

            ra_h = None
            dec_d = None

            if cols['ra_hours'] is not None:
                ra_h = to_float(row[cols['ra_hours']])
            if ra_h is None and cols['ra_deg'] is not None:
                ra_deg = to_float(row[cols['ra_deg']])
                if ra_deg is not None:
                    ra_h = ra_deg / 15.0
            if ra_h is None and cols['ra_hms'] is not None:
                ra_h = hms_to_hours(row[cols['ra_hms']])

            if cols['dec_deg'] is not None:
                dec_d = to_float(row[cols['dec_deg']])
            if dec_d is None and cols['dec_dms'] is not None:
                dec_d = dms_to_deg(row[cols['dec_dms']])

            if ra_h is None or dec_d is None:
                return None
            return name, float(ra_h), float(dec_d)
        except Exception:
            return None

    def _load_once(self):
        if self._loaded:
            return
        files = [f for f in os.listdir(self.base_dir) if f.lower().endswith('.csv')]
        if not files and self.logger:
            self.logger.warning(f"[RadioSource] aucun CSV trouvé dans {self.base_dir}")
        for fname in files:
            group = os.path.splitext(fname)[0]
            path = os.path.join(self.base_dir, fname)
            table: Dict[str, Tuple[float,float]] = {}
            try:
                with open(path, 'r', newline='', encoding='utf-8') as f:
                    reader = csv.reader(f)
                    headers = next(reader, None)
                    if not headers:
                        continue
                    cols = self._detect_cols(headers)
                    for row in reader:
                        rec = self._parse_row(row, cols)
                        if not rec:
                            continue
                        name, ra_h, dec_d = rec
                        table[_norm_key(name)] = (ra_h, dec_d)
                self._by_group[group] = table
                if self.logger:
                    self.logger.info(f"[RadioSource] loaded {len(table)} sources from {fname}")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"[RadioSource] load failed for {fname}: {e}")
        self._loaded = True

    # -------- API UI --------
    def refresh(self, force: bool = False):
        if force:
            self._loaded = False
            self._by_group.clear()
        self._load_once()

    def list_groups(self) -> List[str]:
        self._load_once()
        return sorted(self._by_group.keys())

    def list_sources(self, group: Optional[str]) -> List[str]:
        self._load_once()
        if not group or group not in self._by_group:
            return []
        names = [name for name in self._by_group[group].keys()]
        return sorted(names)

    def resolve(self, name: str) -> Optional[Tuple[float, float]]:
        """Cherche par nom (insensible à la casse) dans tous les groupes."""
        self._load_once()
        key = _norm_key(name)
        for table in self._by_group.values():
            if key in table:
                return table[key]
        return None
