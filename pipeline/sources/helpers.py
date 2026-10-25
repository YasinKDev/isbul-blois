"""Petits outils en plus de common.py pour adecco / randstad.

- commune_coords : coordonnées d'une commune via geo.api.gouv.fr (API publique de
  l'État), pour les sources qui ne donnent qu'un nom de ville et un code postal.
- html_to_text : HTML -> texte brut lisible (sauts de ligne, entités décodées).
"""
from __future__ import annotations

import html
import re

try:
    from .common import TOWNS, client, clean_text, normalize
except ImportError:  # lancé comme script (python pipeline/sources/x.py, même avec -I)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from common import TOWNS, client, clean_text, normalize

GEO_API = "https://geo.api.gouv.fr/departements/{}/communes?fields=nom,codesPostaux,centre&format=json"

# Départements autour de Blois (nom normalisé -> code)
DEPTS = {
    "loir et cher": "41", "indre et loire": "37", "loiret": "45", "cher": "18",
    "indre": "36", "eure et loir": "28", "sarthe": "72",
}

_communes: dict[str, list[tuple[str, list[str], float, float]]] = {}


def _norm_city(city: str) -> str:
    n = normalize(re.sub(r"\(.*?\)|\bcedex\b.*", " ", city or "", flags=re.I))
    n = re.sub(r"\d+", " ", n)
    n = re.sub(r"\bst\b", "saint", n)
    n = re.sub(r"\bste\b", "sainte", n)
    return re.sub(r"\s+", " ", n).strip()


def _load(dept: str) -> list[tuple[str, list[str], float, float]]:
    """Liste (nom normalisé, codes postaux, lat, lon) des communes d'un département."""
    if dept not in _communes:
        rows = []
        try:
            with client() as http:
                r = http.get(GEO_API.format(dept))
                r.raise_for_status()
                for c in r.json():
                    lon, lat = c["centre"]["coordinates"]
                    rows.append((_norm_city(c["nom"]), c.get("codesPostaux") or [], lat, lon))
        except Exception as e:  # réseau / format : on se rabat sur TOWNS
            print(f"[helpers] communes {dept} indisponibles : {e}")
        _communes[dept] = rows
    return _communes[dept]


def commune_coords(city: str, postal_code: str = "", region: str = "") -> tuple[float, float, str] | None:
    """(lat, lon, code postal) d'une commune, ou None si introuvable."""
    n = _norm_city(city)
    dept = postal_code[:2] if re.fullmatch(r"\d{2}|\d{5}", postal_code or "") else DEPTS.get(normalize(region), "")
    if len(postal_code or "") == 2:  # seulement le département (France Travail)
        postal_code = ""
    rows = _load(dept) if dept else []
    same_name = [c for c in rows if c[0] == n]
    if postal_code:
        exact = [c for c in same_name if postal_code in c[1]]
        same_name = exact or same_name
    if same_name:
        _, cps, lat, lon = same_name[0]
        return lat, lon, postal_code or (cps[0] if cps else "")
    if n in TOWNS:  # anciens noms de communes fusionnées (Onzain, Contres...)
        lat, lon = TOWNS[n]
        return lat, lon, postal_code
    if postal_code:  # nom inconnu : 1re commune du code postal
        for _, cps, lat, lon in rows:
            if postal_code in cps:
                return lat, lon, postal_code
    return None


def html_to_text(raw: str) -> str:
    """HTML -> texte brut, en gardant les retours à la ligne des paragraphes/listes."""
    t = re.sub(r"(?i)<br\s*/?>|</br>|</p>|</h\d>|</li>|</div>", "\n", raw or "")
    t = re.sub(r"(?i)<li[^>]*>", "\n- ", t)
    t = html.unescape(clean_text(t, limit=100_000))
    t = re.sub(r"[ \t ]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    return clean_text(t)
