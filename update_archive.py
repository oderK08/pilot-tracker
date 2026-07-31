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
#
# ⚠️ Les noms doivent correspondre à l'orthographe EXACTE utilisée dans la
# source (voir find_most_active_congress.py pour vérifier) -- une
# correspondance partielle insensible à la casse est utilisée, mais "Ro
# Khanna" ne matche PAS "Rohit Khanna" (le vrai nom dans les données), par
# exemple. Sélection basée sur le classement réel par nombre de
# transactions (voir find_most_active_congress.py) -- Donald J Trump
# (exécutif, pas Congrès) et Alan Armstrong (ratio transactions/titres
# suspect, à vérifier avant d'ajouter) ont été volontairement exclus.
TRACKED_CONGRESS = [
    "Nancy Pelosi",
    "Michael T. McCaul",
    "Rohit Khanna",
    "Markwayne Mullin",
    "Josh Gottheimer",
    "Gilbert Cisneros",
    "John Phelan",
    "David H McCormick",
    "Scott H. Peters",
    "April McClain Delaney",
    "John Boozman",
]

# Liste des gérants de fonds suivis en continu -- des institutionnels
# majeurs et bien documentés, avec des styles de gestion différents
# (value, quant, macro, growth, AI-thématique) pour un panel varié.
TRACKED_HEDGE_FUNDS = [
    "Berkshire Hathaway",
    "Bridgewater Associates",
    "Pershing Square Capital Management",
    "Scion Asset Management",
    "Duquesne Family Office",
    "Situational Awareness",  # Leopold Aschenbrenner, CIK 0002045724
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
