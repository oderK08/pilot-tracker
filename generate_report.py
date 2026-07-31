"""
Script principal : reconstitue la valeur RÉELLE du portefeuille d'un
"pilote" (politicien ou gérant de fonds) à partir de ses vraies déclarations
publiques, et génère les graphiques de performance, positions actuelles, et
l'historique des mouvements.

Usage :
    python generate_report.py --pilot congress --name "Nancy Pelosi"
    python generate_report.py --pilot hedge_fund --name "Berkshire Hathaway"

⚠️ Niveau de précision différent entre les deux types de pilote (limite
légale de la donnée source, pas un choix de ce script) :
  - "congress" : le STOCK Act n'oblige à déclarer qu'une FOURCHETTE de
    montant (ex: "$1,001 - $15,000"), jamais un montant exact ni un nombre
    d'actions. Le montant utilisé est le MILIEU de la fourchette déclarée --
    la meilleure estimation possible, mais une estimation.
  - "hedge_fund" : le 13F donne le nombre RÉEL et EXACT d'actions détenues à
    la date du rapport -- utilisé tel quel, aucune estimation. En
    contrepartie, on n'a accès qu'à ce dernier instantané trimestriel, pas à
    l'historique des transactions individuelles qui ont mené à ces positions.
"""
import argparse
import os

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from data_sources import congress_trades, hedge_fund_13f, cusip_resolver
from data_sources.price_data import get_price_history, get_price_on_or_after
from simulation.portfolio_simulator import PortfolioSimulator

BENCHMARK_TICKER = "SPY"
OUTPUT_DIR = "output"


def _build_congress_trades(name: str) -> pd.DataFrame:
    """
    Normalise les transactions de congress-trading-monitor au format attendu
    par le simulateur, avec le VRAI montant estimé (milieu de la fourchette
    déclarée) pour chaque transaction.
    """
    df = congress_trades.get_transactions_for_politician(name)

    records = []
    for _, row in df.iterrows():
        ticker = row.get("ticker")
        if not ticker or pd.isna(ticker):
            continue  # actif sans ticker identifiable (ex: obligation municipale) -> pas simulable

        transaction_type = str(row.get("transaction_type", "")).lower()
        if "purchase" in transaction_type or "buy" in transaction_type:
            action = "buy"
        elif "sale" in transaction_type or "sell" in transaction_type:
            action = "sell"
        else:
            continue  # ex: "exchange", non géré par le simulateur

        date = row.get("filing_date") if pd.notna(row.get("filing_date")) else row.get("transaction_date")
        if pd.isna(date):
            continue

        low = row.get("amount_range_low")
        high = row.get("amount_range_high")
        if pd.notna(low) and pd.notna(high):
            dollar_amount = (low + high) / 2
        elif pd.notna(low):
            dollar_amount = low
        else:
            dollar_amount = None

        records.append({
            "date": pd.to_datetime(date), "ticker": ticker, "action": action,
            "dollar_amount": dollar_amount,
        })

    df_trades = pd.DataFrame(records)
    if df_trades.empty:
        raise RuntimeError(
            f"[generate_report] Transactions trouvées pour '{name}' mais aucune n'a pu être "
            "normalisée (tickers manquants ou types de transaction non reconnus)."
        )
    return df_trades


def _build_hedge_fund_holdings(name: str):
    """
    Retourne (report_date, {ticker: nombre_reel_actions}) pour un gérant de
    fonds, directement depuis son dernier dépôt 13F -- pas d'estimation,
    le nombre d'actions est celui exactement déclaré.
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

    real_shares = dict(zip(holdings["ticker"], holdings["shares"]))
    report_date = pd.to_datetime(holdings["report_date"].iloc[0])
    reported_total_value = holdings["value_usd"].sum()
    return report_date, real_shares, reported_total_value


def run_simulation(pilot_type: str, name: str):
    """Retourne (sim, benchmark_series) -- le benchmark suit exactement les mêmes montants/dates réels."""
    sim = PortfolioSimulator()

    if pilot_type == "congress":
        trades = _build_congress_trades(name)
        total_invested = trades.loc[trades["action"] == "buy", "dollar_amount"].sum()
        print(f"[generate_report] {len(trades)} transactions à reconstituer pour {name} "
              f"(~${total_invested:,.0f} au total, montants estimés au milieu des fourchettes déclarées).")
        sim.process_trades(trades)
        benchmark_df = _compute_benchmark_dca(trades)

    elif pilot_type == "hedge_fund":
        report_date, real_shares, reported_value = _build_hedge_fund_holdings(name)
        print(f"[generate_report] {len(real_shares)} positions réelles à reconstituer pour {name} "
              f"(rapport du {report_date.date()}, valeur déclarée ~${reported_value:,.0f}).")
        sim.set_initial_holdings(report_date, real_shares)
        benchmark_df = _compute_benchmark_lump_sum(reported_value, report_date)

    else:
        raise ValueError(f"pilot_type doit être 'congress' ou 'hedge_fund', reçu: '{pilot_type}'")

    return sim, benchmark_df


def _compute_benchmark_dca(trades: pd.DataFrame) -> pd.DataFrame:
    """
    Benchmark pour le cas Congrès : "si les MÊMES montants réels, aux MÊMES
    dates réelles, avaient été investis dans le S&P 500 (SPY) plutôt que
    dans les titres réellement déclarés" -- achat progressif (dollar-cost
    averaging) suivant exactement le calendrier réel des transactions
    d'achat. Les ventes ne sont pas répliquées ici : le but est de comparer
    le capital réellement déployé, pas de reproduire la stratégie de sortie.
    """
    buys = trades[trades["action"] == "buy"].dropna(subset=["dollar_amount"])
    if buys.empty:
        return pd.DataFrame(columns=["date", "benchmark_value"])

    spy_prices = get_price_history(BENCHMARK_TICKER, start=str(buys["date"].min().date()))

    end_date = pd.Timestamp.today()
    dates = pd.date_range(buys["date"].min(), end_date, freq="W")
    records = []
    buys_sorted = buys.sort_values("date")
    for d in dates:
        cumulative_shares = 0.0
        for _, trade in buys_sorted[buys_sorted["date"] <= d].iterrows():
            try:
                price = get_price_on_or_after(spy_prices, trade["date"])
                cumulative_shares += trade["dollar_amount"] / price
            except Exception:
                continue
        try:
            current_price = get_price_on_or_after(spy_prices, d)
            records.append({"date": d, "benchmark_value": cumulative_shares * current_price})
        except Exception:
            continue

    return pd.DataFrame(records)


def _compute_benchmark_lump_sum(amount: float, start_date) -> pd.DataFrame:
    """
    Benchmark pour le cas 13F : "si le montant total réellement déclaré
    avait été investi en une fois dans le S&P 500 (SPY) à la date du
    rapport" -- un placement unique, suivi ensuite au jour le jour.
    """
    prices = get_price_history(BENCHMARK_TICKER, start=str(start_date.date()))
    if prices.empty:
        return pd.DataFrame(columns=["date", "benchmark_value"])
    first_price = prices.iloc[0]["close"]
    shares = amount / first_price
    prices = prices.copy()
    prices["benchmark_value"] = prices["close"] * shares
    return prices[["date", "benchmark_value"]]


def generate(pilot_type: str, name: str, output_dir: str = OUTPUT_DIR):
    os.makedirs(output_dir, exist_ok=True)
    safe_name = name.lower().replace(" ", "_")

    sim, benchmark_df = run_simulation(pilot_type, name)

    value_df = sim.value_over_time()
    if value_df.empty:
        raise RuntimeError(
            f"[generate_report] Aucun mouvement n'a pu être reconstitué pour '{name}' -- "
            "impossible de calculer une performance. Vérifie le journal "
            "(sim.get_transaction_log()) pour comprendre pourquoi."
        )

    # --- Graphique 1 : performance réelle vs S&P 500 ---
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(value_df["date"], value_df["total_value"], color="#1a3a5c", linewidth=2,
            label=f"Portefeuille réel de {name}")
    if not benchmark_df.empty:
        ax.plot(benchmark_df["date"], benchmark_df["benchmark_value"], color="#888888",
                linewidth=1.5, linestyle="--", label="S&P 500 (même capital, mêmes dates)")
    ax.set_ylabel("Valeur du portefeuille ($)")
    ax.set_title(f"Portefeuille réel reconstitué : {name}", fontsize=13, fontweight="bold", loc="left")
    ax.legend(loc="upper left", frameon=False)
    fig.tight_layout()
    perf_path = os.path.join(output_dir, f"{safe_name}_performance.png")
    fig.savefig(perf_path, dpi=150)
    plt.close(fig)
    print(f"[generate_report] Graphique de performance sauvegardé: {perf_path}")

    # --- Graphique 2 : positions actuelles réelles ---
    positions = sim.get_current_positions()
    if not positions.empty:
        positions = positions.dropna(subset=["current_value"])

    positions_path = None
    if not positions.empty:
        positions = positions.sort_values("current_value", ascending=True)
        fig, ax = plt.subplots(figsize=(10, max(4, len(positions) * 0.4)))
        ax.barh(positions["ticker"], positions["current_value"], color="#2e8b57")
        ax.set_xlabel("Valeur actuelle réelle ($)")
        ax.set_title(f"Positions actuelles réelles : {name}", fontsize=13, fontweight="bold", loc="left")
        fig.tight_layout()
        positions_path = os.path.join(output_dir, f"{safe_name}_positions.png")
        fig.savefig(positions_path, dpi=150)
        plt.close(fig)
        print(f"[generate_report] Graphique des positions sauvegardé: {positions_path}")
    else:
        print("[generate_report] Aucune position actuelle exploitable à afficher "
              "(tout a été vendu, ou prix indisponible pour les positions restantes).")

    # --- Export 3 : historique réel des mouvements ---
    log = sim.get_transaction_log()
    log_path = os.path.join(output_dir, f"{safe_name}_transactions.csv")
    log.to_csv(log_path, index=False)
    print(f"[generate_report] Historique des transactions sauvegardé: {log_path}")

    return {"performance": perf_path, "positions": positions_path,
            "transactions": log_path}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reconstitue le portefeuille réel d'un politicien ou d'un hedge fund.")
    parser.add_argument("--pilot", choices=["congress", "hedge_fund"], required=True,
                         help="Type de pilote à suivre")
    parser.add_argument("--name", required=True, help="Nom du politicien ou du gérant de fonds")
    parser.add_argument("--output-dir", default=OUTPUT_DIR, help="Dossier de sortie (défaut: output/)")
    args = parser.parse_args()

    generate(args.pilot, args.name, output_dir=args.output_dir)
