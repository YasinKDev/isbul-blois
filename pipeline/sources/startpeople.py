"""Offres Start People autour de Blois (startpeople.fr).

/offres-emploi (autorisé par robots.txt, contrairement à /search/) accepte un
filtre de proximité lat/lng + rayon. Chaque carte porte un attribut data-job
en JSON ; la description vient de la page détail (JSON-LD JobPosting +
blocs « description-offre »).
"""
from __future__ import annotations

import html as htmllib
import json
import re

from selectolax.lexbor import LexborHTMLParser

try:
    from .common import HOME_LAT, HOME_LON, RADIUS_KM, Offer, clean_text, client, polite_sleep
except ImportError:
    from common import HOME_LAT, HOME_LON, RADIUS_KM, Offer, clean_text, client, polite_sleep

SOURCE = "startpeople"
BASE = "https://www.startpeople.fr"
LIST_URL = BASE + "/offres-emploi"
MAX_PAGES = 6
MAX_DETAILS = 40


def _blocked(r) -> bool:
    head = r.text[:5000].lower()
    return r.status_code in (403, 429) or "cf-chl" in head or "captcha" in head


def _iso(d: str) -> str:
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", d or "")
    return f"{m[3]}-{m[2]}-{m[1]}" if m else ""


def _city(v: str) -> str:
    # « LA CHAUSSEE ST VICTOR » -> « La Chaussee Saint Victor » (pour town_coords)
    v = re.sub(r"\bSTE?\b", lambda m: "SAINTE" if m[0] == "STE" else "SAINT", v.upper())
    return v.title()


def _parse_list(page_html: str) -> list[Offer]:
    out = []
    tree = LexborHTMLParser(page_html)
    for card in tree.css("div.card-job"):
        try:
            i = card.css_first("i[data-job]")
            if i is None:  # carte promotionnelle, pas une offre
                continue
            j = json.loads(i.attributes["data-job"])
            a = card.css_first("a.iew-job-offer, a[href^='/offre-emploi/']")
            ref = j.get("field_reference") or i.attributes.get("data-ref")
            cp = re.search(r"/annonce/(\d{5})/", j.get("path", ""))
            lo, hi = j.get("field_remuneration_min"), j.get("field_remuneration_max")
            per = j.get("field_periode_de_remuneration", "")
            fmt = lambda x: f"{float(x):g}".replace(".", ",")
            salary = ""
            if lo:
                salary = f"{fmt(lo)} €" if not hi or hi == lo else f"{fmt(lo)} à {fmt(hi)} €"
                salary += f" / {per}" if per else ""
            dur = " ".join(x for x in (j.get("field_duree"), j.get("field_duree_unite")) if x)
            out.append(Offer(
                source=SOURCE,
                source_id=str(ref),
                title=htmllib.unescape(j.get("field_nom", "")).strip(),
                url=BASE + a.attributes["href"] if a else f"{BASE}{j.get('path', '')}",
                company="Start People",
                city=_city(htmllib.unescape(j.get("field_ville", ""))),
                postal_code=cp[1] if cp else "",
                contract=j.get("field_type_de_contrat", ""),
                duration=dur,
                salary=salary,
                published=_iso(j.get("created_1", "")),
                extra={k: v for k, v in {"experience": j.get("field_experience"),
                                          "distance_site": j.get("field_geoloc_proximity")}.items()
                       if v and v != "NC"},
            ))
        except Exception as e:
            print(f"[startpeople] carte ignorée : {e}")
    return out


def _fill_detail(o: Offer, page_html: str) -> None:
    tree = LexborHTMLParser(page_html)
    for s in tree.css('script[type="application/ld+json"]'):
        try:
            ld = json.loads(s.text())
        except Exception:
            continue
        if isinstance(ld, dict) and ld.get("@type") == "JobPosting":
            org = (ld.get("hiringOrganization") or {}).get("name")
            if org:
                o.company = org
            o.published = (ld.get("datePosted") or o.published)[:10]
            addr = (ld.get("jobLocation") or {}).get("address") or {}
            o.postal_code = o.postal_code or addr.get("postalCode", "")
            if not o.description:
                o.description = clean_text(htmllib.unescape(ld.get("description", "")))
            break
    blocks = [b.text(separator="\n") for b in tree.css("div.description-offre")]
    if blocks:
        o.description = clean_text(htmllib.unescape("\n".join(blocks)).replace("\xa0", " "))
    m = re.search(r"[^\n.]*\bhoraires?\b[^\n.]*|[^\n.]*\b\dx8\b[^\n.]*", o.description, re.I)
    if m:
        o.hours = m[0].strip()[:200]


def fetch() -> list[Offer]:
    found: dict[str, Offer] = {}
    params = {
        "geoloc_proximity_center[coordinates][lat]": HOME_LAT,
        "geoloc_proximity_center[coordinates][lng]": HOME_LON,
        "geoloc_proximity_center[geocoder][geolocation_geocoder_address]": "Blois",
        "geoloc_proximity": RADIUS_KM,
    }
    with client() as c:
        for page in range(MAX_PAGES):
            if page:
                polite_sleep(1)
            try:
                r = c.get(LIST_URL, params={**params, "page": page})
            except Exception as e:
                print(f"[startpeople] erreur réseau : {e}")
                break
            if _blocked(r):
                print(f"[startpeople] bloqué (HTTP {r.status_code}), source ignorée")
                return []
            if r.status_code != 200:
                print(f"[startpeople] HTTP {r.status_code} sur la page {page}")
                break
            batch = _parse_list(r.text)
            new = [o for o in batch if o.source_id not in found]
            for o in new:
                found[o.source_id] = o
            if not new or f"page={page + 1}" not in r.text:
                break

        offers = [o for o in found.values() if o.in_area()]
        offers.sort(key=lambda o: o.published, reverse=True)
        for o in offers[:MAX_DETAILS]:  # les plus récentes d'abord
            polite_sleep(1)
            try:
                r = c.get(o.url)
                if _blocked(r):
                    print(f"[startpeople] bloqué sur le détail (HTTP {r.status_code})")
                    break
                if r.status_code == 200:
                    _fill_detail(o, r.text)
            except Exception as e:
                print(f"[startpeople] détail {o.source_id} ignoré : {e}")
    return offers


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    res = fetch()
    print(len(res), "offres")
    print(json.dumps([o.to_dict() for o in res[:5]], ensure_ascii=False, indent=2))
