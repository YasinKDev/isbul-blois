"""Collecte quotidienne : sources -> dédoublonnage -> Claude -> docs/data/offers.json.

Lancement : python pipeline/run.py   (depuis la racine du projet)
Options   : --no-ai  (collecte seule, sans appeler Claude)
"""
from __future__ import annotations

import importlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

from sources.common import NEAR_CP, RADIUS_KM, fingerprint, Offer  # noqa: E402

DATA = ROOT / "docs" / "data" / "offers.json"
KEEP_DAYS = 14

# Ordre = priorité en cas de doublon (la première source gagne).
SOURCES = [
    "francetravail", "adecco", "randstad", "manpower", "synergie",
    "crit", "startpeople", "hellowork", "meteojob", "jobijoba", "indeed",
]


def collect() -> list[dict]:
    offers: list[dict] = []
    seen_fp: dict[str, dict] = {}
    for name in SOURCES:
        try:
            mod = importlib.import_module(f"sources.{name}")
        except ModuleNotFoundError:
            continue
        try:
            found: list[Offer] = mod.fetch()
        except Exception as e:  # une source cassée ne bloque pas les autres
            print(f"[{name}] échec : {e}")
            continue
        added = 0
        for o in found:
            fp = fingerprint(o)
            if fp in seen_fp:
                seen_fp[fp].setdefault("also_on", []).append({"source": o.source, "url": o.url})
                continue
            d = o.to_dict()
            d["fp"] = fp
            seen_fp[fp] = d
            offers.append(d)
            added += 1
        print(f"[{name}] {len(found)} offres, {added} nouvelles après dédoublonnage")
    return offers


def _near(o: dict) -> bool:
    d = o.get("distance_km")
    return d <= RADIUS_KM if d is not None else o.get("postal_code") in NEAR_CP


def main() -> None:
    use_ai = "--no-ai" not in sys.argv
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    today = date.today().isoformat()
    old = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {"offers": []}
    known = {o["uid"]: o for o in old["offers"]}
    known_fp = {o.get("fp") for o in old["offers"]}

    cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
    # Certaines agences gardent en ligne des offres de plusieurs mois : on les ignore.
    fresh = [o for o in collect()
             if o["uid"] not in known and o["fp"] not in known_fp
             and (o.get("published") or today) >= cutoff and _near(o)]
    for o in fresh:
        o["first_seen"] = today
    print(f"{len(fresh)} nouvelles offres aujourd'hui")

    if use_ai and fresh:
        from enrich import enrich
        verdicts = enrich(fresh)
        for o in fresh:
            if o["uid"] in verdicts:
                o["ai"] = verdicts[o["uid"]]

    merged = list(known.values()) + fresh
    # On réessaie l'analyse des offres restées sans verdict (panne, limite...).
    pending = [o for o in merged if "ai" not in o]
    if use_ai and pending and len(pending) != len(fresh):
        from enrich import enrich
        verdicts = enrich([o for o in pending if o not in fresh])
        for o in pending:
            if o["uid"] in verdicts:
                o["ai"] = verdicts[o["uid"]]

    merged = [o for o in merged if o.get("first_seen", today) >= cutoff and _near(o)]
    merged.sort(key=lambda o: (o.get("first_seen", ""), (o.get("ai") or {}).get("score", 0)), reverse=True)

    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps({
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "offers": merged,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = sum(1 for o in merged if (o.get("ai") or {}).get("suitable"))
    print(f"Écrit {DATA.relative_to(ROOT)} : {len(merged)} offres, {ok} adaptées")


if __name__ == "__main__":
    main()
