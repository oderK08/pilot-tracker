# Pilot Tracker

Simule un portefeuille virtuel qui copie les transactions publiques d'un
"pilote" (politicien via STOCK Act, ou hedge fund via formulaires 13F),
sans connecter de vrai compte de courtage — uniquement de la simulation à
partir de données publiques + prix historiques réels.

## Statut actuel

✅ `data_sources/price_data.py` — prix historiques via Stooq, testé et fiable
⚠️ `data_sources/congress_trades.py` — transactions du Congrès, logique
   interne testée mais **API réelle jamais testée en conditions réelles**
   (domaine inaccessible depuis l'environnement de développement d'origine)

À venir une fois `congress_trades.py` validé :
- `data_sources/hedge_fund_13f.py` (positions institutionnelles via SEC EDGAR)
- `simulation/portfolio_simulator.py` (moteur de simulation)
- `generate_report.py` (script principal, graphiques de performance + historique)

## Tester `congress_trades.py` en isolation

Avant de construire quoi que ce soit par-dessus, vérifie que ce module
fonctionne réellement :

```bash
pip install -r requirements.txt
python data_sources/congress_trades.py
```

Si ça échoue, le message d'erreur inclut un extrait de la réponse brute de
l'API pour faciliter le diagnostic et l'ajustement du code.

## Usage prévu (une fois complet)

```bash
python generate_report.py --pilot congress --name "Nancy Pelosi"
python generate_report.py --pilot hedge_fund --name "Bill Ackman"
```
