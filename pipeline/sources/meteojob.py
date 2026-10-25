"""Source Meteojob (meteojob.com).

robots.txt interdit les URL avec « ? » sauf /jobs?* (autorisé explicitement).
La page de résultats /jobs?what=...&where=Blois (41) est rendue côté serveur
(Angular) et contient tout l'état JSON dans <script id="candidate-front-state">
(clé "app:search:offers") : titre, entreprise, lat/lon, contrat, salaire et
description complète. Pas besoin d'ouvrir les fiches.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlencode

try:
    from .common import Offer, clean_text, client, polite_sleep
except ImportError:  # lancé comme script
    from common import Offer, clean_text, client, polite_sleep

SOURCE = "meteojob"
BASE = "https://www.meteojob.com"
WHERE = "Blois (41)"

# Métiers recherchés (usine, entrepôt, tri, emballage).
KEYWORDS = [
    "manutentionnaire",
    "préparateur de commandes",
    "agent de production",
    "opérateur de production",
    "conditionnement",
    "emballage",
    "magasinier",
    "logistique",
    "agent de tri",
    "ouvrier",
]
CONTRATS_OK = {"INTERIM", "CDD", "SEASONAL", "EXTRA"}
MAX_AGE_DAYS = 7
MAX_PAGES = 3      # par mot-clé
MAX_REQUESTS = 45
PAGE_SIZE = 20

STATE_RE = re.compile(r'<script id="candidate-front-state"[^>]*>(.*?)</script>', re.S)


def _blocked(r) -> bool:
    head = r.text[:5000]
    return r.status_code in (403, 429, 503) or "cf-chl" in head or "Just a moment" in head


def _state(html: str) -> dict:
    m = STATE_RE.search(html)
    if not m:
        return {}
    return json.loads(m.group(1)).get("app:search:offers") or {}


def _salary(it: dict) -> str:
    label = ((it.get("labels") or {}).get("salary") or {}).get("value", "")
    s = it.get("salary") or {}
    lo, hi = s.get("from"), s.get("to")
    # Certaines agences mettent un maximum bidon (« 12 € - 10 012 € »).
    if lo and hi and hi > lo * 50:
        return f"À partir de {lo:g} €"
    return label


def _to_offer(it: dict) -> Offer:
    labels = it.get("labels") or {}
    loc = (it.get("locations") or [{}])[0]
    city = loc.get("admin3Label") or re.sub(r"\s*\(.*", "", loc.get("name", ""))
    contract = ", ".join(c.get("value", "") for c in labels.get("contractTypeList", [])) \
        or ", ".join(it.get("contractTypes", []))
    hours = ", ".join(
        [j.get("value", "") for j in labels.get("jobTypeList", [])]
        + [s.get("value", "") for s in labels.get("shiftWorkList", [])]
    )
    parts = [it.get("description", ""), it.get("profileDescription", ""), it.get("benefits", "")]
    desc = clean_text("\n".join(re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", p or "") for p in parts))
    url = BASE + ((it.get("url") or {}).get("jobOffer") or f"/jobs/{it['id']}")
    return Offer(
        source=SOURCE,
        source_id=str(it["id"]),
        title=it.get("title", "").strip(),
        url=url,
        company=(it.get("company") or {}).get("name") or it.get("recruiter", ""),
        city=city,
        postal_code=loc.get("admin3Code", "") or loc.get("admin2Code", ""),
        lat=loc.get("lat"),
        lon=loc.get("lon"),
        contract=contract,
        salary=_salary(it),
        hours=hours,
        description=desc,
        published=(it.get("publicationDate") or "")[:10],
        extra={"jobs": [j.get("label") for j in it.get("jobs", [])]},
    )


def fetch() -> list[Offer]:
    seen: dict[str, Offer] = {}
    n_req = 0
    with client() as c:
        for kw in KEYWORDS:
            for page in range(1, MAX_PAGES + 1):
                if n_req >= MAX_REQUESTS:
                    break
                params = {"what": kw, "where": WHERE, "sorting": "DATE",
                          "publishedSinceDays": MAX_AGE_DAYS}
                if page > 1:
                    params["page"] = page
                try:
                    r = c.get(f"{BASE}/jobs?{urlencode(params)}")
                    n_req += 1
                    if _blocked(r):
                        print(f"[meteojob] bloqué ({r.status_code}), on arrête.")
                        return list(seen.values())
                    st = _state(r.text)
                except Exception as e:
                    print(f"[meteojob] erreur « {kw} » p{page} : {e}")
                    polite_sleep(1)
                    break
                items = st.get("content") or []
                for it in items:
                    try:
                        if str(it.get("id")) in seen:
                            continue
                        if not CONTRATS_OK.intersection(it.get("contractTypes") or []):
                            continue
                        o = _to_offer(it)
                        if o.in_area():
                            seen[o.source_id] = o
                    except Exception as e:
                        print(f"[meteojob] offre ignorée : {e}")
                polite_sleep(1)
                if len(items) < PAGE_SIZE or page * PAGE_SIZE >= (st.get("total") or 0):
                    break
    return list(seen.values())


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    res = fetch()
    print(len(res), "offres")
    print(json.dumps([o.to_dict() for o in res[:5]], ensure_ascii=False, indent=2))
