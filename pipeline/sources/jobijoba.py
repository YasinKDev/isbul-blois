"""Source Jobijoba (jobijoba.com, agrégateur).

robots.txt interdit la recherche /fr/query/ (filtres date/contrat, pagination)
pour les robots génériques. On lit donc seulement les pages SEO autorisées
/fr/emploi/<Métier>/<Ville> et /fr/interim/lieu/<Ville> (une page, ~30 offres,
triées par pertinence), on filtre nous-mêmes contrat + date (7 jours), puis on
ouvre la fiche /fr/annonce/... pour la description complète.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

try:
    from .common import Offer, clean_text, client, normalize, polite_sleep
except ImportError:  # lancé comme script
    from common import Offer, clean_text, client, normalize, polite_sleep

from selectolax.lexbor import LexborHTMLParser

SOURCE = "jobijoba"
BASE = "https://www.jobijoba.com"

KEYWORDS = [
    "Manutentionnaire",
    "Preparateur+de+commandes",
    "Agent+de+production",
    "Operateur+de+production",
    "Conditionnement",
    "Emballage",
    "Magasinier",
    "Logistique",
]
TOWNS = ["Blois", "Mer"]
EXTRA_PAGES = ["/fr/interim/lieu/Blois"]

CONTRATS_OK = ("interim", "cdd", "saisonnier", "temporaire", "extra")
CONTRATS_KO = ("cdi", "stage", "alternance", "apprentissage", "independant", "benevolat", "franchise")
PRIORITE = re.compile(r"manutention|pr[ée]parat|conditionn|emball|production|fabrication|\btri\b"
                      r"|logisti|magasin|cariste|usine|ouvrier|quai|op[ée]rat", re.I)
MAX_AGE_DAYS = 7
MAX_DETAILS = 30
MAX_REQUESTS = 50

MOIS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
        "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12}


def _parse_date(text: str, today: date) -> date | None:
    """« Il y a 21 h » / « Il y a 3 j » / « Hier » / « 23 octobre » -> date."""
    t = normalize(text)
    if not t:
        return None
    if re.search(r"\b\d+\s*(h|min|mn|heures?|minutes?)\b", t) or "aujourd" in t:
        return today
    if "hier" in t:
        return today - timedelta(days=1)
    m = re.search(r"il y a (\d+)\s*(j|jours?|semaines?|mois)\b", t)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        return today - timedelta(days=n * (30 if unit == "mois" else 7 if unit.startswith("sem") else 1))
    m = re.search(r"(\d{1,2}) ([a-z]+)(?: (\d{4}))?", t)
    if m and m.group(2) in MOIS:
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            d = date(year, MOIS[m.group(2)], int(m.group(1)))
        except ValueError:
            return None
        if d > today and not m.group(3):  # « 28 décembre » vu en janvier
            d = d.replace(year=year - 1)
        return d
    return None


def _blocked(r) -> bool:
    head = r.text[:5000]
    return r.status_code in (403, 429, 503) or "cf-chl" in head or "Just a moment" in head


def _parse_list(html: str, today: date) -> list[Offer]:
    tree = LexborHTMLParser(html)
    out = []
    for div in tree.css("div.offers div.offer[data-id]"):
        try:
            link = div.css_first("a.permalink-link")
            if link is None:
                continue
            url = link.attributes.get("href") or ""
            if url.startswith("/"):
                url = BASE + url
            sid = (div.attributes.get("data-id") or "").removeprefix("ad_") or url.rsplit("/", 1)[-1]
            title_el = div.css_first(".offer-header-title")
            o = Offer(source=SOURCE, source_id=sid, url=url,
                      title=title_el.text(strip=True) if title_el else link.attributes.get("title", ""))
            for f in div.css(".offer-features .feature"):
                icon = f.css_first(".iconwrap span")
                cls = (icon.attributes.get("class") or "") if icon else ""
                txt = re.sub(r"\s+", " ", f.text(strip=True))
                if "map-marker" in cls:
                    o.city = txt
                elif "apartment" in cls:
                    o.company = txt
                elif "register" in cls:
                    o.contract = txt
                elif "banknot" in cls or "€" in txt:
                    o.salary = txt
                elif "briefcase" in cls:
                    o.extra["metier"] = txt
            d = div.css_first(".publication_date")
            pub = _parse_date(d.text(strip=True), today) if d else None
            if pub:
                o.published = pub.isoformat()
            desc = div.css_first(".description")
            if desc:
                o.description = clean_text(desc.text(separator=" ").replace("…", "").strip())
            out.append(o)
        except Exception as e:
            print(f"[jobijoba] carte ignorée : {e}")
    return out


def _keep(o: Offer, cutoff: str) -> bool:
    c = normalize(o.contract)
    t = normalize(o.title)
    if any(k in c for k in CONTRATS_KO) or "benevol" in t:
        return False
    if c and not any(k in c for k in CONTRATS_OK):
        return False
    if o.published and o.published < cutoff:
        return False
    return o.in_area()


def _detail(o: Offer, html: str, today: date) -> None:
    tree = LexborHTMLParser(html)
    el = tree.css_first("div.offer.current .permalink-description")
    if el:
        o.description = clean_text(el.text(separator="\n")) or o.description
    # La liste montre la date de remise en ligne ; la fiche, la 1re publication.
    d = tree.css_first("div.offer.current .permalink-date")
    first = _parse_date(d.text(strip=True), today) if d else None
    if first:
        o.extra["first_published"] = first.isoformat()


def fetch() -> list[Offer]:
    today = date.today()
    cutoff = (today - timedelta(days=MAX_AGE_DAYS)).isoformat()
    pages = [f"/fr/emploi/{kw}/{town}" for kw in KEYWORDS for town in TOWNS] + EXTRA_PAGES
    max_lists = MAX_REQUESTS - MAX_DETAILS
    seen: dict[str, Offer] = {}
    n_req = 0
    with client() as c:
        for path in pages[:max_lists]:
            try:
                r = c.get(BASE + path)
                n_req += 1
                if _blocked(r):
                    print(f"[jobijoba] bloqué ({r.status_code}), on arrête.")
                    return []
                if r.status_code == 200:
                    for o in _parse_list(r.text, today):
                        if o.source_id not in seen and _keep(o, cutoff):
                            seen[o.source_id] = o
            except Exception as e:
                print(f"[jobijoba] erreur sur {path} : {e}")
            polite_sleep(1)

        # Fiches : la liste ne donne qu'un extrait de description.
        # Les métiers visés d'abord, au cas où le quota de fiches serait atteint.
        todo = sorted(seen.values(), key=lambda o: not PRIORITE.search(o.title))
        for o in todo[:MAX_DETAILS]:
            if n_req >= MAX_REQUESTS:
                break
            try:
                r = c.get(o.url)
                n_req += 1
                if _blocked(r):
                    print("[jobijoba] bloqué sur les fiches, on garde les extraits.")
                    break
                if r.status_code == 200:
                    _detail(o, r.text, today)
            except Exception as e:
                print(f"[jobijoba] fiche {o.source_id} : {e}")
            polite_sleep(1)
    return list(seen.values())


if __name__ == "__main__":
    import json
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    res = fetch()
    print(len(res), "offres")
    print(json.dumps([o.to_dict() for o in res[:5]], ensure_ascii=False, indent=2))
