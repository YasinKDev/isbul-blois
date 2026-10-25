"""Source Hellowork (hellowork.com).

robots.txt interdit la page de recherche (/fr-fr/emploi/recherche.html) et toute
URL avec « ? ». On lit donc uniquement les pages « listes » SEO autorisées
(/fr-fr/interim/domaine_logistique-ville_blois-41000.html, ...), première page
seulement, puis la fiche de chaque offre retenue (JSON-LD JobPosting) pour la
description, la date exacte et le code postal.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

try:
    from .common import Offer, clean_text, client, polite_sleep
except ImportError:  # lancé comme script
    from common import Offer, clean_text, client, polite_sleep

from selectolax.lexbor import LexborHTMLParser

SOURCE = "hellowork"
BASE = "https://www.hellowork.com"

# Pages listes (sans « ? », autorisées par robots.txt). Une 404 est ignorée.
LIST_PAGES = [
    "/fr-fr/interim/domaine_logistique-ville_blois-41000.html",
    "/fr-fr/interim/domaine_production-ville_blois-41000.html",
    "/fr-fr/emploi/domaine_logistique-ville_blois-41000.html",
    "/fr-fr/emploi/domaine_production-ville_blois-41000.html",
    "/fr-fr/emploi/metier_manutentionnaire-ville_blois-41000.html",
    "/fr-fr/emploi/metier_operateur-de-production-ville_blois-41000.html",
    "/fr-fr/emploi/metier_agent-logisticien-ville_blois-41000.html",
    "/fr-fr/emploi/metier_magasinier-ville_blois-41000.html",
    "/fr-fr/interim/ville_blois-41000.html",
    "/fr-fr/interim/ville_mer-41500.html",
    "/fr-fr/interim/ville_la-chaussee-saint-victor-41260.html",
]

CONTRATS_OK = ("intérim", "interim", "cdd", "temporaire", "saisonnier", "mission")
PRIORITE = re.compile(r"manutention|pr[ée]parat|conditionn|emball|production|fabrication|\btri\b"
                      r"|logisti|magasin|cariste|usine|ouvrier|quai|op[ée]rat", re.I)
MAX_AGE_DAYS = 7
MAX_DETAILS = 38
MAX_REQUESTS = 50


def _age_days(text: str) -> int | None:
    """« il y a 19 heures » -> 0, « il y a 3 jours » -> 3, « il y a 2 mois » -> 60."""
    t = text.lower()
    if "instant" in t or "minute" in t or "heure" in t or "aujourd" in t:
        return 0
    if "hier" in t:
        return 1
    m = re.search(r"(\d+)\s*(jour|semaine|mois)", t)
    if not m:
        return None
    n = int(m.group(1))
    return n * {"jour": 1, "semaine": 7, "mois": 30}[m.group(2)]


def _blocked(r) -> bool:
    return r.status_code in (403, 429, 503) or "cf-chl" in r.text[:5000] or "captcha" in r.text[:5000].lower()


def _parse_list(html: str) -> list[dict]:
    tree = LexborHTMLParser(html)
    cards = []
    for li in tree.css("li[data-id-storage-item-id]"):
        try:
            oid = li.attributes.get("data-id-storage-item-id") or ""
            a = li.css_first("a[data-cy=offerTitle]")
            if not oid or a is None:
                continue
            spans = a.css("h3 span")
            title = spans[0].text(strip=True) if spans else a.text(strip=True)
            company = spans[1].text(strip=True) if len(spans) > 1 else ""
            loc = li.css_first("[data-cy=localisationCard]")
            loc = loc.text(strip=True) if loc else ""
            city, _, dep = loc.rpartition(" - ")
            contract = li.css_first("[data-cy=contractCard]")
            salary = next((s.text(strip=True) for s in li.css(".typo-s-bold") if "€" in s.text()), "")
            tags = [t.text(strip=True) for t in li.css("[data-cy=contractTag]")]
            duration = next((t for t in tags if re.search(r"\d", t)), "")
            hours = ", ".join(t for t in tags if t != duration and not t.startswith("+"))
            age_el = li.css_first(".text-grey-500")
            cards.append({
                "id": oid,
                "title": title,
                "company": company,
                "city": (city or loc).strip(),
                "dep": dep.strip(),
                "contract": contract.text(strip=True) if contract else "",
                "salary": salary,
                "duration": duration,
                "hours": hours,
                "age": _age_days(age_el.text(strip=True)) if age_el else None,
                "url": BASE + (a.attributes.get("href") or f"/fr-fr/emplois/{oid}.html"),
            })
        except Exception as e:  # une carte cassée ne doit pas tout arrêter
            print(f"[hellowork] carte ignorée : {e}")
    return cards


def _job_posting(html: str) -> dict:
    tree = LexborHTMLParser(html)
    for s in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(s.text())
        except ValueError:
            continue
        for d in data if isinstance(data, list) else [data]:
            if isinstance(d, dict) and d.get("@type") == "JobPosting":
                return d
    return {}


def _fill_from_detail(offer: Offer, jp: dict) -> None:
    offer.description = clean_text(re.sub(r"<(br|/p|/h2|/li)[^>]*>", "\n", jp.get("description", "")))
    if jp.get("datePosted"):
        offer.published = jp["datePosted"][:10]
    addr = ((jp.get("jobLocation") or {}).get("address") or {}) if isinstance(jp.get("jobLocation"), dict) else {}
    if addr.get("postalCode"):
        offer.postal_code = addr["postalCode"]
    if addr.get("addressLocality"):
        offer.city = addr["addressLocality"]
    geo = (jp.get("jobLocation") or {}).get("geo") if isinstance(jp.get("jobLocation"), dict) else None
    if isinstance(geo, dict) and geo.get("latitude"):
        offer.lat, offer.lon = float(geo["latitude"]), float(geo["longitude"])
    org = jp.get("hiringOrganization") or {}
    if not offer.company and isinstance(org, dict):
        offer.company = org.get("name", "")
    et = jp.get("employmentType")
    if et:
        offer.extra["employmentType"] = et


def fetch() -> list[Offer]:
    today = date.today()
    seen: dict[str, Offer] = {}
    n_req = 0
    with client() as c:
        for path in LIST_PAGES:
            if n_req >= MAX_REQUESTS - MAX_DETAILS:
                break
            try:
                r = c.get(BASE + path)
                n_req += 1
                if _blocked(r):
                    print(f"[hellowork] bloqué ({r.status_code}) sur {path}, on arrête.")
                    return []
                if r.status_code != 200:
                    continue
                for card in _parse_list(r.text):
                    if card["id"] in seen:
                        continue
                    if not any(k in card["contract"].lower() for k in CONTRATS_OK):
                        continue
                    if card["age"] is not None and card["age"] > MAX_AGE_DAYS:
                        continue
                    o = Offer(
                        source=SOURCE, source_id=card["id"], title=card["title"], url=card["url"],
                        company=card["company"], city=card["city"], postal_code=card["dep"],
                        contract=card["contract"], duration=card["duration"], salary=card["salary"],
                        hours=card["hours"],
                    )
                    if card["age"] is not None:
                        o.published = (today - timedelta(days=card["age"])).isoformat()
                    if o.in_area():
                        seen[o.source_id] = o
            except Exception as e:
                print(f"[hellowork] erreur sur {path} : {e}")
            polite_sleep(1)

        # Fiches détaillées : description + date exacte + code postal.
        # Les métiers visés d'abord, au cas où le quota de fiches serait atteint.
        todo = sorted(seen.values(), key=lambda o: not PRIORITE.search(o.title))
        for o in todo[:MAX_DETAILS]:
            if n_req >= MAX_REQUESTS:
                break
            try:
                r = c.get(o.url)
                n_req += 1
                if _blocked(r):
                    print("[hellowork] bloqué sur les fiches, on garde les offres sans description.")
                    break
                if r.status_code == 200:
                    _fill_from_detail(o, _job_posting(r.text))
            except Exception as e:
                print(f"[hellowork] fiche {o.source_id} : {e}")
            polite_sleep(1)

    offers = [o for o in seen.values() if o.in_area()]
    cutoff = (today - timedelta(days=MAX_AGE_DAYS)).isoformat()
    return [o for o in offers if not o.published or o.published >= cutoff]


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    res = fetch()
    print(len(res), "offres")
    print(json.dumps([o.to_dict() for o in res[:5]], ensure_ascii=False, indent=2))
