# Pilot Tracker

Ce que détiennent réellement les hedge funds et les politiciens américains,
à partir de leurs seules déclarations publiques — aucune connexion à un
compte de courtage, uniquement des données publiques et des prix
historiques réels.

## Deux sources, deux écrans

Les deux sources ne disent pas la même chose, donc le projet ne prétend pas
en tirer la même chose.

| | 🏦 Hedge fund (13F) | 🏛️ Politicien (STOCK Act) |
|---|---|---|
| Ce qui est déclaré | des **positions** (photo trimestrielle) | des **transactions** datées |
| Précision | nombre de titres exact | fourchette de montant seulement |
| Ce qu'on en tire | positions, poids, variations d'un trimestre à l'autre | reconstitution du portefeuille + performance vs S&P 500 |
| Performance calculée | **non** — voir ci-dessous | oui, avec l'imprécision des fourchettes |

### Pourquoi aucune performance sur les 13F

Un 13F ne contient ni le cash, ni les positions non-US, ni les ventes à
découvert, ni le moindre mouvement à l'intérieur du trimestre, et arrive
avec jusqu'à 45 jours de retard. Une version précédente de ce projet en
tirait une courbe de performance quotidienne, en interpolant linéairement
les positions entre deux trimestres. C'était de l'invention : la source ne
dit rien de ce qui s'est passé entre deux photos. La vue 13F montre donc ce
que cette source sait réellement dire — des positions et leurs variations.

### Le piège que la vue 13F existe pour éviter

Le poids d'une position bouge pour deux raisons très différentes :

- le gérant a acheté ou vendu → une **décision** (effet flux)
- le cours a monté ou baissé → le **marché** (effet prix)

« Micron est passé de 6,8 % à 10,2 % » mélange les deux sans le dire. Le
tableau sépare systématiquement **Δ titres %** (la décision) de **Δ poids**
(le résultat), et affiche **Δ prix %** pour expliquer l'écart. Une position
dont le poids grimpe avec un Δ titres nul, c'est le cours qui a monté — pas
une conviction renforcée.

## Les trois pièges du format 13F

Ils ont chacun coûté un bug silencieux à ce projet, et sont désormais
verrouillés par des tests (`tests/test_13f_parsing.py`) :

1. **Une position = plusieurs lignes.** Un gérant déclarant pour plusieurs
   sous-gérants (`otherManager`) éclate la même position sur autant de
   lignes. Il faut les additionner ; n'en garder qu'une donnait Apple figé
   à 692 000 titres chez Berkshire au lieu de ~887 millions.
2. **L'unité de la colonne `value` a changé.** Milliers de dollars jusqu'aux
   dépôts de fin 2022, dollars entiers à partir du 3 janvier 2023. Un
   facteur 1000 appliqué systématiquement affichait Berkshire à
   48 000 milliards de dollars.
3. **`putCall` change le sens d'une ligne.** Une ligne peut être un put ou
   un call, et le nombre déclaré est alors le **notionnel du sous-jacent**,
   pas la prime engagée. Ignorer ce champ affichait les paris baissiers de
   Scion Asset Management (Michael Burry) comme des convictions haussières.

## Structure

```
analysis/holdings_view.py     vue positions/variations trimestrielles (13F)
core.py                       logique partagée CLI <-> Streamlit
data_sources/
  hedge_fund_13f.py           dépôts 13F via SEC EDGAR (couverture + positions)
  congress_trades.py          transactions du Congrès (STOCK Act)
  cusip_resolver.py           CUSIP -> ticker (via Fails-to-Deliver SEC)
  price_data.py               prix historiques
  archive.py                  archive persistante committée dans le dépôt
simulation/portfolio_simulator.py   reconstitution de portefeuille (Congrès uniquement)
streamlit_app.py              application interactive
generate_report.py            rapports statiques (PNG/CSV)
update_archive.py             mise à jour quotidienne de l'archive (GitHub Actions)
```

## Usage

```bash
pip install -r requirements.txt
export EDGAR_USER_AGENT="Prénom Nom email@exemple.com"   # exigé par la SEC

streamlit run streamlit_app.py

python generate_report.py --pilot hedge_fund --name "Scion Asset Management" --quarters 8
python generate_report.py --pilot congress --name "Nancy Pelosi"
```

Pour un hedge fund, `generate_report.py` produit dans `output/` :

| Fichier | Contenu |
|---|---|
| `<nom>_13F.pdf` | **le rapport lisible** : tableau des positions détenues avec leurs variations trimestrielles, puis tableau des positions liquidées. Format paysage, en-têtes répétés, aucun graphique |
| `<nom>_positions.csv` | les mêmes positions, pour retraitement |
| `<nom>_sorties.csv` | les positions liquidées, avec les titres et la valeur soldés |

Les tests ne font aucun appel réseau :

```bash
python tests/test_13f_parsing.py
python tests/test_holdings_view.py
python tests/test_pdf_report.py
```

## Archive

`data_archive/` est committé dans le dépôt : l'archive **est** la donnée
qu'on construit au fil du temps.

- **Congrès** : irremplaçable. La source ne publie qu'environ 1,5 an
  d'historique glissant ; tout ce qui en est effacé est perdu. On ajoute
  sans jamais retirer.
- **13F** : entièrement régénérable, la SEC conservant ses archives
  indéfiniment. Après un changement du parsing, relancer le workflow
  *Mettre à jour l'archive* avec `only: hedge_fund` et `rebuild: ✅` (ou
  `python update_archive.py --only hedge_fund --rebuild`). Les archives
  produites par une version antérieure du parsing sont automatiquement
  refusées à la lecture plutôt que servies avec des positions amputées.

## Workflows GitHub Actions

| Workflow | Déclenchement | Rôle |
|---|---|---|
| **Tests** | automatique (push / PR) | tests sans réseau du parsing 13F et de la vue |
| **Mettre à jour l'archive** | quotidien 6h UTC + manuel | alimente l'archive ; options `only` et `rebuild` en manuel |
| **Générer le rapport** | manuel | rapport statique (CSV/PNG) pour un pilote donné |
