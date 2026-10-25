"""France Travail : moteur de recherche public candidat.francetravail.fr.

L'API « Offres d'emploi v2 » n'est plus en accès libre sur francetravail.io,
on lit donc la page de résultats publique (autorisée par robots.txt tant qu'on
n'utilise pas motsCles). Elle reprend aussi les offres des partenaires :
Adecco, Randstad, Manpower, etc.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from selectolax.lexbor import LexborHTMLParser

try:
    from .common import Offer, client, clean_text, polite_sleep, RADIUS_KM
except ImportError:
    from common import Offer, client, clean_text, polite_sleep, RADIUS_KM

BASE = "https://candidat.francetravail.fr"
PARAMS = {
    "lieux": "41018",           # Blois
    "rayon": str(RADIUS_KM),
    "typeContrat": "CDD,MIS",
    "offresPartenaires": "true",
    "tri": "1",                 # plus récentes d'abord
}
PAGE = 20
MAX_PAGES = 20


def _published(text: str) -> str:
    text = text.lower()
    today = date.today()
    if "aujourd" in text:
        return today.isoformat()
    if "hier" in text:
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"il y a (\d+) jour", text)
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", text)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return ""


def _parse(html: str) -> list[Offer]:
    offers = []
    for li in LexborHTMLParser(html).css("li.result"):
        try:
            oid = li.attributes.get("data-id-offre")
            title = li.css_first(".media-heading-title").text(strip=True)
            sub = li.css_first("p.subtext")
            city_node = sub.css_first("span") if sub else None
            place = city_node.text(strip=True) if city_node else ""  # "41 - Blois"
            dept, _, city = place.partition(" - ")
            company = sub.text(strip=True).replace(place, "").strip(" - –\n\t") if sub else ""
            desc = li.css_first("p.description")
            contrat = li.css_first(".media-right p.contrat") or li.css_first("p.contrat")
            ctype = contrat.css_first(".type-contrat") if contrat else None
            contract_text = contrat.text(strip=True) if contrat else ""
            hours = ctype.text(strip=True) if ctype else ""
            logo = li.css_first(".media-object img")
            origin = (logo.attributes.get("alt") or "").replace("Origine de l'offre : ", "") if logo else ""
            pub = li.css_first("p.date")
            offers.append(Offer(
                source="francetravail",
                source_id=oid,
                title=title,
                url=f"{BASE}/offres/recherche/detail/{oid}",
                company=company,
                city=(city or place).title(),
                postal_code=dept if dept.isdigit() else "",
                contract=contract_text.replace(hours, "").strip() if hours else contract_text,
                hours=hours,
                description=clean_text(desc.text() if desc else ""),
                published=_published(pub.text() if pub else ""),
                extra={"origin": origin},
            ))
        except Exception as e:
            print(f"[francetravail] offre ignorée : {e}")
    return offers


def fetch(days: int = 7) -> list[Offer]:
    """Offres d'intérim et CDD publiées depuis `days` jours (1, 3, 7, 14 ou 31)."""
    params = {**PARAMS, "emission": str(days)}
    offers: list[Offer] = []
    with client() as http:
        r = http.get(f"{BASE}/offres/recherche", params=params)
        if r.status_code != 200:
            print(f"[francetravail] HTTP {r.status_code}")
            return []
        offers += _parse(r.text)
        m = re.search(r"(\d+)\s+offres", r.text)
        total = int(m.group(1)) if m else PAGE
        for page in range(1, min(MAX_PAGES, -(-total // PAGE))):
            polite_sleep(1)
            start = page * PAGE
            r = http.get(
                f"{BASE}/offres/recherche.rechercheoffre:afficherplusderesultats/{start}-{start + PAGE - 1}/1",
                params=params, headers={"X-Requested-With": "XMLHttpRequest"},
            )
            if r.status_code != 200:
                break
            try:
                parts = r.json()["_tapestry"]["content"]
            except Exception:
                break
            html = "".join(p[1] for p in parts if isinstance(p, list) and len(p) > 1)
            batch = _parse(html)
            if not batch:
                break
            offers += batch
    seen, out = set(), []
    for o in offers:
        if o.source_id not in seen and o.in_area():
            seen.add(o.source_id)
            out.append(o)
    return out


if __name__ == "__main__":
    import json

    found = fetch()
    print(len(found), "offres")
    print(json.dumps([o.to_dict() for o in found[:5]], ensure_ascii=False, indent=1))
