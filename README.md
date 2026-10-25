# İş Bul · Blois

Petite web app en turc qui regroupe chaque jour les offres d'intérim et de CDD
sans diplôme (usine, logistique, tri, conditionnement…) autour de Blois.

- `pipeline/` : collecte (France Travail, agences d'intérim, jobboards), puis tri
  et traduction en turc par Claude. Lancement : `python pipeline/run.py`.
- `docs/` : l'appli (PWA servie par GitHub Pages) et `docs/data/offers.json`.
- `.github/workflows/daily.yml` : collecte automatique deux fois par jour.

Secrets nécessaires (Settings → Secrets → Actions) : `ANTHROPIC_API_KEY`,
`ISBUL_PROFILE` (le profil recherché, gardé privé). En local : `.env` et
`profile.md`, tous deux ignorés par Git.
