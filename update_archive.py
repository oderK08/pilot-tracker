"""
Script planifié (cron) : met à jour l'archive persistante (voir
data_sources/archive.py) pour la liste curatée de pilotes définie dans
tracked_pilots.py.

C'est CE run régulier qui fait le travail lourd une fois par jour --
l'appli Streamlit se contente ensuite de LIRE ce qui a déjà été récupéré,
ce qui la rend quasi instantanée pour les pilotes suivis.

Le travail diffère selon le type de pilote :
  - Congrès : récupération des transactions ET calcul de la valeur
    journalière du portefeuille (le vrai travail lourd -- position par
    position, jour par jour), mis en cache incrémentalement.
  - Hedge fund : récupération des dépôts 13F uniquement. Aucune
    valorisation quotidienne n'est calculée : la vue 13F ne montre que des
    positions et leurs variations trimestrielles (voir
    analysis/holdings_view.py), et tout ce dont elle a besoin est déjà
    dans le dépôt lui-même.

Pensé pour tourner quotidiennement via GitHub Actions.

Usage :
    python update_archive.py                     # tous les pilotes suivis
    python update_archive.py --only hedge_fund   # seulement les fonds
    python update_archive.py --rebuild           # ignore l'archive et repart de la source
"""
import argparse

import core
from data_sources import archive
from simulation.portfolio_simulator import PortfolioSimulator
from tracked_pilots import TRACKED_CONGRESS, TRACKED_HEDGE_FUNDS


def update_congress(name: str):
    """Récupère les transactions puis met à jour le cache de valeur journalière."""
    sim = PortfolioSimulator()
    stock_trades, option_trades = core.build_congress_trades(name)
    if not stock_trades.empty:
        sim.process_trades(stock_trades)
    if not option_trades.empty:
        sim.process_option_trades(option_trades)
    print(f"  Archive à jour: {len(stock_trades) + len(option_trades)} transaction(s) au total.")

    updated_history = core.get_value_over_time("congress", name, sim)
    print(f"  Cache de valeur journalière à jour: {len(updated_history)} jour(s) au total.")


def update_hedge_fund(name: str, rebuild: bool = False):
    """
    Récupère les dépôts 13F et les fusionne dans l'archive, trimestre par
    trimestre. Avec `rebuild`, l'archive existante est d'abord supprimée --
    sans risque, la SEC conservant ses archives indéfiniment.
    """
    if rebuild and archive.delete_archive("hedge_fund", name):
        print("  Archive existante supprimée (--rebuild).")

    history = core.build_hedge_fund_holdings(name)
    quarters = history["report_date"].nunique()
    options = int(history["put_call"].notna().sum())
    print(f"  Archive à jour: {len(history)} positions sur {quarters} trimestre(s) "
          f"(dont {options} ligne(s) d'options).")


def update_all(only: str = None, rebuild: bool = False):
    if only in (None, "congress"):
        print(f"=== Mise à jour de {len(TRACKED_CONGRESS)} politicien(s) suivi(s) ===")
        for name in TRACKED_CONGRESS:
            print(f"\n--- {name} ---")
            try:
                update_congress(name)
            except Exception as e:
                print(f"  ÉCHEC pour '{name}': {e}")

    if only in (None, "hedge_fund"):
        print(f"\n=== Mise à jour de {len(TRACKED_HEDGE_FUNDS)} gérant(s) de fonds suivi(s) ===")
        if rebuild:
            print("(--rebuild : les archives existantes sont ignorées, tout est re-téléchargé depuis EDGAR)")
        for name in TRACKED_HEDGE_FUNDS:
            print(f"\n--- {name} ---")
            try:
                update_hedge_fund(name, rebuild=rebuild)
            except Exception as e:
                print(f"  ÉCHEC pour '{name}': {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Met à jour l'archive des pilotes suivis.")
    parser.add_argument("--only", choices=["congress", "hedge_fund"],
                        help="Ne mettre à jour qu'un seul type de pilote.")
    parser.add_argument("--rebuild", action="store_true",
                        help="Reconstruire les archives 13F depuis EDGAR au lieu de compléter l'existant.")
    args = parser.parse_args()

    update_all(only=args.only, rebuild=args.rebuild)
