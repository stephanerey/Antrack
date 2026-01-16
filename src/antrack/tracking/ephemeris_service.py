from PyQt5.QtCore import QObject, pyqtSignal
from skyfield.api import load as sf_load, Star, Angle
from skyfield.data import hipparcos
from typing import Dict, Optional, List, Tuple
import time
import numpy as np
import os
import math

from antrack.tracking.tracking import convert_float_to_hms, decimal_degrees_to_dms
from antrack.tracking.satellites import TLERepository, DEFAULT_GROUPS as DEFAULT_TLE_GROUPS
from antrack.tracking.radiosources import RadioSourceCatalog
from antrack.tracking.spacecrafts import SpacecraftRepo

class EphemerisService(QObject):
    pose_updated = pyqtSignal(str, dict)  # key, payload

    def __init__(self, thread_manager, observer, planets, logger=None, parent=None,
                 tle_dir: Optional[str] = None, tle_groups: Optional[List[str]] = None,
                 tle_refresh_hours: float = 6.0,
                 radiosrc_dir: Optional[str] = None,
                 spacecraft_dir: Optional[str] = None):
        super().__init__(parent)
        self.thread_manager = thread_manager
        self.observer = observer
        self.planets = planets
        self.logger = logger

        self._workers: Dict[str, bool] = {}
        self._targets: Dict[str, Dict] = {}

        self._hip_df = None

        # caches objets résolus
        self._star_cache: Dict[str, Star] = {}
        self._radio_cache: Dict[str, Star] = {}
        # cache de passes par clé (throttling)
        self._pass_cache: Dict[str, Dict[str, object]] = {}  # key -> {'last_tt': float, 'payload': dict}

        # --- TLE repo ---
        if not tle_dir:
            tle_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'tle'))
        try:
            self._tle_repo = TLERepository(
                tle_dir=tle_dir,
                groups=tle_groups or DEFAULT_TLE_GROUPS,
                refresh_hours=tle_refresh_hours,
                logger=self.logger
            )
        except Exception as e:
            self._tle_repo = None
            if self.logger:
                self.logger.error(f"[Ephemeris] TLERepository init failed: {e}")

        # --- Radio sources catalog ---
        if not radiosrc_dir:
            radiosrc_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'radiosources'))
        try:
            self._rs_catalog = RadioSourceCatalog(radiosrc_dir, logger=self.logger)
            self._rs_catalog.refresh(force=False)
        except Exception as e:
            self._rs_catalog = None
            if self.logger:
                self.logger.error(f"[Ephemeris] RadioSourceCatalog init failed: {e}")

        # --- Spacecraft (SPICE) ---
        if not spacecraft_dir:
            spacecraft_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'spacecrafts'))
        try:
            self._sc_repo = SpacecraftRepo(spacecraft_dir, logger=self.logger)
        except Exception as e:
            self._sc_repo = None
            if self.logger:
                self.logger.error(f"[Ephemeris] SpacecraftRepo init failed: {e}")

    # ---------- API publique ----------

    def start_object(self, key: str, obj_type: str, name: str, interval: float = 0.1):
        self._targets[key] = {'obj_type': obj_type, 'name': name, 'interval': float(interval)}
        if self._workers.get(key, False):
            if self.logger:
                self.logger.info(f"[Ephemeris] target updated key='{key}' type='{obj_type}' name='{name}'")
            return
        self._workers[key] = True

        def loop():
            self._object_loop(key)

        self.thread_manager.start_thread(f"Ephemeris:{key}", loop)
        if self.logger:
            self.logger.info(f"[Ephemeris] started key='{key}' type='{obj_type}' name='{name}' interval={interval}s")

    def stop_object(self, key: str):
        try:
            self._workers[key] = False
        except Exception:
            pass
        try:
            self.thread_manager.stop_thread(f"Ephemeris:{key}")
        except Exception:
            pass

    def stop_all(self):
        for key in list(self._workers.keys()):
            self.stop_object(key)

    # ---------- API TLE ----------
    def tle_refresh(self, force: bool = False):
        try:
            if self._tle_repo:
                self._tle_repo.refresh_if_due(force=force)
        except Exception:
            pass

    def tle_set_groups(self, groups: List[str]):
        try:
            if self._tle_repo:
                self._tle_repo.set_groups(groups)
        except Exception:
            pass

    def tle_groups(self) -> List[str]:
        try:
            if self._tle_repo:
                return self._tle_repo.list_groups()
        except Exception:
            pass
        return []

    def tle_list_satellites(self, group: Optional[str] = None) -> List[tuple]:
        try:
            if self._tle_repo:
                return self._tle_repo.list_satellites(group=group, sort_by="name")
        except Exception:
            pass
        return []

    # ---------- API RadioSource ----------
    def rs_refresh(self, force: bool = False):
        try:
            if self._rs_catalog:
                self._rs_catalog.refresh(force=force)
        except Exception:
            pass

    def rs_groups(self) -> List[str]:
        try:
            if self._rs_catalog:
                return self._rs_catalog.list_groups()
        except Exception:
            pass
        return []

    def rs_list_sources(self, group: Optional[str]) -> List[str]:
        try:
            if self._rs_catalog:
                return self._rs_catalog.list_sources(group)
        except Exception:
            pass
        return []

    # ---------- API Spacecraft ----------
    def sc_list_spacecrafts(self) -> List[str]:
        try:
            if self._sc_repo:
                return self._sc_repo.list_spacecrafts()
        except Exception:
            pass
        return []

    # ---------- Boucle worker ----------
    def _object_loop(self, key: str):
        last_type = None
        while self._workers.get(key, False):
            try:
                sel = self._targets.get(key)
                if not sel or not self.observer or not self.planets:
                    time.sleep(0.1)
                    continue

                obj_type = sel.get('obj_type') or ""
                name = sel.get('name') or ""
                interval = float(sel.get('interval') or 0.1)

                if obj_type != last_type:
                    if obj_type == "Star" and self._hip_df is None:
                        try:
                            with sf_load.open(hipparcos.URL) as f:
                                self._hip_df = hipparcos.load_dataframe(f)
                        except Exception as e:
                            if self.logger:
                                self.logger.error(f"[Ephemeris] Hipparcos load failed: {e}")
                            self._hip_df = None
                    last_type = obj_type

                t_now = self.observer.timescale.now()

                payload = self._compute_payload(obj_type, name, t_now)
                payload['name'] = name

                # --- throttling du calcul de passes ---
                cache = self._pass_cache.setdefault(key, {'last_tt': 0.0, 'payload': self._empty_pass_info()})
                now_tt = float(t_now.tt)
                if obj_type == "Artificial Satellite":
                    min_period_s = 1.0
                elif obj_type == "Spacecraft":
                    min_period_s = 3.0
                else:
                    min_period_s = 3.0

                if (now_tt - float(cache['last_tt'])) * 86400.0 >= min_period_s:
                    cache['payload'] = self._compute_pass_info(obj_type, name, t_now)
                    cache['last_tt'] = now_tt

                payload.update(cache['payload'])

                try:
                    self.pose_updated.emit(key, payload)
                except Exception:
                    pass

            except Exception as e:
                if self.logger:
                    self.logger.error(f"[Ephemeris] loop error (key={key}): {e}")

            time.sleep(sel.get('interval', 0.1) if sel else 0.1)

    # ---------- Résolutions ----------
    def _resolve_ss_body(self, raw_name: str):
        if not raw_name:
            return None
        n = raw_name.strip().lower()
        if n in ('sun', 'moon', 'earth'):
            candidates = [n]
        else:
            base = n.replace(' barycenter', '')
            if base in ('jupiter', 'saturn', 'uranus', 'neptune'):
                candidates = [f'{base} barycenter', base]
            else:
                candidates = [base, f'{base} barycenter']
        if n.endswith(' barycenter'):
            base = n[:-11]
            if base not in candidates:
                candidates.append(base)
        for key in candidates:
            try:
                return self.planets[key]
            except Exception:
                continue
        if self.logger:
            self.logger.debug(f"_resolve_ss_body: '{raw_name}' introuvable")
        return None

    def _resolve_star(self, name: str) -> Optional[Star]:
        if not name:
            return None
        if name in self._star_cache:
            return self._star_cache[name]
        hip_map = {"Polaris": 11767, "Sirius": 32349, "Betelgeuse": 27989}
        hip_id = hip_map.get(name, 11767)
        if self._hip_df is None:
            return None
        try:
            star = Star.from_dataframe(self._hip_df.loc[hip_id])
            self._star_cache[name] = star
            return star
        except Exception:
            return None

    def _resolve_sat(self, want: str):
        if not self._tle_repo or not want:
            return None
        try:
            return self._tle_repo.resolve(want)
        except Exception as e:
            if self.logger:
                self.logger.error(f"[Ephemeris] _resolve_sat error for '{want}': {e}")
            return None

    def _resolve_radio_source(self, name: str) -> Optional[Star]:
        if not name:
            return None
        if name in self._radio_cache:
            return self._radio_cache[name]
        if not self._rs_catalog:
            return None
        try:
            rec = self._rs_catalog.resolve(name)
            if not rec:
                return None
            ra_h, dec_d = rec
            star = Star(ra=Angle(hours=ra_h), dec=Angle(degrees=dec_d))
            self._radio_cache[name] = star
            return star
        except Exception as e:
            if self.logger:
                self.logger.error(f"[Ephemeris] _resolve_radio_source error for '{name}': {e}")
            return None

    # ---------- Calcul de payload ----------
    def _vantage(self):
        try:
            return self.planets['earth'] + self.observer.topocentric
        except Exception:
            return None

    def _compute_payload(self, obj_type: str, name: str, t) -> dict:
        az = el = dist_km = dist_au = None
        ra_hms = dec_dms = None
        try:
            if obj_type == "Solar System":
                vantage = self._vantage()
                target = self._resolve_ss_body(name)
                if target is not None and vantage is not None:
                    app = vantage.at(t).observe(target).apparent()
                    alt, azm, dist = app.altaz()
                    ra, dec, dist2 = app.radec()
                    az = float(azm.degrees)
                    el = float(alt.degrees)
                    dist_km = float(getattr(dist, 'km', None)) if hasattr(dist, "km") else None
                    ra_hms = convert_float_to_hms(float(getattr(ra, "hours", 0.0)))
                    dec_dms = decimal_degrees_to_dms(float(getattr(dec, "degrees", 0.0)))
                    dist_au = float(getattr(dist2, "au", 0.0))

            elif obj_type == "Star":
                vantage = self._vantage()
                star = self._resolve_star(name)
                if star is not None and vantage is not None:
                    app = vantage.at(t).observe(star).apparent()
                    alt, azm, dist = app.altaz()
                    ra, dec, dist2 = app.radec()
                    az = float(azm.degrees)
                    el = float(alt.degrees)
                    dist_km = float(getattr(dist, 'km', None)) if hasattr(dist, "km") else None
                    ra_hms = convert_float_to_hms(float(getattr(ra, "hours", 0.0)))
                    dec_dms = decimal_degrees_to_dms(float(getattr(dec, "degrees", 0.0)))
                    dist_au = float(getattr(dist2, "au", 0.0))

            elif obj_type == "Artificial Satellite":
                obs = self.observer.topocentric
                sat = self._resolve_sat(name)
                if sat is not None and obs is not None:
                    topo = (sat - obs).at(t)
                    alt, azm, dist = topo.altaz()
                    ra, dec, dist2 = topo.radec()
                    az = float(azm.degrees)
                    el = float(alt.degrees)
                    dist_km = float(getattr(dist, 'km', None)) if hasattr(dist, "km") else None
                    ra_hms = convert_float_to_hms(float(getattr(ra, "hours", 0.0)))
                    dec_dms = decimal_degrees_to_dms(float(getattr(dec, "degrees", 0.0)))
                    dist_au = float(getattr(dist2, "au", 0.0))

            elif obj_type == "Radio Source":
                vantage = self._vantage()
                star = self._resolve_radio_source(name)
                if star is not None and vantage is not None:
                    app = vantage.at(t).observe(star).apparent()
                    alt, azm, dist = app.altaz()
                    ra, dec, dist2 = app.radec()
                    az = float(azm.degrees)
                    el = float(alt.degrees)
                    dist_km = float(getattr(dist, 'km', None)) if hasattr(dist, "km") else None
                    ra_hms = convert_float_to_hms(float(getattr(ra, "hours", 0.0)))
                    dec_dms = decimal_degrees_to_dms(float(getattr(dec, "degrees", 0.0)))
                    dist_au = float(getattr(dist2, "au", 0.0))

            elif obj_type == "Spacecraft":
                # 1) position J2000 géocentrique via SPICE
                if self._sc_repo is not None and name:
                    try:
                        # Convertit Skyfield Time -> ET (J2000 seconds)
                        dt = t.utc_datetime()
                        utc_str = dt.strftime("%Y-%m-%dT%H:%M:%S")
                        import spiceypy as sp
                        et = sp.utc2et(utc_str)
                        x, y, z = self._sc_repo.position_earth_centered(name, et)
                        if (x is not None) and (y is not None) and (z is not None):
                            # 2) direction -> RA/DEC (J2000)
                            r = math.sqrt(x*x + y*y + z*z)
                            if r > 0.0:
                                ra_rad = math.atan2(y, x)
                                if ra_rad < 0.0:
                                    ra_rad += 2.0 * math.pi
                                dec_rad = math.asin(z / r)
                                # distance géocentrique (km)
                                dist_km = r
                                # 3) projeter depuis l'observateur topo via Skyfield
                                star = Star(ra=Angle(radians=ra_rad), dec=Angle(radians=dec_rad))
                                vantage = self._vantage()
                                if vantage is not None:
                                    app = vantage.at(t).observe(star).apparent()
                                    alt, azm, dist = app.altaz()
                                    ra, dec, dist2 = app.radec()
                                    az = float(azm.degrees)
                                    el = float(alt.degrees)
                                    ra_hms = convert_float_to_hms(float(getattr(ra, "hours", 0.0)))
                                    dec_dms = decimal_degrees_to_dms(float(getattr(dec, "degrees", 0.0)))
                                    dist_au = float(getattr(dist2, "au", 0.0))
                    except Exception as e:
                        if self.logger:
                            self.logger.error(f"[Ephemeris] Spacecraft payload error '{name}': {e}")

        except Exception as e:
            if self.logger:
                self.logger.error(f"[Ephemeris] _compute_payload error for '{name}': {e}")

        return {
            'az': az, 'el': el,
            'dist_km': dist_km,
            'ra_hms': ra_hms,
            'dec_dms': dec_dms,
            'dist_au': dist_au,
            'el_now_deg': el,
        }

    # ---------- Passes ----------
    def _compute_pass_info(self, obj_type: str, name: str, t_now):
        """Toujours retourne un dict, sans lever d'exception."""
        try:
            ts = self.observer.timescale
            elev_thresh = 0.0

            if obj_type == "Artificial Satellite":
                hours_fwd, step_s, lookback_s = 12.0, 10.0, 3600.0
            elif obj_type == "Spacecraft":
                hours_fwd, step_s, lookback_s = 24.0, 120.0, 6 * 3600.0
            elif obj_type == "Solar System":
                hours_fwd, step_s, lookback_s = 36.0, 120.0, 12 * 3600.0
            else:  # Star / Radio Source
                hours_fwd, step_s, lookback_s = 24.0, 120.0, 12 * 3600.0

            times = self._build_time_array(ts, t_now, hours_fwd, step_s, lookback_s)

            alt_deg = self._altitude_series(obj_type, name, times)
            if not alt_deg:
                return self._empty_pass_info()

            tt = np.atleast_1d(np.asarray(times.tt, dtype=float))
            now_tt = float(t_now.tt)
            now_idx = int(np.clip(np.searchsorted(tt, now_tt, side='right') - 1, 0, len(tt) - 1))

            above = np.array([(a is not None) and (a >= elev_thresh) for a in alt_deg], dtype=bool)
            visible_now = bool(above[now_idx])
            el_now = float(alt_deg[now_idx]) if alt_deg[now_idx] is not None else None

            if above.all():
                max_i = self._argmax_safe(alt_deg, 0, len(alt_deg) - 1)
                max_el = float(alt_deg[max_i]) if max_i is not None else None
                max_time = self._time_from_tt(tt[max_i]) if max_i is not None else None
                return {
                    'visible_now': True, 'el_now_deg': el_now,
                    'aos_utc': None, 'los_utc': None, 'dur_str': None,
                    'max_el_deg': max_el,
                    'max_el_time_utc': self._fmt_time_utc(max_time) if max_time else None,
                    # numériques
                    'aos_tt': None,
                    'los_tt': None,
                    'max_tt': float(max_time.tt) if max_time is not None else None,
                }

            if (~above).all():
                out = self._empty_pass_info()
                out.update({'el_now_deg': el_now, 'visible_now': False})
                return out

            rises = np.where((~above[:-1]) & (above[1:]))[0] + 1
            sets_  = np.where((above[:-1]) & (~above[1:]))[0] + 1

            aos_time = los_time = None
            dur_s = None
            max_el = None
            max_time = None

            if visible_now:
                rise_idx = int(rises[rises <= now_idx][-1]) if rises.size and (rises <= now_idx).any() else None
                set_idx  = int(sets_[sets_ > now_idx][0])    if sets_.size and (sets_ > now_idx).any() else None
                if rise_idx is not None:
                    aos_time = self._interpolate_crossing(times, alt_deg, rise_idx - 1, rise_idx, elev_thresh)
                if set_idx is not None:
                    los_time = self._interpolate_crossing(times, alt_deg, set_idx - 1, set_idx, elev_thresh)
                if (aos_time is not None) and (los_time is not None):
                    dur_s = (los_time.tt - aos_time.tt) * 86400.0
                    max_i = self._argmax_safe(alt_deg,
                                              rise_idx if rise_idx is not None else 0,
                                              set_idx if set_idx is not None else now_idx)
                    if max_i is not None and alt_deg[max_i] is not None:
                        max_el = float(alt_deg[max_i])
                        max_time = self._time_from_tt(tt[max_i])
            else:
                if rises.size and (rises > now_idx).any():
                    rise_idx = int(rises[rises > now_idx][0])
                    aos_time = self._interpolate_crossing(times, alt_deg, rise_idx - 1, rise_idx, elev_thresh)
                    if sets_.size and (sets_ > rise_idx).any():
                        set_idx = int(sets_[sets_ > rise_idx][0])
                        los_time = self._interpolate_crossing(times, alt_deg, set_idx - 1, set_idx, elev_thresh)
                        dur_s = (los_time.tt - aos_time.tt) * 86400.0
                        max_i = self._argmax_safe(alt_deg, rise_idx, set_idx)
                        if max_i is not None and alt_deg[max_i] is not None:
                            max_el = float(alt_deg[max_i])
                            max_time = self._time_from_tt(tt[max_i])

            return {
                'visible_now': visible_now,
                'el_now_deg': el_now,
                'aos_utc': self._fmt_time_utc(aos_time) if (aos_time is not None) else None,
                'los_utc': self._fmt_time_utc(los_time) if (los_time is not None) else None,
                'dur_str': self._fmt_duration(dur_s) if (dur_s is not None) else None,
                'max_el_deg': max_el,
                'max_el_time_utc': self._fmt_time_utc(max_time) if (max_time is not None) else None,
                # numériques
                'aos_tt': float(aos_time.tt) if (aos_time is not None) else None,
                'los_tt': float(los_time.tt) if (los_time is not None) else None,
                'max_tt': float(max_time.tt) if (max_time is not None) else None,
            }

        except Exception as e:
            if self.logger:
                self.logger.debug(f"[Ephemeris] pass-info fallback for {name}: {e}")
            el_now = self._altitude_now(obj_type, name, t_now)
            return {
                'visible_now': (el_now is not None and el_now >= 0.0),
                'el_now_deg': el_now,
                'aos_utc': None, 'los_utc': None, 'dur_str': None,
                'max_el_deg': None, 'max_el_time_utc': None,
                # numériques
                'aos_tt': None, 'los_tt': None, 'max_tt': None,
            }

    # ---------- Helpers altitude ----------
    def _ensure_vector_time(self, times):
        try:
            tt = np.atleast_1d(np.array(times.tt, dtype=float))
            if tt.size >= 2:
                return times
            ts = self.observer.timescale
            base = float(tt.reshape(()))
            return ts.tt_jd(np.array([base, base + 1.0 / 86400.0], dtype=float))  # +1s
        except Exception:
            return times

    def _altitude_series(self, obj_type: str, name: str, times):
        times = self._ensure_vector_time(times)
        n = int(np.size(getattr(times, 'tt', [])))
        vantage = self._vantage()

        try:
            if obj_type == "Artificial Satellite":
                sat = self._resolve_sat(name)
                if sat is not None and self.observer.topocentric is not None:
                    topo = (sat - self.observer.topocentric).at(times)
                    alt, _, _ = topo.altaz()
                    vals = np.atleast_1d(alt.degrees).astype(float)
                    return self._normalize_series(vals.tolist(), n)

            elif obj_type == "Solar System":
                target = self._resolve_ss_body(name)
                if target is not None and vantage is not None:
                    app = vantage.at(times).observe(target).apparent()
                    alt, _, _ = app.altaz()
                    vals = np.atleast_1d(alt.degrees).astype(float)
                    return self._normalize_series(vals.tolist(), n)

            elif obj_type == "Star":
                star = self._resolve_star(name)
                if star is not None and vantage is not None:
                    app = vantage.at(times).observe(star).apparent()
                    alt, _, _ = app.altaz()
                    vals = np.atleast_1d(alt.degrees).astype(float)
                    return self._normalize_series(vals.tolist(), n)

            elif obj_type == "Radio Source":
                star = self._resolve_radio_source(name)
                if star is not None and vantage is not None:
                    app = vantage.at(times).observe(star).apparent()
                    alt, _, _ = app.altaz()
                    vals = np.atleast_1d(alt.degrees).astype(float)
                    return self._normalize_series(vals.tolist(), n)

            elif obj_type == "Spacecraft":
                # Pas de vectorisation SPICE ici → on échantillonne scalaire proprement
                tt = np.atleast_1d(np.array(times.tt, dtype=float))
                out: List[Optional[float]] = []
                try:
                    import spiceypy as sp
                except Exception:
                    return [None] * n
                for jd in tt:
                    try:
                        t = self._time_from_tt(jd)
                        dt = t.utc_datetime()
                        utc_str = dt.strftime("%Y-%m-%dT%H:%M:%S")
                        et = sp.utc2et(utc_str)
                        x, y, z = self._sc_repo.position_earth_centered(name, et) if self._sc_repo else (None, None, None)
                        if (x is None) or (y is None) or (z is None) or vantage is None:
                            out.append(None); continue
                        r = math.sqrt(x*x + y*y + z*z)
                        if r <= 0.0:
                            out.append(None); continue
                        ra_rad = math.atan2(y, x)
                        if ra_rad < 0.0:
                            ra_rad += 2.0 * math.pi
                        dec_rad = math.asin(z / r)
                        star = Star(ra=Angle(radians=ra_rad), dec=Angle(radians=dec_rad))
                        app = vantage.at(t).observe(star).apparent()
                        alt, _, _ = app.altaz()
                        out.append(float(alt.degrees))
                    except Exception:
                        out.append(None)
                return self._normalize_series(out, n)

        except Exception as e:
            if self.logger:
                self.logger.debug(f"[Ephemeris] vector alt series failed ({obj_type} {name}): {e}")

        return self._altitude_series_scalar(obj_type, name, times, n)

    def _altitude_series_scalar(self, obj_type: str, name: str, times, n_expected: int):
        tt = np.atleast_1d(np.array(times.tt, dtype=float))
        out: List[Optional[float]] = []
        for jd in tt:
            t = self._time_from_tt(jd)
            alt_deg: Optional[float] = None
            try:
                if obj_type == "Artificial Satellite":
                    sat = self._resolve_sat(name)
                    if sat is not None and self.observer.topocentric is not None:
                        topo = (sat - self.observer.topocentric).at(t)
                        alt, _, _ = topo.altaz()
                        alt_deg = float(alt.degrees)
                elif obj_type == "Star":
                    vantage = self._vantage()
                    star = self._resolve_star(name)
                    if star is not None and vantage is not None:
                        app = vantage.at(t).observe(star).apparent()
                        alt, _, _ = app.altaz()
                        alt_deg = float(alt.degrees)
                elif obj_type == "Solar System":
                    vantage = self._vantage()
                    target = self._resolve_ss_body(name)
                    if target is not None and vantage is not None:
                        app = vantage.at(t).observe(target).apparent()
                        alt, _, _ = app.altaz()
                        alt_deg = float(alt.degrees)
                elif obj_type == "Radio Source":
                    vantage = self._vantage()
                    star = self._resolve_radio_source(name)
                    if star is not None and vantage is not None:
                        app = vantage.at(t).observe(star).apparent()
                        alt, _, _ = app.altaz()
                        alt_deg = float(alt.degrees)
                elif obj_type == "Spacecraft":
                    # Fallback déjà couvert dans la voie “scalaire” ci-dessus via boucle
                    pass
            except Exception:
                alt_deg = None
            out.append(alt_deg)

        return self._normalize_series(out, n_expected)

    def _altitude_now(self, obj_type: str, name: str, t):
        try:
            if obj_type == "Artificial Satellite":
                sat = self._resolve_sat(name)
                if sat is not None and self.observer.topocentric is not None:
                    topo = (sat - self.observer.topocentric).at(t)
                    alt, _, _ = topo.altaz()
                    return float(alt.degrees)
            elif obj_type == "Star":
                vantage = self._vantage()
                star = self._resolve_star(name)
                if star is not None and vantage is not None:
                    app = vantage.at(t).observe(star).apparent()
                    alt, _, _ = app.altaz()
                    return float(alt.degrees)
            elif obj_type == "Solar System":
                vantage = self._vantage()
                target = self._resolve_ss_body(name)
                if target is not None and vantage is not None:
                    app = vantage.at(t).observe(target).apparent()
                    alt, _, _ = app.altaz()
                    return float(alt.degrees)
            elif obj_type == "Radio Source":
                vantage = self._vantage()
                star = self._resolve_radio_source(name)
                if star is not None and vantage is not None:
                    app = vantage.at(t).observe(star).apparent()
                    alt, _, _ = app.altaz()
                    return float(alt.degrees)
            elif obj_type == "Spacecraft":
                import spiceypy as sp
                dt = t.utc_datetime()
                utc_str = dt.strftime("%Y-%m-%dT%H:%M:%S")
                et = sp.utc2et(utc_str)
                x, y, z = self._sc_repo.position_earth_centered(name, et) if self._sc_repo else (None, None, None)
                if (x is None) or (y is None) or (z is None):
                    return None
                r = math.sqrt(x*x + y*y + z*z)
                if r <= 0.0:
                    return None
                ra_rad = math.atan2(y, x);  ra_rad = ra_rad + (2.0*math.pi if ra_rad < 0.0 else 0.0)
                dec_rad = math.asin(z / r)
                star = Star(ra=Angle(radians=ra_rad), dec=Angle(radians=dec_rad))
                vantage = self._vantage()
                if vantage is None:
                    return None
                app = vantage.at(t).observe(star).apparent()
                alt, _, _ = app.altaz()
                return float(alt.degrees)
        except Exception:
            return None
        return None

    # ---------- NEW: séries AZ/EL vectorisées ----------
    def _az_el_series(self, obj_type: str, name: str, times):
        """
        Séries vectorisées AZ/EL (en degrés). Retourne (az_list, el_list) de même longueur que times.
        Utilise les mêmes résolutions que _altitude_series. Fallback scalaire si besoin.
        """
        times = self._ensure_vector_time(times)
        n = int(np.size(getattr(times, 'tt', [])))
        vantage = self._vantage()

        def norm2(a, b, n):
            a = self._normalize_series(a, n)
            b = self._normalize_series(b, n)
            return a, b

        try:
            if obj_type == "Artificial Satellite":
                sat = self._resolve_sat(name)
                if sat is not None and self.observer.topocentric is not None:
                    topo = (sat - self.observer.topocentric).at(times)
                    alt, azm, _ = topo.altaz()
                    az = np.atleast_1d(azm.degrees).astype(float).tolist()
                    el = np.atleast_1d(alt.degrees).astype(float).tolist()
                    return norm2(az, el, n)

            elif obj_type == "Solar System":
                target = self._resolve_ss_body(name)
                if target is not None and vantage is not None:
                    app = vantage.at(times).observe(target).apparent()
                    alt, azm, _ = app.altaz()
                    az = np.atleast_1d(azm.degrees).astype(float).tolist()
                    el = np.atleast_1d(alt.degrees).astype(float).tolist()
                    return norm2(az, el, n)

            elif obj_type == "Star":
                star = self._resolve_star(name)
                if star is not None and vantage is not None:
                    app = vantage.at(times).observe(star).apparent()
                    alt, azm, _ = app.altaz()
                    az = np.atleast_1d(azm.degrees).astype(float).tolist()
                    el = np.atleast_1d(alt.degrees).astype(float).tolist()
                    return norm2(az, el, n)

            elif obj_type == "Radio Source":
                star = self._resolve_radio_source(name)
                if star is not None and vantage is not None:
                    app = vantage.at(times).observe(star).apparent()
                    alt, azm, _ = app.altaz()
                    az = np.atleast_1d(azm.degrees).astype(float).tolist()
                    el = np.atleast_1d(alt.degrees).astype(float).tolist()
                    return norm2(az, el, n)

            elif obj_type == "Spacecraft":
                # Voie scalaire (SPICE non vectorisé ici)
                tt = np.atleast_1d(np.array(times.tt, dtype=float))
                AZ, EL = [], []
                try:
                    import spiceypy as sp
                except Exception:
                    return self._normalize_series([None]*n, n), self._normalize_series([None]*n, n)
                for jd in tt:
                    t = self._time_from_tt(jd)
                    try:
                        dt = t.utc_datetime()
                        utc_str = dt.strftime("%Y-%m-%dT%H:%M:%S")
                        et = sp.utc2et(utc_str)
                        x, y, z = self._sc_repo.position_earth_centered(name, et) if self._sc_repo else (None, None, None)
                        if (x is None) or (y is None) or (z is None) or vantage is None:
                            AZ.append(None); EL.append(None); continue
                        r = math.sqrt(x*x + y*y + z*z)
                        if r <= 0.0:
                            AZ.append(None); EL.append(None); continue
                        ra_rad = math.atan2(y, x);  ra_rad = ra_rad + (2.0*math.pi if ra_rad < 0.0 else 0.0)
                        dec_rad = math.asin(z / r)
                        star = Star(ra=Angle(radians=ra_rad), dec=Angle(radians=dec_rad))
                        app = vantage.at(t).observe(star).apparent()
                        alt, azm, _ = app.altaz()
                        AZ.append(float(azm.degrees)); EL.append(float(alt.degrees))
                    except Exception:
                        AZ.append(None); EL.append(None)
                return norm2(AZ, EL, n)

        except Exception as e:
            if self.logger:
                self.logger.debug(f"[Ephemeris] vector az/el failed ({obj_type} {name}): {e}")

        # Fallback scalaire générique
        tt = np.atleast_1d(np.array(times.tt, dtype=float))
        AZ, EL = [], []
        for jd in tt:
            t = self._time_from_tt(jd)
            az = el = None
            try:
                if obj_type == "Artificial Satellite":
                    sat = self._resolve_sat(name)
                    if sat is not None and self.observer.topocentric is not None:
                        topo = (sat - self.observer.topocentric).at(t)
                        alt, azm, _ = topo.altaz()
                        az = float(azm.degrees); el = float(alt.degrees)
                elif obj_type in ("Solar System", "Star", "Radio Source"):
                    vantage = self._vantage()
                    target = None
                    if obj_type == "Solar System":
                        target = self._resolve_ss_body(name)
                    elif obj_type == "Star":
                        target = self._resolve_star(name)
                    else:
                        target = self._resolve_radio_source(name)
                    if target is not None and vantage is not None:
                        app = vantage.at(t).observe(target).apparent()
                        alt, azm, _ = app.altaz()
                        az = float(azm.degrees); el = float(alt.degrees)
            except Exception:
                pass
            AZ.append(az); EL.append(el)
        return self._normalize_series(AZ, len(tt)), self._normalize_series(EL, len(tt))

    # ---------- NEW: construction de la trace AOS→LOS ----------
    def build_pass_track(self, obj_type: str, name: str, t_now, step_s: float = 1.0) -> Dict[str, object]:
        """
        Construit la trace du passage courant (si visible) ou du prochain :
        retourne un dict avec: {'times': Time, 'tt': np.array, 'utc': [str], 'az': [deg], 'el': [deg],
                                'aos_tt': float, 'los_tt': float}
        Lève ValueError si aucun passage AOS→LOS déterminé.
        """
        info = self._compute_pass_info(obj_type, name, t_now)
        aos_tt = info.get('aos_tt')
        los_tt = info.get('los_tt')

        # Cas "always above": visible sans AOS/LOS -> fenêtre courte autour de now
        if info.get('visible_now') and (aos_tt is None or los_tt is None):
            center = float(t_now.tt)
            span_s = 20 * 60.0  # 20 minutes
            ts = self.observer.timescale
            n = max(2, int(round(span_s / max(1.0, float(step_s))))) + 1
            tt = np.linspace(center - span_s/2.0/86400.0, center + span_s/2.0/86400.0, n)
            times = ts.tt_jd(tt)
            az, el = self._az_el_series(obj_type, name, times)
            utc = [self._fmt_time_utc(self._time_from_tt(x)) for x in tt]
            return {'times': times, 'tt': tt, 'utc': utc, 'az': az, 'el': el,
                    'aos_tt': None, 'los_tt': None}

        # Passage normal
        if (aos_tt is None) or (los_tt is None) or (los_tt <= aos_tt):
            raise ValueError("Aucun passage AOS→LOS déterminé")

        ts = self.observer.timescale
        n = max(2, int(round(((los_tt - aos_tt) * 86400.0) / max(1.0, float(step_s))))) + 1
        tt = np.linspace(aos_tt, los_tt, n)
        times = ts.tt_jd(tt)

        az, el = self._az_el_series(obj_type, name, times)
        utc = [self._fmt_time_utc(self._time_from_tt(x)) for x in tt]

        return {'times': times, 'tt': tt, 'utc': utc, 'az': az, 'el': el,
                'aos_tt': aos_tt, 'los_tt': los_tt}

    def build_pass_track_for_key(self, key: str, step_s: float = 1.0) -> Dict[str, object]:
        sel = self._targets.get(key, {})
        obj_type = sel.get('obj_type')
        name = sel.get('name')
        if not obj_type or not name:
            raise ValueError(f"Key '{key}': aucun objet sélectionné")
        t_now = self.observer.timescale.now()
        return self.build_pass_track(obj_type, name, t_now, step_s=step_s)

    # ---------- Utils ----------
    @staticmethod
    def _normalize_series(values: List[Optional[float]], n: int) -> List[Optional[float]]:
        if values is None or n <= 0:
            return []
        if len(values) == n:
            return values
        if len(values) == 1 and n > 1:
            return [values[0]] * n
        if len(values) < n:
            return (values + [None] * n)[:n]
        return values[:n]

    def _interpolate_crossing(self, times, alts, i0: int, i1: int, thr: float):
        try:
            a0 = None if alts[i0] is None else float(alts[i0])
            a1 = None if alts[i1] is None else float(alts[i1])
        except Exception:
            tt = np.atleast_1d(np.array(times.tt, dtype=float))
            return self._time_from_tt(tt[min(i1, len(tt) - 1)])
        if (a0 is None) or (a1 is None) or (a1 == a0):
            tt = np.atleast_1d(np.array(times.tt, dtype=float))
            return self._time_from_tt(tt[min(i1, len(tt) - 1)])
        r = (thr - a0) / (a1 - a0)
        r = max(0.0, min(1.0, r))
        tt = np.atleast_1d(np.array(times.tt, dtype=float))
        t_cross = tt[i0] + r * (tt[i1] - tt[i0])
        return self._time_from_tt(t_cross)

    def _fmt_time_utc(self, t):
        if t is None:
            return None
        try:
            dt = t.utc_datetime()
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return None

    def _fmt_duration(self, seconds):
        if seconds is None or seconds < 0:
            return None
        s = int(round(seconds))
        h, r = divmod(s, 3600)
        m, s = divmod(r, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    def _time_from_tt(self, tt_scalar):
        ts = self.observer.timescale
        try:
            return ts.tt_jd(float(np.array(tt_scalar).reshape(())))
        except Exception:
            return None

    @staticmethod
    def _argmax_safe(seq: List[Optional[float]], i0: int, i1: int) -> Optional[int]:
        if seq is None:
            return None
        i0 = max(0, int(i0))
        i1 = min(len(seq) - 1, int(i1))
        if i1 < i0:
            return None
        best_i = None
        best_v = None
        for i in range(i0, i1 + 1):
            v = seq[i]
            if v is None:
                continue
            if (best_v is None) or (v > best_v):
                best_v = v
                best_i = i
        return best_i

    def _build_time_array(self, ts, t_now, hours_fwd: float, step_s: float, lookback_s: float):
        start_tt = float(t_now.tt) - float(lookback_s) / 86400.0
        end_tt   = float(t_now.tt) + float(hours_fwd) / 24.0
        n = int(max(2, round((end_tt - start_tt) * 86400.0 / float(step_s)))) + 1
        tt = np.linspace(start_tt, end_tt, n)
        return ts.tt_jd(tt)

    @staticmethod
    def _empty_pass_info():
        return {
            'visible_now': False,
            'el_now_deg': None,
            'aos_utc': None,
            'los_utc': None,
            'dur_str': None,
            'max_el_deg': None,
            'max_el_time_utc': None,
            # numériques (ajouts)
            'aos_tt': None,
            'los_tt': None,
            'max_tt': None,
        }
