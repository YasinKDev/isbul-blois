"""Source Indeed (fr.indeed.com) — désactivée.

Constat (oct. 2026) :
- /jobs?q=...&l=Blois et /rss?... répondent 403 (protection anti-robot) ;
- les pages SEO /q-...-l-...-emplois.html renvoient une page « Security Check »
  et sont de toute façon interdites par robots.txt (Disallow: /q-, /l-, /rss, /viewjob) ;
- l'API « Publisher » (api.indeed.com/ads/apisearch) est fermée aux nouveaux
  comptes depuis des années.
Il n'existe donc pas d'accès public légitime sans contourner la protection, ce
qu'on ne fait pas. fetch() vérifie juste une fois si le blocage est levé.
"""
from __future__ import annotations

try:
    from .common import Offer, client
except ImportError:  # lancé comme script
    from common import Offer, client

SOURCE = "indeed"
PROBE_URL = "https://fr.indeed.com/jobs?q=manutentionnaire&l=Blois+(41)&fromage=7"


def fetch() -> list[Offer]:
    try:
        with client() as c:
            r = c.get(PROBE_URL)
        status = r.status_code
        challenge = "Security Check" in r.text[:20000] or "cf-chl" in r.text[:20000]
    except Exception as e:
        status, challenge = f"erreur {e}", True
    if status == 200 and not challenge:
        print("[indeed] la recherche répond 200 : un vrai parseur pourrait être écrit (non implémenté).")
    else:
        print(f"[indeed] source désactivée : accès bloqué par Indeed ({status}), pas de contournement.")
    return []


if __name__ == "__main__":
    import json
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    res = fetch()
    print(len(res), "offres")
    print(json.dumps([o.to_dict() for o in res[:5]], ensure_ascii=False, indent=2))
