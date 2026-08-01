"""
Script planifié (cron) : met à jour l'archive persistante (voir
data_sources/archive.py) ET le cache de valeur journalière pré-calculée
(voir data_sources/value_history.py) pour la liste curatée de pilotes
définie dans tracked_pilots.py.

C'est CE run régulier qui fait tout le travail lourd (calcul de la valeur
jour par jour, position par position) une fois par jour -- l'appli
Streamlit se contente ensuite de LIRE ce qui a déjà été calculé, ce qui la
rend quasi instantanée pour les pilotes suivis. Sans ce script, chaque
consultation de l'appli aurait dû recalculer tout l'historique depuis zéro.

Pensé pour tourner quotidiennement via GitHub Actions.

Usage :
    python update_archive.py
"""
import core
from simulation.portfolio_simulator import PortfolioSimulator
from tracked_pilots import TRACKED_CONGRESS, TRACKED_HEDGE_FUNDS


def _build_and_cache_value_history(pilot_type: str, name: str):
    """
    Construit le simulateur (positions à jour depuis l'archive/13F/Congrès)
    puis met à jour le cache de valeur journalière -- ne recalcule que les
    jours manquants depuis la dernière exécution de ce script, pas tout
    l'historique.
    """
    sim = PortfolioSimulator()

    if pilot_type == "congress":
        stock_trades, option_trades = core.build_congress_trades(name)
        if not stock_trades.empty:
            sim.process_trades(stock_trades)
        if not option_trades.empty:
            sim.process_option_trades(option_trades)
        n_trades = len(stock_trades) + len(option_trades)
        print(f"  Archive à jour: {n_trades} transaction(s) au total.")

    elif pilot_type == "hedge_fund":
        snapshots, first_date, _ = core.build_hedge_fund_timeline(name)
        sim.set_holdings_timeline(snapshots)
        print(f"  Archive à jour: {len(snapshots)} trimestre(s) depuis {first_date.date()}.")

    else:
        raise ValueError(f"pilot_type inconnu: '{pilot_type}'")

    updated_history = core.get_value_over_time(pilot_type, name, sim)
    print(f"  Cache de valeur journalière à jour: {len(updated_history)} jour(s) au total.")


def update_all():
    print(f"=== Mise à jour de {len(TRACKED_CONGRESS)} politicien(s) suivi(s) ===")
    for name in TRACKED_CONGRESS:
        print(f"\n--- {name} ---")
        try:
            _build_and_cache_value_history("congress", name)
        except Exception as e:
            print(f"  ÉCHEC pour '{name}': {e}")

    print(f"\n=== Mise à jour de {len(TRACKED_HEDGE_FUNDS)} gérant(s) de fonds suivi(s) ===")
    for name in TRACKED_HEDGE_FUNDS:
        print(f"\n--- {name} ---")
        try:
            _build_and_cache_value_history("hedge_fund", name)
        except Exception as e:
            print(f"  ÉCHEC pour '{name}': {e}")


if __name__ == "__main__":
    update_all()
