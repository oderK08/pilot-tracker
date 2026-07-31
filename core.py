"""
Logique centrale de reconstitution de portefeuille -- partagée entre le
script CLI (generate_report.py, pour les runs automatisés GitHub Actions)
et l'application interactive (streamlit_app.py). Ce module ne fait AUCUN
rendu graphique ni écriture de fichier : uniquement la récupération des
données réelles et la simulation.

⚠️ Niveau de précision différent entre les deux types de pilote (limite
légale de la donnée source, pas un choix de ce module) :
  - "congress" : le STOCK Act n'oblige à déclarer qu'une FOURCHETTE de
    montant, jamais un montant exact ni un nombre d'actions. Le montant
    utilisé est le MILIEU de la fourchette déclarée. La source de données
    ne couvre qu'environ 1,5 an d'historique glissant.
  - "hedge_fund" : le 13F donne le nombre RÉEL et EXACT d'actions détenues
    à chaque trimestre déclaré sur les 5 dernières années.
"""
import pandas as pd

from data_sources import congress_trades, hedge_fund_13f, cusip_resolver
from data_sources.price_data import get_price_history, get_price_on_or_after
from simulation.portfolio_simulator import PortfolioSimulator

BENCHMARK_TICKER = "SPY"
HEDGE_FUND_YEARS = 5


def build_congress_trades(name: str) -> pd.DataFrame:
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
            continue

        transaction_type = str(row.get("transaction_type", "")).lower()
        if "purchase" in transaction_type or "buy" in transaction_type:
            action = "buy"
        elif "sale" in transaction_type or "sell" in transaction_type:
            action = "sell"
        else:
            continue

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
            f"Transactions trouvées pour '{name}' mais aucune n'a pu être normalisée "
            "(tickers manquants ou types de transaction non reconnus)."
        )
    return df_trades


def build_hedge_fund_timeline(name: str, years: int = HEDGE_FUND_YEARS):
    """
    Retourne (snapshots, first_report_date, first_total_value) pour un
    gérant de fonds, à partir de TOUS ses dépôts 13F-HR sur `years` années.

    snapshots: liste de (date, {ticker: nombre_reel_actions}), une entrée
    par trimestre réellement déposé.
    """
    history = hedge_fund_13f.get_13f_holdings_history(name, years=years)
    if history.empty:
        raise RuntimeError(f"Aucune position 13F trouvée pour '{name}' sur {years} ans.")

    cusip_map = cusip_resolver.build_cusip_to_ticker_map()
    history["ticker"] = history["cusip"].map(cusip_map)

    history = history.dropna(subset=["ticker"])
    if history.empty:
        raise RuntimeError(f"Positions trouvées pour '{name}' mais aucun CUSIP n'a pu être résolu en ticker.")

    snapshots = []
    for report_date, group in history.groupby("report_date"):
        holdings = dict(zip(group["ticker"], group["shares"]))
        snapshots.append((report_date, holdings))
    snapshots.sort(key=lambda s: s[0])

    first_date, _ = snapshots[0]
    first_total_value = history[history["report_date"] == first_date]["value_usd"].sum()

    return snapshots, first_date, first_total_value


def compute_benchmark_dca(trades: pd.DataFrame) -> pd.DataFrame:
    """
    Benchmark pour le cas Congrès : "si les MÊMES montants réels, aux MÊMES
    dates réelles, avaient été investis dans le S&P 500 (SPY)" -- achat
    progressif (dollar-cost averaging) suivant le calendrier réel des achats.
    """
    buys = trades[trades["action"] == "buy"].dropna(subset=["dollar_amount"])
    if buys.empty:
        return pd.DataFrame(columns=["date", "benchmark_value"])

    spy_prices = get_price_history(BENCHMARK_TICKER, start=str(buys["date"].min().date()))

    end_date = pd.Timestamp.today()
    dates = pd.date_range(buys["date"].min(), end_date, freq="D")
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


def compute_benchmark_lump_sum(amount: float, start_date) -> pd.DataFrame:
    """
    Benchmark pour le cas 13F : "si le montant total réellement déclaré
    avait été investi en une fois dans le S&P 500 (SPY) à la date du rapport".
    """
    prices = get_price_history(BENCHMARK_TICKER, start=str(start_date.date()))
    if prices.empty:
        return pd.DataFrame(columns=["date", "benchmark_value"])
    first_price = prices.iloc[0]["close"]
    shares = amount / first_price
    prices = prices.copy()
    prices["benchmark_value"] = prices["close"] * shares
    return prices[["date", "benchmark_value"]]


def normalize_to_base(df: pd.DataFrame, value_col: str, base: float = 10_000.0) -> pd.DataFrame:
    """
    Reformate une série de valeurs pour qu'elle parte d'une base commune
    (10 000$ par défaut) -- indispensable pour comparer équitablement deux
    séries à des échelles très différentes (ex: un portefeuille réel à
    plusieurs millions de $ vs un repère théorique). Sans cette
    normalisation, la comparaison visuelle des deux courbes n'a pas de sens :
    ce qui compte, c'est la PERFORMANCE relative, pas le montant absolu.
    """
    df = df.copy()
    if df.empty:
        return df
    first_value = df[value_col].iloc[0]
    if not first_value:
        return df
    df[value_col] = df[value_col] / first_value * base
    return df


def run_simulation(pilot_type: str, name: str, progress_callback=None):
    """
    Fonction principale : reconstitue le portefeuille réel d'un pilote.

    Args:
        pilot_type: "congress" ou "hedge_fund"
        name: nom du politicien ou du gérant de fonds
        progress_callback: fonction optionnelle appelée avec un message de
            progression (str) -- utile pour afficher un état d'avancement
            dans une interface interactive (Streamlit), sans coupler ce
            module à un framework d'interface en particulier.

    Retourne (sim, benchmark_df).
    """
    def _notify(msg):
        if progress_callback:
            progress_callback(msg)
        print(f"[core] {msg}")

    sim = PortfolioSimulator()

    if pilot_type == "congress":
        trades = build_congress_trades(name)
        total_invested = trades.loc[trades["action"] == "buy", "dollar_amount"].sum()
        _notify(f"{len(trades)} transactions à reconstituer pour {name} "
                f"(~${total_invested:,.0f} au total, montants estimés au milieu des fourchettes déclarées).")
        sim.process_trades(trades)
        benchmark_df = compute_benchmark_dca(trades)

    elif pilot_type == "hedge_fund":
        snapshots, first_date, first_value = build_hedge_fund_timeline(name, years=HEDGE_FUND_YEARS)
        n_tickers_total = len(set(t for _, h in snapshots for t in h.keys()))
        _notify(f"{len(snapshots)} trimestres réels reconstitués pour {name} sur {HEDGE_FUND_YEARS} ans "
                f"({n_tickers_total} titres différents détenus au fil du temps, "
                f"premier trimestre connu: {first_date.date()}, valeur déclarée alors ~${first_value:,.0f}).")
        sim.set_holdings_timeline(snapshots)
        benchmark_df = compute_benchmark_lump_sum(first_value, first_date)

    else:
        raise ValueError(f"pilot_type doit être 'congress' ou 'hedge_fund', reçu: '{pilot_type}'")

    return sim, benchmark_df
