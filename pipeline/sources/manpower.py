"""Offres Manpower autour de Blois (manpower.fr).

La liste /offre-emploi/blois est rendue côté serveur (Angular SSR) : on lit les
cartes d'offres, puis la page détail pour la description. robots.txt autorise
ces pages mais demande Crawl-delay: 10, on attend donc 10 s entre requêtes.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from selectolax.lexbor import LexborHTMLParser

try:
    from .common import Offer, clean_text, client, polite_sleep
except ImportError:
    from common import Offer, clean_text, client, polite_sleep

SOURCE = "manpower"
BASE = "https://www.manpower.fr"
LIST_URL = BASE + "/offre-emploi/blois"
MAX_PAGES = 5        # 10 offres par page
MAX_DETAILS = 30     # pages détail par exécution
MAX_AGE_DAYS = 10    # on ne descend pas plus loin dans la liste triée par date
DELAY = 10           # Crawl-delay de robots.txt


def _txt(node, sel: str) -> str:
    n = node.css_first(sel)
    return re.sub(r"\s+", " ", n.text()).strip() if n else ""


def _iso(d: str) -> str:
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", d or "")
    return f"{m[3]}-{m[2]}-{m[1]}" if m else ""


def _blocked(r) -> bool:
    return r.status_code in (403, 429, 503) or "cf-chl" in r.text[:5000] or "captcha" in r.text[:5000].lower()


def _parse_list(html: str) -> list[Offer]:
    offers = []
    for card in LexborHTMLParser(html).css("app-offer-list-card"):
        try:
            a = card.css_first("h4 a")
            m = re.search(r"/offers/details/(\d+)", a.attributes.get("href", "")) if a else None
            if not m:
                continue
            loc = _txt(card, ".icone-localisation")
            lm = re.match(r"(\d{5})\s+(.*)", loc)
            cp, city = (lm[1], lm[2]) if lm else ("", loc)
            offers.append(Offer(
                source=SOURCE,
                source_id=m[1],
                title=re.sub(r"\s+", " ", a.text()).strip(),
                url=f"{BASE}/offers/details/{m[1]}",
                company="Manpower",
                city=city,
                postal_code=cp,
                contract=_txt(card, ".icone-contrat"),
                duration=_txt(card, ".icone-duree"),
                salary=_txt(card, ".icone-salaire"),
                published=_iso(_txt(card, ".publier_")),
                extra={k: v for k, v in {
                    "debut": _txt(card, ".icone-disponibilite"),
                    "experience": _txt(card, ".icone-experience"),
                    "postes": _txt(card, ".icone-number"),
                    "secteur": _txt(card, ".icone-secteur"),
                }.items() if v},
            ))
        except Exception as e:  # une carte cassée ne doit pas tout arrêter
            print(f"[manpower] carte ignorée : {e}")
    return offers


def _fill_detail(o: Offer, html: str) -> None:
    tree = LexborHTMLParser(html)
    body = tree.css_first("app-offer-details-content div.col-lg-8")
    if body:
        for n in body.css("a.btn"):  # bouton « Retour à ma recherche »
            n.decompose()
        o.description = clean_text(body.text(separator="\n"))
    # Agence qui publie : « Publiée par l'agence : Blois ... »
    agence = tree.css_first("app-offer-details-content .col-lg-4")
    if agence:
        lines = [l.strip() for l in agence.text(separator="\n").splitlines() if l.strip()]
        for i, l in enumerate(lines):
            if l.startswith("Publiée par") and i + 1 < len(lines):
                o.company = f"Manpower {lines[i + 1]}"
                break
        tel = re.search(r"0\d(?:\s?\d{2}){4}", agence.text())
        if tel:
            o.phone = tel[0]


def fetch() -> list[Offer]:
    found: dict[str, Offer] = {}
    oldest = (date.today() - timedelta(days=MAX_AGE_DAYS)).isoformat()
    with client() as c:
        for page in range(1, MAX_PAGES + 1):
            try:
                r = c.get(LIST_URL, params={"page": page, "sort": "date"})
            except Exception as e:
                print(f"[manpower] erreur réseau : {e}")
                break
            if _blocked(r):
                print(f"[manpower] bloqué (HTTP {r.status_code}), source ignorée")
                return []
            if r.status_code != 200:
                print(f"[manpower] HTTP {r.status_code} sur la page {page}")
                break
            batch = _parse_list(r.text)
            for o in batch:
                found.setdefault(o.source_id, o)
            if len(batch) < 10 or any(o.published and o.published < oldest for o in batch):
                break
            polite_sleep(DELAY)

        offers = [o for o in found.values() if o.in_area()]
        for o in offers[:MAX_DETAILS]:
            polite_sleep(DELAY)
            try:
                r = c.get(o.url)
                if _blocked(r):
                    print(f"[manpower] bloqué sur le détail (HTTP {r.status_code})")
                    break
                if r.status_code == 200:
                    _fill_detail(o, r.text)
            except Exception as e:
                print(f"[manpower] détail {o.source_id} ignoré : {e}")
    return offers


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    res = fetch()
    print(len(res), "offres")
    print(json.dumps([o.to_dict() for o in res[:5]], ensure_ascii=False, indent=2))
