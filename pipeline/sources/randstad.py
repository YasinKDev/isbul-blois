"""Offres Randstad autour de Blois.

Les pages de recherche randstad.fr embarquent la réponse Elasticsearch dans
`window.__ROUTE_DATA__` (30 offres/page). On lit la page du département
/emploi/loir-et-cher/ (+ /page-N/) puis on filtre au rayon avec code postal + ville.
Pourquoi le département et pas Blois + rayon : robots.txt interdit les URL de rayon
(/km-, /radius-search/) et de tri par date (/sd-) ; /emploi/blois/ ne donne que la
ville elle-même. Le détail (/emploi/<slug>/) donne description complète, durée, GPS.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

try:
    from .common import Offer, client, polite_sleep
    from .helpers import commune_coords, html_to_text
except ImportError:  # lancé comme script (python pipeline/sources/x.py, même avec -I)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from common import Offer, client, polite_sleep
    from helpers import commune_coords, html_to_text

BASE = "https://www.randstad.fr"
LIST_URL = BASE + "/emploi/loir-et-cher/"
PER_PAGE = 30
MAX_PAGES = 8
MAX_DETAILS = 40
MAX_REQUESTS = 50
DAYS_BACK = 7  # détail seulement pour les offres récentes


def _route_data(text: str) -> dict | None:
    key = "window.__ROUTE_DATA__ = "
    i = text.find(key)
    if i < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[i + len(key):])[0]
    except json.JSONDecodeError:
        return None


def _get(http, url: str):
    """GET ; renvoie (réponse, bloqué ?)."""
    r = http.get(url)
    blocked = r.status_code in (403, 429, 503) or (
        "__ROUTE_DATA__" not in r.text and re.search(r"cf-chl|challenge-platform|captcha-delivery", r.text[:20000]) is not None)
    return r, blocked


def _num(x: str) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return ""
    return (f"{v:.2f}".rstrip("0").rstrip(".")).replace(".", ",") if v else ""


def _salary(s: dict) -> str:
    lo = "" if s.get("HideSalaryMin") == "true" else _num(s.get("SalaryMin"))
    hi = "" if s.get("HideSalaryMax") == "true" else _num(s.get("SalaryMax"))
    if not lo and not hi:
        return ""
    unit = {"par heure": "€/heure", "par mois": "€/mois", "par année": "€/an", "par an": "€/an",
            "par jour": "€/jour"}.get((s.get("CompensationType") or "").lower(), "€")
    if lo and hi and lo != hi:
        return f"{lo} – {hi} {unit}"
    return f"{lo or hi} {unit}"


def _contract(t: str) -> str:
    t = (t or "").strip()
    return t[:1].upper() + t[1:] if t and not t.isupper() else t


def _url(src: dict) -> str:
    # l'URL publique est dans le dataLayer de la carte ; sinon on la reconstruit
    m = re.search(r'"url":"(/emploi/[^"]+)"', src.get("dataLayerJobClick") or "")
    if m:
        return BASE + m.group(1)
    bx = src.get("BlueXSanitized") or {}
    return f"{BASE}/emploi/{bx.get('Title', 'offre')}_{bx.get('City', '')}_{src['JobId'].lower()}/"


def _to_offer(src: dict) -> Offer:
    info = src.get("JobInformation") or {}
    loc = src.get("JobLocation") or {}
    client_name = (src.get("ClientInformation") or {}).get("ClientName")
    brand = ((src.get("JobIdentity") or {}).get("CompanyName") or "Randstad").strip()
    o = Offer(source="randstad", source_id=src["JobId"], title=(info.get("Title") or "").strip(),
              url=_url(src), company=client_name or brand.title(),
              city=loc.get("City") or "", postal_code=loc.get("Postcode") or "")
    geo = commune_coords(o.city, o.postal_code, loc.get("Region") or "")
    if geo:
        o.lat, o.lon, o.postal_code = geo
    o.contract = _contract(info.get("JobType"))
    o.duration = info.get("Duration") or ""
    o.hours = info.get("Hours") or ""
    o.salary = _salary(src.get("Salary") or {})
    o.description = html_to_text(info.get("Description") or "")  # tronquée dans la liste
    o.published = ((src.get("JobDates") or {}).get("DateCreated") or "")[:10]
    o.extra = {k: v for k, v in {
        "secteur": (src.get("BlueXJobData") or {}).get("Specialism"),
        "agence": (src.get("Contact") or {}).get("Office"),
        "marque": brand,
    }.items() if v}
    return o


def _apply_detail(o: Offer, src: dict) -> None:
    info = src.get("JobInformation") or {}
    loc = src.get("JobLocation") or {}
    dates = src.get("JobDates") or {}
    if info.get("Description"):
        o.description = html_to_text(info["Description"])
    o.duration = info.get("Duration") or o.duration
    o.hours = info.get("Hours") or o.hours
    o.salary = _salary(src.get("Salary") or {}) or o.salary
    try:
        o.lat, o.lon = float(loc["Latitude"]), float(loc["Longitude"])
    except (KeyError, TypeError, ValueError):
        pass
    for k, name in (("DateStart", "debut"), ("DateEnd", "fin")):
        if dates.get(k):
            o.extra[name] = dates[k][:10]
    if info.get("Qualification"):
        o.extra["qualification"] = info["Qualification"]


def fetch() -> list[Offer]:
    offers: list[Offer] = []
    seen: set[str] = set()
    requests = 0
    with client() as http:
        pages = 1
        page = 1
        while page <= min(pages, MAX_PAGES):
            if requests:
                polite_sleep(1)
            url = LIST_URL if page == 1 else f"{LIST_URL}page-{page}/"
            try:
                r, blocked = _get(http, url)
            except Exception as e:
                print(f"[randstad] erreur réseau : {e}")
                break
            requests += 1
            if blocked:
                print(f"[randstad] accès bloqué (HTTP {r.status_code}), aucune offre récupérée")
                return []
            data = _route_data(r.text) if r.status_code == 200 else None
            if not data:
                print(f"[randstad] page {page} illisible (HTTP {r.status_code})")
                break
            hits = (data.get("searchResults") or {}).get("hits") or {}
            pages = -(-int(hits.get("total") or 0) // PER_PAGE)
            for h in hits.get("hits") or []:
                try:
                    o = _to_offer(h["_source"])
                    if o.source_id not in seen and o.in_area():
                        seen.add(o.source_id)
                        offers.append(o)
                except Exception as e:
                    print(f"[randstad] offre ignorée : {e}")
            page += 1

        offers.sort(key=lambda o: o.published, reverse=True)
        # Détail (description complète, durée, GPS) pour les offres récentes
        limit = (date.today() - timedelta(days=DAYS_BACK)).isoformat()
        recent = [o for o in offers if o.published >= limit]
        for o in recent[:min(MAX_DETAILS, MAX_REQUESTS - requests)]:
            polite_sleep(1)
            try:
                r, blocked = _get(http, o.url)
                if blocked:
                    print("[randstad] détail bloqué, on s'arrête là")
                    break
                data = _route_data(r.text) if r.status_code == 200 else None
                hits = (((data or {}).get("jobData") or {}).get("hits") or {}).get("hits") or []
                if hits:
                    _apply_detail(o, hits[0]["_source"])
            except Exception as e:
                print(f"[randstad] détail {o.source_id} ignoré : {e}")
    return [o for o in offers if o.in_area()]


if __name__ == "__main__":
    res = fetch()
    print(len(res), "offres")
    print(json.dumps([o.to_dict() for o in res[:5]], ensure_ascii=False, indent=2))
