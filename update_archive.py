"""
Script planifié (cron) : met à jour l'archive persistante (voir
data_sources/archive.py) pour la liste curatée de pilotes définie dans
tracked_pilots.py -- indépendant des recherches ponctuelles faites par un
utilisateur sur l'appli Streamlit (qui ne permet de toute façon que de
choisir parmi cette même liste, voir tracked_pilots.py).

Pensé pour tourner régulièrement (ex: quotidien) via GitHub Actions --
c'est CE run régulier qui, au fil du temps, fait grandir l'historique
Congrès au-delà de la fenêtre glissante de ~1,5 an de la source externe.

Usage :
    python update_archive.py
"""
import core
from tracked_pilots import TRACKED_CONGRESS, TRACKED_HEDGE_FUNDS


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
