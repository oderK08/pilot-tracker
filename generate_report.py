"""
Script principal : simule un portefeuille virtuel copiant un "pilote"
(politicien ou gérant de fonds) et génère les graphiques de performance,
positions actuelles, et l'historique des transactions simulées.

Usage :
    python generate_report.py --pilot congress --name "Nancy Pelosi"
    python generate_report.py --pilot hedge_fund --name "Berkshire Hathaway"

⚠️ Différence de comportement entre les deux types de pilote :
  - "congress" : vraies transactions individuelles datées (achats/ventes
    étalés dans le temps, comme un vrai historique de trading)
  - "hedge_fund" : on n'a accès qu'au DERNIER dépôt 13F (une photo
    trimestrielle des positions), pas à l'historique des transactions
    individuelles -- on simule donc l'entrée en une fois dans les positions
    actuelles, pondérées par leur poids réel dans le portefeuille déclaré,
    à la date du rapport. La performance avant cette date n'existe donc pas
    pour ce type de pilote (contrairement au Congrès).
"""
import argparse
import os

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from data_sources import congress_trades, hedge_fund_13f, cusip_resolver
from data_sources.price_data import get_price_history
from simulation.portfolio_simulator import PortfolioSimulator

STARTING_CASH = 10_000.0
BUY_FRACTION = 0.10
BENCHMARK_TICKER = "SPY"
OUTPUT_DIR = "output"


def _build_congress_trades(name: str) -> pd.DataFrame:
    """Normalise les transactions Quiver Quantitative au format attendu par le simulateur."""
    df = congress_trades.get_transactions_for_politician(name)
    if df.empty:
        raise RuntimeError(f"[generate_report] Aucune transaction trouvée pour '{name}'.")

    records = []
    for _, row in df.iterrows():
        ticker = row.get("Ticker")
        if not ticker or ticker == "--":
            continue

        transaction_type = str(row.get("Transaction", "")).lower()
        if "purchase" in transaction_type or "buy" in transaction_type:
            action = "buy"
        elif "sale" in transaction_type or "sell" in transaction_type:
            action = "sell"
        else:
            continue  # ex: "exchange", non géré par le simulateur

        date = row.get("Filed") or row.get("TransactionDate")
        if pd.isna(date):
            continue

        records.append({"date": pd.to_datetime(date), "ticker": ticker, "action": action})

    df_trades = pd.DataFrame(records)
    if df_trades.empty:
        raise RuntimeError(
            f"[generate_report] Transactions trouvées pour '{name}' mais aucune n'a pu être "
            "normalisée (tickers manquants ou types de transaction non reconnus)."
        )
    return df_trades


def _build_hedge_fund_weights(name: str):
    """
    Retourne (report_date, {ticker: poids}) pour un gérant de fonds, à
    partir de son dernier dépôt 13F. Voir avertissement en tête de fichier
    sur la limitation "photo unique" de cette approche.
    """
    holdings = hedge_fund_13f.get_13f_holdings(name)
    if holdings.empty:
        raise RuntimeError(f"[generate_report] Aucune position 13F trouvée pour '{name}'.")

    print("[generate_report] Résolution des CUSIP en tickers via les données SEC Fails-to-Deliver...")
    cusip_map = cusip_resolver.build_cusip_to_ticker_map()
    holdings["ticker"] = holdings["cusip"].map(cusip_map)

    n_before = len(holdings)
    holdings = holdings.dropna(subset=["ticker"])
    n_after = len(holdings)
    if n_after < n_before:
        print(f"[generate_report] {n_before - n_after} position(s) sur {n_before} n'ont pas pu être "
              "résolues en ticker (CUSIP absent des données de référence) -- ignorées.")

    if holdings.empty:
        raise RuntimeError(
            f"[generate_report] Positions trouvées pour '{name}' mais aucun CUSIP n'a pu être "
            "résolu en ticker."
        )

    total_value = holdings["value_usd"].sum()
    holdings["weight"] = holdings["value_usd"] / total_value
    weights = dict(zip(holdings["ticker"], holdings["weight"]))
    report_date = pd.to_datetime(holdings["report_date"].iloc[0])
    return report_date, weights


def run_simulation(pilot_type: str, name: str) -> PortfolioSimulator:
    sim = PortfolioSimulator(starting_cash=STARTING_CASH, buy_fraction=BUY_FRACTION)

    if pilot_type == "congress":
        trades = _build_congress_trades(name)
        print(f"[generate_report] {len(trades)} transactions à simuler pour {name}.")
        sim.process_trades(trades)

    elif pilot_type == "hedge_fund":
        report_date, weights = _build_hedge_fund_weights(name)
        print(f"[generate_report] {len(weights)} positions à simuler pour {name} "
              f"(rapport du {report_date.date()}).")
        sim.invest_by_weights(report_date, weights)

    else:
        raise ValueError(f"pilot_type doit être 'congress' ou 'hedge_fund', reçu: '{pilot_type}'")

    return sim


def _compute_benchmark(start_date, end_date) -> pd.DataFrame:
    """Valeur d'un investissement identique dans le S&P 500 (SPY) sur la même période, pour comparaison."""
    prices = get_price_history(BENCHMARK_TICKER, start=str(start_date.date()))
    prices = prices[prices["date"] <= end_date]
    if prices.empty:
        return pd.DataFrame(columns=["date", "benchmark_value"])
    first_price = prices.iloc[0]["close"]
    shares = STARTING_CASH / first_price
    prices["benchmark_value"] = prices["close"] * shares
    return prices[["date", "benchmark_value"]]


def generate(pilot_type: str, name: str, output_dir: str = OUTPUT_DIR):
    os.makedirs(output_dir, exist_ok=True)
    safe_name = name.lower().replace(" ", "_")

    sim = run_simulation(pilot_type, name)

    value_df = sim.value_over_time()
    if value_df.empty:
        raise RuntimeError(
            f"[generate_report] Aucune transaction n'a pu être exécutée pour '{name}' -- "
            "impossible de calculer une performance. Vérifie le journal des transactions "
            "(sim.get_transaction_log()) pour comprendre pourquoi."
        )

    benchmark_df = _compute_benchmark(value_df["date"].min(), value_df["date"].max())

    # --- Graphique 1 : performance vs S&P 500 ---
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(value_df["date"], value_df["total_value"], color="#1a3a5c", linewidth=2,
            label=f"Portefeuille copiant {name}")
    if not benchmark_df.empty:
        ax.plot(benchmark_df["date"], benchmark_df["benchmark_value"], color="#888888",
                linewidth=1.5, linestyle="--", label="S&P 500 (repère)")
    ax.axhline(STARTING_CASH, color="#cccccc", linewidth=0.8, linestyle=":")
    ax.set_ylabel("Valeur du portefeuille ($)")
    ax.set_title(f"Performance simulée : copier {name}", fontsize=13, fontweight="bold", loc="left")
    ax.legend(loc="upper left", frameon=False)
    fig.tight_layout()
    perf_path = os.path.join(output_dir, f"{safe_name}_performance.png")
    fig.savefig(perf_path, dpi=150)
    plt.close(fig)
    print(f"[generate_report] Graphique de performance sauvegardé: {perf_path}")

    # --- Graphique 2 : positions actuelles ---
    # Important : on nettoie (dropna) AVANT de décider s'il y a quelque chose
    # à afficher, pas après -- sinon on risque de générer un graphique à
    # partir de données qui s'avèrent vides une fois nettoyées (ex: prix du
    # jour indisponible pour toutes les positions), créant une incohérence
    # entre le fichier réellement sauvegardé et l'état du DataFrame.
    positions = sim.get_current_positions()
    if not positions.empty:
        positions = positions.dropna(subset=["current_value"])

    positions_path = None
    if not positions.empty:
        positions = positions.sort_values("current_value", ascending=True)
        fig, ax = plt.subplots(figsize=(10, max(4, len(positions) * 0.4)))
        ax.barh(positions["ticker"], positions["current_value"], color="#2e8b57")
        ax.set_xlabel("Valeur actuelle ($)")
        ax.set_title(f"Positions actuelles simulées : {name}", fontsize=13, fontweight="bold", loc="left")
        fig.tight_layout()
        positions_path = os.path.join(output_dir, f"{safe_name}_positions.png")
        fig.savefig(positions_path, dpi=150)
        plt.close(fig)
        print(f"[generate_report] Graphique des positions sauvegardé: {positions_path}")
    else:
        print("[generate_report] Aucune position actuelle exploitable à afficher "
              "(tout a été vendu, rien n'a été acheté, ou prix indisponible pour les positions restantes).")

    # --- Export 3 : historique des transactions simulées ---
    log = sim.get_transaction_log()
    log_path = os.path.join(output_dir, f"{safe_name}_transactions.csv")
    log.to_csv(log_path, index=False)
    print(f"[generate_report] Historique des transactions sauvegardé: {log_path}")

    return {"performance": perf_path, "positions": positions_path,
            "transactions": log_path}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simule un portefeuille copiant un politicien ou un hedge fund.")
    parser.add_argument("--pilot", choices=["congress", "hedge_fund"], required=True,
                         help="Type de pilote à copier")
    parser.add_argument("--name", required=True, help="Nom du politicien ou du gérant de fonds")
    parser.add_argument("--output-dir", default=OUTPUT_DIR, help="Dossier de sortie (défaut: output/)")
    args = parser.parse_args()

    generate(args.pilot, args.name, output_dir=args.output_dir)
