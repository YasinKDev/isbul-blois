"""Offres Crit autour de Blois (crit-job.com).

La page /offres?localisationVille=Blois&localisationRayon=25 est rendue par
React Router : les données du loader (offres complètes avec description et
coordonnées) sont dans le flux « turbo-stream » injecté dans la page. On le
décode, pas besoin des pages détail. robots.txt : tout est autorisé.
"""
from __future__ import annotations

import json
import re

try:
    from .common import RADIUS_KM, Offer, clean_text, client, polite_sleep
except ImportError:
    from common import RADIUS_KM, Offer, clean_text, client, polite_sleep

SOURCE = "crit"
BASE = "https://www.crit-job.com"
LIST_URL = BASE + "/offres/"
PER_PAGE = 20
MAX_PAGES = 10

# Valeurs spéciales du format turbo-stream (indices négatifs).
_SPECIAL = {-1: None, -2: None, -3: None, -4: 0, -5: None, -6: None, -7: None}


def _stream(html: str) -> list:
    """Concatène les morceaux passés à streamController.enqueue(...)."""
    dec, s = json.JSONDecoder(), ""
    for m in re.finditer(r"streamController\.enqueue\(", html):
        try:
            v, _ = dec.raw_decode(html, m.end())
        except ValueError:
            continue
        if isinstance(v, str):
            s += v
    for line in s.splitlines():
        line = line.strip()
        if line.startswith("["):
            return json.loads(line)
    return []


def _decode(arr: list):
    """Décodage minimal turbo-stream : objets {"_i": j}, listes d'indices."""
    cache: dict[int, object] = {}

    def val(i):
        if isinstance(i, int) and i < 0:
            return _SPECIAL.get(i)
        if i in cache:
            return cache[i]
        x = arr[i]
        if isinstance(x, dict):
            o: dict = {}
            cache[i] = o
            for k, v in x.items():
                o[arr[int(k[1:])]] = val(v)
            return o
        if isinstance(x, list):
            if x and isinstance(x[0], str) and len(x[0]) == 1:  # Date, Promise, ...
                return x[1] if x[0] == "D" and len(x) > 1 else None
            o = []
            cache[i] = o
            o.extend(val(j) for j in x)
            return o
        return x

    return val(0) if arr else {}


def _salary(r: dict) -> str:
    if not r or r.get("minimum") is None:
        return ""
    per = {"HEURE": "heure", "MOIS": "mois", "ANNEE": "an", "AN": "an", "JOUR": "jour"}.get(
        str(r.get("periodicite", "")).upper(), str(r.get("periodicite", "")).lower())
    fmt = lambda x: f"{x:g}".replace(".", ",")
    lo, hi = r["minimum"], r.get("maximum") or r["minimum"]
    s = f"{fmt(lo)} €" if lo == hi else f"{fmt(lo)} à {fmt(hi)} €"
    return f"{s} / {per}" if per else s


def _to_offer(d: dict) -> Offer:
    info, poste = d.get("informations") or {}, d.get("poste") or {}
    loc = d.get("localisation") or {}
    pt = loc.get("point") or {}
    desc = poste.get("descriptionPoste") or ""
    m = re.search(r"[^\n]*\bhoraires?\b[^\n]*", desc, re.I)
    rem = d.get("remuneration") or {}
    extra = {k: v for k, v in {
        "debut": poste.get("dateEmbauche"),
        "secteur": poste.get("secteurActivite"),
        "fonction": poste.get("fonction"),
        "avantages": rem.get("avantages"),
    }.items() if v}
    return Offer(
        source=SOURCE,
        source_id=d["idOffre"],
        title=re.sub(r"\s+", " ", info.get("intitule", "")).strip(),
        url=f"{BASE}/offres/{d['idOffre']}",
        company="Crit",
        city=loc.get("nomVille", ""),
        postal_code=loc.get("cpVille", ""),
        lat=pt.get("lat"),
        lon=pt.get("lon"),
        contract=poste.get("typeContrat", ""),
        duration=poste.get("dureeContrat") or "",
        salary=_salary(rem),
        hours=(m[0].strip()[:200] if m else "") or poste.get("typeEmploi") or "",
        description=clean_text(desc),
        published=(info.get("dateDiffusion") or "")[:10],
        extra=extra,
    )


def fetch() -> list[Offer]:
    offers: dict[str, Offer] = {}
    with client() as c:
        for page in range(1, MAX_PAGES + 1):
            if page > 1:
                polite_sleep(1)
            try:
                r = c.get(LIST_URL, params={"page": page, "localisationVille": "Blois",
                                            "localisationRayon": RADIUS_KM})
            except Exception as e:
                print(f"[crit] erreur réseau : {e}")
                break
            head = r.text[:5000].lower()
            if r.status_code in (403, 429) or "cf-chl" in head or "captcha" in head:
                print(f"[crit] bloqué (HTTP {r.status_code}), source ignorée")
                return []
            if r.status_code != 200:
                print(f"[crit] HTTP {r.status_code} sur la page {page}")
                break
            try:
                data = _decode(_stream(r.text))["loaderData"]["routes/offres"]
            except Exception as e:
                print(f"[crit] données de page illisibles : {e}")
                break
            if data.get("maintenanceActive"):
                print("[crit] site en maintenance")
                break
            items = data.get("offres") or []
            for d in items:
                try:
                    o = _to_offer(d)
                    if o.in_area():
                        offers.setdefault(o.source_id, o)
                except Exception as e:
                    print(f"[crit] offre ignorée : {e}")
            total = data.get("totalOffres") or 0
            if not items or page * PER_PAGE >= total:
                break
    return sorted(offers.values(), key=lambda o: o.published, reverse=True)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    res = fetch()
    print(len(res), "offres")
    print(json.dumps([o.to_dict() for o in res[:5]], ensure_ascii=False, indent=2))
