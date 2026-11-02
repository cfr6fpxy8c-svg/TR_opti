# TR_opti — Scanner BTC 15 minutes

[![scanner](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fcfr6fpxy8c-svg%2FTR_opti%2Fmain%2Ffrontend%2Fdata%2Fbadge.json)](https://github.com/cfr6fpxy8c-svg/TR_opti/actions/workflows/scanner.yml)
[![Tests](https://github.com/cfr6fpxy8c-svg/TR_opti/actions/workflows/tests.yml/badge.svg)](https://github.com/cfr6fpxy8c-svg/TR_opti/actions/workflows/tests.yml)

Scanner d'analyse du Bitcoin (BTCUSDT perpétuel). Toutes les 15 minutes, il analyse le marché sur
1D / 4H / 1H / 15M, calcule un score pondéré sur 100 et publie une **proposition d'entrée**
(sens, déclencheur, stop, objectif, gain/risque, palier, % de réussite historique du palier).
Chaque proposition est suivie jusqu'à son résultat. **Aucun ordre n'est passé.**

## Documentation

- [Spécifications fonctionnelles](docs/SPECIFICATIONS-FONCTIONNELLES.md) — règles métier, score, paliers, cycle de vie, dashboard.
- [Spécifications techniques](docs/SPECIFICATIONS-TECHNIQUES.md) — architecture, modules, configuration, CI/CD, sécurité, exploitation.

## Démarrage rapide

```bash
python -m pip install --require-hashes -r requirements.txt   # outils QA (le moteur n'a aucune dépendance)
python -m pytest tests/ -q                                    # suite de tests hors ligne
python -m scanner.main --symbols BTCUSDT                      # scan local (écrit data/ et frontend/data/)
python -m scanner.main --symbols BTCUSDT --dry-run            # scan sans écriture
python -m scanner.replay --symbols BTCUSDT --candles 2880     # replay des 30 derniers jours
python -m scanner.validate_data                               # validation des JSON publiés
cd frontend && python -m http.server 8080                     # dashboard sur http://localhost:8080
```

## Fonctionnement en production

- **Planification** : cron-job.org déclenche `.github/workflows/scanner.yml` (API `workflow_dispatch`,
  `source=cron`) à **:00, :15, :30 et :45 UTC**.
- **Scan** : job en lecture seule → données validées → job de publication qui commite uniquement les
  fichiers de données autorisés.
- **Qualité et sécurité** : `.github/workflows/tests.yml` et `.github/workflows/security.yml`.
- **Configuration** : tous les paramètres sont dans `config/strategy.json` (mode `weighted` actif).
- **Dashboard** : `frontend/index.html`, servi en statique ; les données sont dans `frontend/data/`.

## Avertissement

Outil d'analyse uniquement. Ce n'est pas un conseil en investissement et aucun résultat n'est garanti.
Le score classe la qualité d'une configuration ; le % affiché est un taux historique mesuré, avec la
taille d'échantillon. Le trading de produits dérivés crypto comporte un risque élevé de perte.

## Licence

MIT — voir [LICENSE](LICENSE).
