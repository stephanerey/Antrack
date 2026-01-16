# tracking/spacecrafts.py
import os
from typing import List, Optional, Tuple
import spiceypy as sp

_DEFAULT_NAMES = [
    # Liste générique (sera filtrée par presence des kernels)
    "VOYAGER 1", "VOYAGER 2",
    "CASSINI", "NEW HORIZONS",
    "JUNO", "JUICE",
    "MARS RECONNAISSANCE ORBITER", "MARS EXPRESS", "MAVEN",
    "BEPI COLOMBO", "OSIRIS-REX", "PARKER SOLAR PROBE",
    "TGO", "EXOMARS TRACE GAS ORBITER",
]

class SpacecraftRepo:
    """
    Gestionnaire des kernels SPICE pour les sondes spatiales.
    Les fichiers .bsp doivent être placés dans data/spacecrafts/.
    Optionnel: data/spacecrafts/spacecrafts.txt (une sonde par ligne)
    """
    def __init__(self, base_dir: str, logger=None):
        self.base_dir = os.path.abspath(base_dir)
        os.makedirs(self.base_dir, exist_ok=True)
        self.logger = logger
        self._kernels_loaded = False
        self._name_cache: List[str] = []

    # ---- Kernels ----
    def _load_kernels_once(self):
        if self._kernels_loaded:
            return
        files = [f for f in os.listdir(self.base_dir) if f.lower().endswith(".bsp")]
        for fname in files:
            path = os.path.join(self.base_dir, fname)
            try:
                sp.furnsh(path)
                if self.logger:
                    self.logger.info(f"[Spacecraft] loaded {fname}")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"[Spacecraft] error loading {fname}: {e}")
        self._kernels_loaded = True

    # ---- Listing / résolution ----
    def list_spacecrafts(self) -> List[str]:
        """
        Retourne une liste de noms “probables”.
        Si data/spacecrafts/spacecrafts.txt existe → on l’utilise.
        Sinon on fournit une liste par défaut. Le succès effectif
        dépendra des kernels chargés (bodn2c doit réussir).
        """
        self._load_kernels_once()
        # fichier optionnel
        txt = os.path.join(self.base_dir, "spacecrafts.txt")
        names: List[str] = []
        if os.path.isfile(txt):
            try:
                with open(txt, "r", encoding="utf-8") as f:
                    for line in f:
                        s = line.strip()
                        if s and not s.startswith("#"):
                            names.append(s)
            except Exception as e:
                if self.logger:
                    self.logger.error(f"[Spacecraft] read spacecrafts.txt failed: {e}")
        if not names:
            names = list(_DEFAULT_NAMES)
        # Déduplique + trie
        names = sorted({n.strip(): True for n in names}.keys())
        # Facultatif: si bodn2c échoue, le nom restera listé (l’UI laissera aussi une saisie libre)
        return names

    def resolve(self, name: str) -> Optional[int]:
        """Résout un nom en NAIF ID (retourne None si inconnu)."""
        if not name:
            return None
        self._load_kernels_once()
        try:
            return sp.bodn2c(name)
        except Exception:
            return None

    def position_earth_centered(self, target: str, et: float) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """
        Retourne la position (x,y,z) km du target par rapport au centre de la Terre, cadre J2000.
        """
        self._load_kernels_once()
        try:
            # STATE of target relative to EARTH in J2000, no aberration correction
            state, _ = sp.spkezr(target, et, "J2000", "NONE", "EARTH")
            return float(state[0]), float(state[1]), float(state[2])
        except Exception as e:
            if self.logger:
                self.logger.error(f"[Spacecraft] position error '{target}': {e}")
            return (None, None, None)
