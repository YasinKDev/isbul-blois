"""Offres Adecco autour de Blois.

adecco.fr redirige vers www.adecco.com/fr-fr. La page de recherche (Next.js) charge
les offres en JS via deux API JSON internes, utilisées telles quelles ici :
- POST /api/data/jobs/summarized : liste (10 offres/page, tri par date, rayon en km)
- GET  /api/data/jobs/job-description-details/{id}/ADECCO/FR/fr-FR/job-details : détail
robots.txt d'adecco.com n'interdit ni /fr-fr/offres-emploi ni /api/.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

try:
    from .common import RADIUS_KM, Offer, client, normalize, polite_sleep
    from .helpers import commune_coords, html_to_text
except ImportError:  # lancé comme script (python pipeline/sources/x.py, même avec -I)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from common import RADIUS_KM, Offer, client, normalize, polite_sleep
    from helpers import commune_coords, html_to_text

BASE = "https://www.adecco.com"
SEARCH_URL = BASE + "/api/data/jobs/summarized"
DETAIL_URL = BASE + "/api/data/jobs/job-description-details/{}/ADECCO/FR/fr-FR/job-details"
LOCATION = "Blois, France"  # géocodé côté Adecco (centre de Blois)
MAX_PAGES = 10              # 10 offres par page
MAX_DETAILS = 40
MAX_REQUESTS = 50           # pages + détails
DAYS_BACK = 7


def _search_body(offset: int) -> dict:
    qs = (f"&location:{LOCATION}&fq=WebSiteName:(adecco.fr)"
          f"&d={RADIUS_KM}&sort=PostedDate desc")
    return {"queryString": qs, "filtersToDisplay": "", "range": offset,
            "siteName": "adecco", "brand": "adecco",
            "countryCode": "FR", "languageCode": "fr-FR"}


def _blocked(r) -> bool:
    if r.status_code in (403, 429, 503):
        return True
    ct = r.headers.get("content-type", "")
    return "json" not in ct and re.search(r"captcha|cf-chl|challenge-platform", r.text[:5000], re.I) is not None


def _slug(text: str) -> str:
    return normalize(text).replace(" ", "-")


def _num(x: float) -> str:
    return (f"{x:.2f}".rstrip("0").rstrip(".")).replace(".", ",")


def _salary(job: dict) -> str:
    lo, hi = job.get("minsalary") or 0, job.get("maxsalary") or 0
    if not lo and not hi:
        return ""
    unit = {"Heure": "€/heure", "Annuel": "€/an", "Mois": "€/mois"}.get(job.get("salaryTimeScale") or "", "€")
    if lo and hi and hi != lo:
        return f"{_num(lo)} – {_num(hi)} {unit}"
    return f"{_num(lo or hi)} {unit}"


def _hours(job: dict) -> str:
    parts = []
    if job.get("workingHours"):
        parts.append(str(job["workingHours"]))
    lo, hi = job.get("workMinHours") or 0, job.get("workMaxHours") or 0
    if lo or hi:
        parts.append(f"{_num(lo)}–{_num(hi)} h" if lo and hi and lo != hi else f"{_num(lo or hi)} h")
    for k in ("contractTypeTitle", "shiftTitles"):  # "Temps plein", "Jour"...
        if job.get(k) and job[k] not in parts:
            parts.append(job[k])
    return " · ".join(parts)


def _to_offer(job: dict) -> Offer:
    jid = str(job["jobId"])
    title = (job.get("jobTitle") or "").strip()
    city = (job.get("cityName") or "").strip().title()
    state = job.get("stateName") or ""
    url = f"{BASE}/fr-fr/offres-emploi/{_slug(title)}-{_slug(city)}-{_slug(state)}/{jid}"
    o = Offer(source="adecco", source_id=jid, title=title, url=url, company="Adecco", city=city)
    geo = commune_coords(city, "", state)
    if geo:
        o.lat, o.lon, o.postal_code = geo
    o.contract = job.get("employmentTypeTitle") or ""
    o.duration = job.get("contractDurationTitle") or ""
    o.salary = _salary(job)
    o.hours = _hours(job)
    o.published = (job.get("postedDate") or job.get("jobCreationDate") or "")[:10]
    o.extra = {k: v for k, v in {
        "secteur": job.get("industryTypeTitle"),
        "postes": job.get("numberOfPositions"),
        "debut_asap": job.get("vacancyStartAsap"),
        "postuler": job.get("applyUri"),
        "agence": job.get("branchCode"),
    }.items() if v not in (None, "")}
    return o


def _apply_detail(o: Offer, d: dict) -> None:
    # on coupe le texte publicitaire commun à toutes les offres Adecco
    text = html_to_text(d.get("jobDescription") or "")
    o.description = re.split(r"\n\s*A propos de nous\b", text, flags=re.I)[0].strip()
    if d.get("companyName"):
        o.company = d["companyName"]
    o.salary = _salary(d) or o.salary
    o.hours = _hours(d) or o.hours
    o.duration = d.get("contractDurationTitle") or o.duration
    if d.get("startDate"):
        o.extra["debut"] = d["startDate"][:10]
    if d.get("jobUrl"):
        o.url = d["jobUrl"]


def fetch() -> list[Offer]:
    offers: list[Offer] = []
    seen: set[str] = set()
    limit = (date.today() - timedelta(days=DAYS_BACK)).isoformat()
    with client(headers={"Origin": BASE, "Referer": BASE + "/fr-fr/offres-emploi"}) as http:
        offset, pages = 0, 0
        for page in range(MAX_PAGES):
            pages += 1
            if page:
                polite_sleep(1)
            try:
                r = http.post(SEARCH_URL, json=_search_body(offset))
            except Exception as e:
                print(f"[adecco] erreur réseau : {e}")
                break
            if _blocked(r):
                print(f"[adecco] accès bloqué (HTTP {r.status_code}), aucune offre récupérée")
                return []
            try:
                data = r.json()
            except json.JSONDecodeError:
                print(f"[adecco] réponse inattendue (HTTP {r.status_code})")
                break
            jobs = data.get("jobs") or []
            for job in jobs:
                try:
                    o = _to_offer(job)
                    if o.source_id not in seen and o.in_area():
                        seen.add(o.source_id)
                        offers.append(o)
                except Exception as e:
                    print(f"[adecco] offre ignorée : {e}")
            nxt = (data.get("pagination") or {}).get("nextRange")
            oldest = min((j.get("postedDate") or "9")[:10] for j in jobs) if jobs else ""
            if not jobs or not nxt or oldest < limit:
                break
            offset = nxt

        # La liste n'a pas de description : on prend le détail (plus récentes d'abord)
        for o in offers[:min(MAX_DETAILS, MAX_REQUESTS - pages)]:
            polite_sleep(1)
            try:
                r = http.get(DETAIL_URL.format(o.source_id))
                if _blocked(r):
                    print("[adecco] détail bloqué, on s'arrête là")
                    break
                if r.status_code == 200:
                    _apply_detail(o, r.json())
            except Exception as e:
                print(f"[adecco] détail {o.source_id} ignoré : {e}")
    return offers


if __name__ == "__main__":
    res = fetch()
    print(len(res), "offres")
    print(json.dumps([o.to_dict() for o in res[:5]], ensure_ascii=False, indent=2))
