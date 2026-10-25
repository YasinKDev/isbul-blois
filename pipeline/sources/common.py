"""Outils partagés par toutes les sources d'offres.

Chaque source expose `fetch() -> list[Offer]` et ne renvoie que des offres
situées dans le rayon autour de Blois (RADIUS_KM).
"""
from __future__ import annotations

import hashlib
import math
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field

import httpx

HOME_LAT, HOME_LON = 47.5861, 1.3359  # centre de Blois
RADIUS_KM = 25  # Mer est à ~18 km

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

# Communes du rayon (nom normalisé -> (lat, lon)), pour les sources qui ne donnent
# qu'un nom de ville.
TOWNS = {
    "blois": (47.5861, 1.3359),
    "vineuil": (47.5833, 1.3764),
    "la chaussee saint victor": (47.6153, 1.3653),
    "saint gervais la foret": (47.5675, 1.3578),
    "villebarou": (47.6236, 1.3236),
    "saint sulpice de pommeray": (47.6022, 1.2731),
    "fosse": (47.6372, 1.2914),
    "villerbon": (47.6597, 1.3772),
    "menars": (47.6431, 1.4053),
    "cour cheverny": (47.5103, 1.4581),
    "cheverny": (47.5003, 1.4578),
    "chailles": (47.5408, 1.3017),
    "candé sur beuvron": (47.4933, 1.2581),
    "cande sur beuvron": (47.4933, 1.2581),
    "chitenay": (47.4986, 1.3747),
    "cellettes": (47.5281, 1.3828),
    "mont pres chambord": (47.5594, 1.4553),
    "huisseau sur cosson": (47.5917, 1.4508),
    "saint claude de diray": (47.6164, 1.4194),
    "chambord": (47.6161, 1.5172),
    "bracieux": (47.5497, 1.5406),
    "onzain": (47.4983, 1.1767),
    "veuzain sur loire": (47.4983, 1.1767),
    "chouzy sur cisse": (47.5258, 1.2475),
    "herbault": (47.6050, 1.1392),
    "mer": (47.7044, 1.5072),
    "suevres": (47.6672, 1.4597),
    "cour sur loire": (47.6453, 1.4269),
    "muides sur loire": (47.6703, 1.5272),
    "saint laurent nouan": (47.7186, 1.6022),
    "marolles": (47.6408, 1.2858),
    "averdon": (47.6922, 1.3036),
    "contres": (47.4183, 1.4297),
    "le controis en sologne": (47.4183, 1.4297),
    "les montils": (47.4950, 1.2994),
    "seur": (47.4972, 1.3369),
    "mesland": (47.5108, 1.1072),
    "chaumont sur loire": (47.4806, 1.1886),
    "molineuf": (47.5956, 1.2139),
    "orchaise": (47.5864, 1.2014),
    "valencisse": (47.5864, 1.2014),
}


@dataclass
class Offer:
    source: str           # ex. "francetravail", "adecco"
    source_id: str        # identifiant de l'offre chez la source
    title: str
    url: str
    company: str = ""
    city: str = ""
    postal_code: str = ""
    lat: float | None = None
    lon: float | None = None
    contract: str = ""    # "Intérim", "CDD", "CDI", ... tel que donné par la source
    duration: str = ""    # durée de mission si connue
    salary: str = ""
    hours: str = ""       # horaires / temps de travail
    description: str = "" # texte brut, tronqué à MAX_DESC
    published: str = ""   # date ISO (AAAA-MM-JJ) si connue
    phone: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.source_id}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["uid"] = self.uid
        d["distance_km"] = self.distance_km()
        return d

    def distance_km(self) -> float | None:
        if self.lat is None or self.lon is None:
            coords = town_coords(self.city)
            if not coords:
                # Commune hors de TOWNS : on demande à geo.api.gouv.fr.
                try:
                    from .helpers import commune_coords
                except ImportError:
                    from helpers import commune_coords
                found = commune_coords(self.city, self.postal_code, "loir et cher")
                if not found:
                    return None
                coords = found[:2]
            lat, lon = coords
        else:
            lat, lon = self.lat, self.lon
        return round(haversine(HOME_LAT, HOME_LON, lat, lon), 1)

    def in_area(self) -> bool:
        d = self.distance_km()
        if d is not None:
            return d <= RADIUS_KM
        # Sans coordonnées : seulement les codes postaux proches de Blois.
        return self.postal_code in NEAR_CP


MAX_DESC = 3000

# Codes postaux à moins de ~25 km de Blois.
NEAR_CP = {"41000", "41350", "41260", "41330", "41190", "41120", "41150",
           "41500", "41700", "41250", "41220", "41310"}


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def town_coords(city: str) -> tuple[float, float] | None:
    n = normalize(re.sub(r"\(.*?\)|\d+", " ", city or ""))
    n = re.sub(r"^\d+\s*", "", n).strip()
    if n in TOWNS:
        return TOWNS[n]
    for name, coords in TOWNS.items():
        if n.startswith(name + " ") or n.endswith(" " + name):
            return coords
    return None


def clean_text(text: str, limit: int = MAX_DESC) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text[:limit]


def fingerprint(offer: Offer) -> str:
    """Empreinte pour repérer la même offre publiée sur plusieurs sites."""
    base = f"{normalize(offer.title)}|{normalize(offer.company)}|{normalize(offer.city)}"
    return hashlib.sha1(base.encode()).hexdigest()[:16]


def client(**kw) -> httpx.Client:
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"}
    headers.update(kw.pop("headers", {}))
    return httpx.Client(headers=headers, timeout=30, follow_redirects=True, **kw)


def polite_sleep(seconds: float = 1.0) -> None:
    time.sleep(seconds)
