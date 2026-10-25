"""Tri et traduction des offres par Claude.

Pour chaque offre : est-ce qu'elle convient au profil (profile.md) ? Si oui, un
résumé très simple en turc pour l'appli, et un petit message de candidature en
français prêt à copier.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

MODEL = "claude-opus-5-5"
BATCH = 8  # offres par requête
WORKERS = 6  # requêtes simultanées

# Le profil (parcours, situation) reste privé : fichier profile.md non versionné
# en local, secret ISBUL_PROFILE sur GitHub.
_profile_file = Path(__file__).resolve().parent.parent / "profile.md"
PROFILE = os.environ.get("ISBUL_PROFILE") or (_profile_file.read_text(encoding="utf-8") if _profile_file.exists() else "")


class Verdict(BaseModel):
    uid: str
    suitable: bool
    score: int = Field(ge=0, le=100)
    reason_fr: str = Field(description="Une phrase : pourquoi elle convient ou pas")
    category: Literal["paketleme", "depo", "ayiklama", "uretim", "temizlik", "tarim", "diger"]
    french_level: Literal["az", "orta", "cok"] = Field(description="Français nécessaire : az = peu, orta = moyen, cok = beaucoup")
    title_tr: str
    summary_tr: str = Field(description="2 phrases courtes : ce qu'on fait et où")
    tasks_tr: list[str] = Field(description="3 tâches maximum, très courtes")
    schedule_tr: str = Field(description="Horaires / temps de travail")
    salary_tr: str
    duration_tr: str = Field(description="Durée de la mission ou du contrat")
    apply_message_fr: str


class Batch(BaseModel):
    verdicts: list[Verdict]


def _brief(o: dict) -> dict:
    """Ce qu'on envoie à Claude pour une offre (sans les champs inutiles)."""
    keep = ("uid", "title", "company", "city", "contract", "duration", "salary", "hours", "description", "extra")
    d = {k: o[k] for k in keep if o.get(k)}
    if o.get("distance_km") is not None:
        d["distance_km"] = o["distance_km"]
    return d


def _ask(client: anthropic.Anthropic, offers: list[dict]) -> list[Verdict]:
    payload = json.dumps([_brief(o) for o in offers], ensure_ascii=False)
    response = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low"},
        system=[{"type": "text", "text": PROFILE, "cache_control": {"type": "ephemeral"}}],
        messages=[{
            "role": "user",
            "content": "Voici les offres (JSON). Rends exactement un verdict par offre, avec le même uid.\n\n" + payload,
        }],
        output_format=Batch,
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        print(f"[enrich] pas de réponse exploitable (stop_reason={response.stop_reason})")
        return []
    return response.parsed_output.verdicts


def enrich(offers: list[dict]) -> dict[str, dict]:
    """Renvoie {uid: verdict} pour les offres analysées."""
    if not PROFILE:
        raise SystemExit("[enrich] profil introuvable : créer profile.md ou définir ISBUL_PROFILE")
    client = anthropic.Anthropic()
    out: dict[str, dict] = {}
    chunks = [offers[i:i + BATCH] for i in range(0, len(offers), BATCH)]

    def run(chunk: list[dict]) -> list[Verdict]:
        try:
            return _ask(client, chunk)
        except anthropic.RateLimitError:
            print("[enrich] limite de débit atteinte, lot reporté à demain")
        except anthropic.APIStatusError as e:
            print(f"[enrich] erreur API {e.status_code} : {e.message}")
        except anthropic.APIConnectionError:
            print("[enrich] problème réseau, lot ignoré")
        return []

    done = 0
    # Plusieurs lots en parallèle : la première collecte compte des centaines d'offres.
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(run, c): c for c in chunks}
        for fut in as_completed(futures):
            chunk = futures[fut]
            uids = {o["uid"] for o in chunk}
            for v in fut.result():
                if v.uid in uids:
                    out[v.uid] = v.model_dump()
            done += len(chunk)
            print(f"[enrich] {done}/{len(offers)} offres analysées")
    return out
