"""Offres Synergie autour de Blois (synergie.fr).

La recherche (/rechercher-un-emploi) est interdite par robots.txt et la
pagination des pages département se fait côté client. On passe donc par le
sitemap des offres (autorisé, ~5 fichiers) : l'URL contient le code postal, on
garde le Loir-et-Cher, puis on lit chaque page détail (JSON-LD JobPosting +
bloc « Détails de l'offre »).
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

SOURCE = "synergie"
BASE = "https://www.synergie.fr"
SITEMAP = BASE + "/sitemaps/jobs/sitemap.xml"
MAX_SITEMAPS = 8
MAX_DETAILS = 40
MAX_AGE_DAYS = 30    # lastmod du sitemap plus ancien : on ne regarde pas
# Codes postaux à moins de ~25 km de Blois, traités en priorité.
NEAR_CP = {"41000", "41350", "41260", "41330", "41190", "41120", "41150",
           "41500", "41700", "41250", "41220", "41310"}

URL_RE = re.compile(r"/trouver-un-emploi/[^<\s]*-(41\d{3})/offre-(\d+-\d+)")


def _blocked(r) -> bool:
    head = r.text[:5000].lower()
    return r.status_code in (403, 429) or "cf-chl" in head or "captcha" in head


def _sitemap_urls(c) -> list[tuple[str, str, str, str]]:
    """(url, code postal, id, lastmod) des offres du 41 trouvées dans le sitemap."""
    r = c.get(SITEMAP)
    if _blocked(r):
        raise PermissionError(f"HTTP {r.status_code}")
    pages = re.findall(r"<loc>([^<]+sitemap\.xml\?page=\d+)</loc>", r.text) or [SITEMAP]
    oldest = (date.today() - timedelta(days=MAX_AGE_DAYS)).isoformat()
    out = []
    for p in pages[:MAX_SITEMAPS]:
        polite_sleep(1)
        r = c.get(p.replace("&amp;", "&"))
        if _blocked(r):
            raise PermissionError(f"HTTP {r.status_code}")
        for m in re.finditer(r"<loc>([^<]+)</loc>\s*<lastmod>([^<]+)</lastmod>", r.text):
            u = URL_RE.search(m[1])
            if u and m[2][:10] >= oldest:
                out.append((m[1], u[1], u[2], m[2][:10]))
    return out


def _details(tree) -> dict[str, str]:
    """Bloc « Détails de l'offre » : libellé -> valeur."""
    lines = []
    for h in tree.css("h2, h3, p, dt, dd, span, div"):
        if h.text(deep=False).strip() == "Détails de l'offre":
            box = h.parent
            lines = [l.strip() for l in box.text(separator="\n").splitlines() if l.strip()]
            break
    keys = ("Date de publication", "Lieu de la mission", "Type de contrat", "Salaire",
            "Période de disponibilité", "Métier", "Durée", "Horaires", "Temps de travail")
    d = {}
    for i, l in enumerate(lines[:-1]):
        if l in keys and l not in d:
            d[l] = lines[i + 1]
    return d


def _parse_detail(html: str, url: str, sid: str, cp: str) -> Offer:
    tree = LexborHTMLParser(html)
    ld = {}
    for s in tree.css('script[type="application/ld+json"]'):
        try:
            j = json.loads(s.text())
        except Exception:
            continue
        if isinstance(j, dict) and j.get("@type") == "JobPosting":
            ld = j
            break
    det = _details(tree)
    h1 = tree.css_first("h1")
    title = h1.text().strip() if h1 else ld.get("title", "")
    addr = (ld.get("jobLocation") or {}).get("address") or {}
    lieu = det.get("Lieu de la mission", "")
    city = addr.get("addressLocality") or lieu.split(" - ")[0]
    # Sections de texte : descriptif, profil, client
    parts = []
    for sec in tree.css("section"):
        h2 = sec.css_first("h2")
        if h2 and h2.text().strip() in ("Descriptif du poste", "Profil recherché", "A propos de notre client"):
            parts.append(sec.text(separator="\n"))
    desc = clean_text("\n".join(parts) or ld.get("description", ""))
    published = ld.get("datePosted", "")[:10]
    if not published:
        m = re.search(r"(\d{2})/(\d{2})/(\d{4})", det.get("Date de publication", ""))
        published = f"{m[3]}-{m[2]}-{m[1]}" if m else ""
    tel = re.search(r"Appelez l'agence \(([\d\s]+)\)", tree.body.text() if tree.body else "")
    agence = ""
    for n in tree.css("h3, h4, p"):
        t = n.text(deep=False).strip()
        if t.startswith(("Agence Synergie ", "Synergie ")) and len(t) < 80:
            agence = t
            break
    hours = det.get("Horaires") or det.get("Temps de travail", "")
    if not hours:
        m = re.search(r"[^.\n]*\b(horaires?|\d+h\d*\s*(?:-|à)\s*\d+h)[^.\n]*", desc, re.I)
        hours = m[0].strip()[:200] if m else ""
    return Offer(
        source=SOURCE,
        source_id=sid,
        title=re.sub(r"\s+", " ", title),
        url=url,
        company=agence or "Synergie",
        city=city,
        postal_code=addr.get("postalCode") or cp,
        contract=det.get("Type de contrat", ""),
        duration=det.get("Période de disponibilité") or det.get("Durée", ""),
        salary="" if "non renseign" in det.get("Salaire", "") else det.get("Salaire", ""),
        hours=hours,
        description=desc,
        published=published,
        phone=re.sub(r"\s+", " ", tel[1]).strip() if tel else "",
        extra={k: v for k, v in {"metier": det.get("Métier", ""),
                                  "secteur": ld.get("industry", ""),
                                  "fin": ld.get("validThrough", "")}.items() if v},
    )


def fetch() -> list[Offer]:
    offers: list[Offer] = []
    with client() as c:
        try:
            cands = _sitemap_urls(c)
        except PermissionError as e:
            print(f"[synergie] bloqué ({e}), source ignorée")
            return []
        except Exception as e:
            print(f"[synergie] sitemap illisible : {e}")
            return []
        # Proches de Blois d'abord, puis les plus récemment modifiées.
        cands = {sid: (u, cp, sid, lm) for u, cp, sid, lm in cands}.values()
        cands = sorted(cands, key=lambda x: (x[1] in NEAR_CP, x[3]), reverse=True)
        for url, cp, sid, _ in cands[:MAX_DETAILS]:
            polite_sleep(1)
            try:
                r = c.get(url)
                if _blocked(r):
                    print(f"[synergie] bloqué sur le détail (HTTP {r.status_code})")
                    break
                if r.status_code != 200:
                    continue  # offre retirée entre-temps
                o = _parse_detail(r.text, url, sid, cp)
                if o.in_area():
                    offers.append(o)
            except Exception as e:
                print(f"[synergie] offre {sid} ignorée : {e}")
    offers.sort(key=lambda o: o.published, reverse=True)
    return offers


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    res = fetch()
    print(len(res), "offres")
    print(json.dumps([o.to_dict() for o in res[:5]], ensure_ascii=False, indent=2))
