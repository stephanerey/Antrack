# src/tracking/observer.py
# Gestion d'un observateur (position géographique) pour le tracking

from decimal import Decimal
from typing import Optional

from skyfield.api import load, wgs84


class Observer:
    """
    Représente un observateur terrestre (site) avec coordonnées WGS84,
    prêt à être combiné à un corps céleste (ex: Terre) pour un repère topocentrique.
    """
    def __init__(self):
        # Timescale Skyfield (UTC)
        self.timescale = load.timescale()
        self.name: Optional[str] = None
        self.longitude: Optional[float] = None
        self.latitude: Optional[float] = None
        self.altitude: Optional[float] = None
        # Objets Skyfield
        self.topocentric = None
        self.astrometric = None

    def create_observer(self, name, longitude, latitude, altitude, planet_earth):
        """
        Initialise l'observateur à partir de coordonnées (en degrés et mètres).
        - name: str
        - longitude, latitude: degrés (float/str/Decimal)
        - altitude: mètres (float/str/Decimal)
        - planet_earth: segment 'earth' du kernel SPK (ex: load('...de440s.bsp')['earth'])
        """
        self.name = name
        # Convertir en float puis Decimal pour wgs84.latlon
        self.longitude = float(longitude) if longitude is not None else None
        self.latitude = float(latitude) if latitude is not None else None
        self.altitude = float(altitude) if altitude is not None else None

        if self.latitude is None or self.longitude is None or self.altitude is None:
            raise ValueError("Coordonnées observateur incomplètes (latitude/longitude/altitude manquantes)")

        self.topocentric = wgs84.latlon(
            latitude_degrees=Decimal(str(self.latitude)),
            longitude_degrees=Decimal(str(self.longitude)),
            elevation_m=Decimal(str(self.altitude)),
        )
        self.astrometric = self.topocentric + planet_earth if planet_earth is not None else None
