"""
Script planifié (cron) : met à jour l'archive persistante (voir
data_sources/archive.py) pour une liste curatée de pilotes suivis dans la
durée -- indépendant des recherches ponctuelles faites par un utilisateur
sur l'appli Streamlit (qui met aussi à jour l'archive en passant, mais
seulement pour les pilotes que quelqu'un cherche activement).

Pensé pour tourner régulièrement (ex: quotidien) via GitHub Actions --
c'est CE run régulier qui, au fil du temps, fait grandir l'historique
Congrès au-delà de la fenêtre glissante de ~1,5 an de la source externe.

Usage :
    python update_archive.py
"""
import core

# Liste des politiciens suivis en continu -- à ajuster librement. Chaque nom
# ajouté ici commence à accumuler son propre historique dès le premier run
# après son ajout (pas d'historique rétroactif possible au-delà de ce que la
# source externe expose au moment de l'ajout).
TRACKED_CONGRESS = [
    "Nancy Pelosi",
    "Dan Crenshaw",
    "Josh Gottheimer",
    "Ro Khanna",
    "Michael McCaul",
    "Markwayne Mullin",
]

# Liste des gérants de fonds suivis en continu -- des institutionnels
# majeurs et bien documentés, avec des styles de gestion différents
# (value, quant, macro, growth) pour un panel varié.
TRACKED_HEDGE_FUNDS = [
    "Berkshire Hathaway",
    "Bridgewater Associates",
    "Pershing Square Capital Management",
    "Scion Asset Management",
    "Duquesne Family Office",
]


def update_all():
    print(f"=== Mise à jour de {len(TRACKED_CONGRESS)} politicien(s) suivi(s) ===")
    for name in TRACKED_CONGRESS:
        print(f"\n--- {name} ---")
        try:
            trades = core.build_congress_trades(name)
            print(f"  OK: {len(trades)} transactions dans l'archive après cette mise à jour.")
        except Exception as e:
            print(f"  ÉCHEC pour '{name}': {e}")

    print(f"\n=== Mise à jour de {len(TRACKED_HEDGE_FUNDS)} gérant(s) de fonds suivi(s) ===")
    for name in TRACKED_HEDGE_FUNDS:
        print(f"\n--- {name} ---")
        try:
            snapshots, first_date, _ = core.build_hedge_fund_timeline(name)
            print(f"  OK: {len(snapshots)} trimestres dans l'archive après cette mise à jour "
                  f"(depuis {first_date.date()}).")
        except Exception as e:
            print(f"  ÉCHEC pour '{name}': {e}")


if __name__ == "__main__":
    update_all()
