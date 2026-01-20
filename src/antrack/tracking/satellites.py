# tracking/satellites.py
import os
import re
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional, Iterable, List, Tuple

from skyfield.api import Loader, EarthSatellite

DEFAULT_GROUPS = ["stations", "active", "amateur", "weather"]
CELESTRAK_URL_TMPL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={grp}&FORMAT=tle"

def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def _norm_name(name: str) -> str:
    return (name or "").strip().upper()

class TLERepository:
    """
    - Cache disque dans src/data/tle
    - Un fichier par groupe (filename unique)
    - Refresh périodique
    - Résolution par nom / NORAD
    - Listing par groupe
    """
    def __init__(
        self,
        tle_dir: str,
        groups: Optional[Iterable[str]] = None,
        refresh_hours: float = 6.0,
        logger=None
    ):
        self.tle_dir = os.path.abspath(os.path.expanduser(tle_dir))
        _ensure_dir(self.tle_dir)
        self.loader = Loader(self.tle_dir)
        self.groups = list(groups or DEFAULT_GROUPS)
        self.refresh_delta = timedelta(hours=float(refresh_hours))
        self.logger = logger

        self.by_name: Dict[str, EarthSatellite] = {}
        self.by_norad: Dict[int, EarthSatellite] = {}
        self.by_group: Dict[str, List[EarthSatellite]] = {}  # ← NEW
        self._next_refresh = datetime.min
        self._lock = threading.Lock()

    # ----- config -----
    def set_groups(self, groups: Iterable[str]):
        with self._lock:
            self.groups = list(groups)
            # force refresh on next call
            self._next_refresh = datetime.min

    # ----- refresh & build indexes -----
    def refresh_if_due(self, force: bool = False):
        with self._lock:
            if not force and datetime.utcnow() < self._next_refresh:
                return

            by_name: Dict[str, EarthSatellite] = {}
            by_norad: Dict[int, EarthSatellite] = {}
            by_group: Dict[str, List[EarthSatellite]] = {}
            total = 0
            any_loaded = False

            for grp in self.groups:
                url = CELESTRAK_URL_TMPL.format(grp=grp)
                sats = None
                try:
                    sats = self.loader.tle_file(url, filename=f"celestrak_{grp}.tle", reload=True)
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"[TLE] refresh failed for group={grp}: {e}")
                    try:
                        sats = self.loader.tle_file(url, filename=f"celestrak_{grp}.tle", reload=False)
                        if self.logger:
                            self.logger.info(f"[TLE] fallback cache used for group={grp}")
                    except Exception as e2:
                        if self.logger:
                            self.logger.error(f"[TLE] fallback cache failed for group={grp}: {e2}")
                        sats = []

                total += len(sats)
                any_loaded = any_loaded or bool(sats)
                by_group[grp] = sats
                for s in sats:
                    try:
                        by_name[_norm_name(s.name)] = s
                    except Exception:
                        pass
                    try:
                        satnum = int(s.model.satnum)
                        by_norad[satnum] = s
                    except Exception:
                        pass

            if any_loaded:
                self.by_name = by_name
                self.by_norad = by_norad
                self.by_group = by_group
                self._next_refresh = datetime.utcnow() + self.refresh_delta
                if self.logger:
                    self.logger.info(f"[TLE] loaded groups={self.groups} total={total} cached={len(self.by_name)}")
            else:
                if self.logger:
                    self.logger.error("[TLE] refresh failed for all groups")
                self._next_refresh = datetime.utcnow() + timedelta(hours=1)

    # ----- resolve -----
    def resolve(self, query: str) -> Optional[EarthSatellite]:
        self.refresh_if_due()
        if not query:
            return None

        # NORAD ?
        try:
            norad = int(str(query).strip())
            sat = self.by_norad.get(norad)
            if sat:
                return sat
        except Exception:
            pass

        q = _norm_name(query)

        # exact first
        sat = self.by_name.get(q)
        if sat:
            return sat

        # contains fallback
        pat = re.sub(r"\s+", " ", q)
        for name, sat in self.by_name.items():
            if pat in name:
                return sat

        return None

    # ----- listing -----
    def list_groups(self) -> List[str]:
        self.refresh_if_due()
        # ne retourne que les groupes réellement chargés
        return list(self.by_group.keys())

    def list_satellites(
        self,
        group: Optional[str] = None,
        sort_by: str = "name"
    ) -> List[Tuple[str, int]]:
        """
        Retourne [(name, norad), ...] pour un groupe donné, ou toute la base si group=None.
        """
        self.refresh_if_due()
        sats: List[EarthSatellite] = []
        if group:
            sats = list(self.by_group.get(group, []))
        else:
            # concat de tous les groupes (peut dupliquer des objets identiques selon flux → on déduplique par NORAD)
            seen = set()
            for glist in self.by_group.values():
                for s in glist:
                    try:
                        sid = int(s.model.satnum)
                        if sid in seen:
                            continue
                        seen.add(sid)
                        sats.append(s)
                    except Exception:
                        sats.append(s)

        rows: List[Tuple[str, int]] = []
        for s in sats:
            try:
                rows.append((s.name, int(s.model.satnum)))
            except Exception:
                rows.append((s.name, -1))

        if sort_by == "name":
            rows.sort(key=lambda x: _norm_name(x[0]))
        elif sort_by == "norad":
            rows.sort(key=lambda x: (x[1] if x[1] >= 0 else 9_999_999))
        return rows
