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

from data_sources import congress_trades, hedge_fund_13f, cusip_resolver, archive, value_history
from data_sources.options_parser import parse_option_details
from data_sources.price_data import get_price_history, get_price_on_or_after
from simulation.portfolio_simulator import PortfolioSimulator

BENCHMARK_TICKER = "SPY"
HEDGE_FUND_YEARS = 5


def _normalize_congress_trades(df: pd.DataFrame):
    """
    Sépare et normalise les transactions en DEUX groupes distincts -- les
    actions classiques et les OPTIONS (identifiées par asset_type == "OP"
    dans la source) -- car une option ne peut pas être traitée comme un
    achat d'action au comptant (levier, prix d'exercice, échéance) sans
    fausser complètement l'exposition réelle.

    Le détail de l'option (call/put, strike, échéance, nombre de contrats)
    est extrait du champ `comment` en texte libre déjà fourni par la
    source -- pas besoin de reparser le document PDF nous-mêmes.

    Returns:
        (stock_trades, option_trades) -- deux DataFrames, chacun pouvant
        être vide si aucune transaction du type correspondant n'est trouvée.
    """
    stock_records = []
    option_records = []
    n_options_unparsed = 0

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

        is_option = str(row.get("asset_type", "")).upper() == "OP"

        if is_option:
            details = parse_option_details(row.get("comment"))
            if details is None:
                n_options_unparsed += 1
                continue  # texte du commentaire non reconnu -- on n'invente pas les détails manquants
            option_records.append({
                "date": pd.to_datetime(date), "ticker": ticker, "action": action,
                "option_type": details["option_type"], "strike_price": details["strike_price"],
                "expiration_date": details["expiration_date"], "num_contracts": details["num_contracts"],
                "dollar_amount": dollar_amount,
            })
        else:
            stock_records.append({
                "date": pd.to_datetime(date), "ticker": ticker, "action": action,
                "dollar_amount": dollar_amount,
            })

    if n_options_unparsed:
        print(f"[core] {n_options_unparsed} transaction(s) d'options au format de commentaire non reconnu -- ignorées.")

    return pd.DataFrame(stock_records), pd.DataFrame(option_records)


def build_congress_trades(name: str):
    """
    Normalise les transactions au format attendu par le simulateur, séparées
    en (stock_trades, option_trades).

    Fusionne systématiquement avec l'archive persistante locale (voir
    data_sources/archive.py) : la source externe n'a qu'~1,5 an
    d'historique glissant, donc SANS cette fusion on serait pour toujours
    limité à cette fenêtre, même après des années d'utilisation.
    """
    live_df = congress_trades.get_transactions_for_politician(name)
    df = archive.merge_and_save("congress", name, live_df, archive.CONGRESS_DEDUP_KEYS)
    stock_trades, option_trades = _normalize_congress_trades(df)
    if stock_trades.empty and option_trades.empty:
        raise RuntimeError(
            f"Transactions trouvées pour '{name}' mais aucune n'a pu être normalisée "
            "(tickers manquants, types non reconnus, ou commentaires d'options non reconnus)."
        )
    return stock_trades, option_trades


def _snapshots_from_history(history: pd.DataFrame):
    """
    Transforme un historique 13F déjà résolu en tickers (qu'il vienne de
    l'archive ou d'une récupération en direct) en (snapshots,
    first_report_date, first_total_value).
    """
    snapshots = []
    for report_date, group in history.groupby("report_date"):
        holdings = dict(zip(group["ticker"], group["shares"]))
        snapshots.append((report_date, holdings))
    snapshots.sort(key=lambda s: s[0])

    first_date, _ = snapshots[0]
    first_total_value = history[history["report_date"] == first_date]["value_usd"].sum()

    return snapshots, first_date, first_total_value


def _combine_dates(stock_trades: pd.DataFrame, option_trades: pd.DataFrame) -> pd.DataFrame:
    """
    Combine les dates des transactions actions et options en un seul
    DataFrame (juste la colonne "date") pour le calcul du benchmark --
    gère proprement le cas où l'un des deux DataFrames est vide (et donc
    sans même la colonne "date", ce qu'un simple pd.concat planterait sur).
    """
    dates = []
    if not stock_trades.empty:
        dates.append(stock_trades[["date"]])
    if not option_trades.empty:
        dates.append(option_trades[["date"]])
    if not dates:
        return pd.DataFrame(columns=["date"])
    return pd.concat(dates, ignore_index=True)


def build_hedge_fund_timeline(name: str, years: int = HEDGE_FUND_YEARS):
    """
    Retourne (snapshots, first_report_date, first_total_value) pour un
    gérant de fonds, à partir de TOUS ses dépôts 13F-HR sur `years` années.

    Fusionne avec l'archive persistante locale -- moins critique que pour
    le Congrès (la SEC garde ses archives indéfiniment), mais évite de
    re-télécharger ~20 dépôts XML à chaque run pour un gérant suivi
    régulièrement.

    snapshots: liste de (date, {ticker: nombre_reel_actions}), une entrée
    par trimestre réellement déposé.
    """
    live_history = hedge_fund_13f.get_13f_holdings_history(name, years=years)
    if live_history.empty:
        raise RuntimeError(f"Aucune position 13F trouvée pour '{name}' sur {years} ans.")

    history = archive.merge_and_save("hedge_fund", name, live_history, archive.HEDGE_FUND_DEDUP_KEYS)

    cusip_map = cusip_resolver.build_cusip_to_ticker_map()
    history["ticker"] = history["cusip"].map(cusip_map)

    history = history.dropna(subset=["ticker"])
    if history.empty:
        raise RuntimeError(f"Positions trouvées pour '{name}' mais aucun CUSIP n'a pu être résolu en ticker.")

    return _snapshots_from_history(history)


def compute_benchmark_dca(trades: pd.DataFrame) -> pd.DataFrame:
    """
    Benchmark pour le cas Congrès : simplement la performance RÉELLE du
    S&P 500 (SPY) depuis le premier jour du portefeuille suivi, tenu sans
    y toucher -- pas une reproduction transaction par transaction.

    Une version précédente essayait de rejouer chaque achat/vente réel sur
    SPY à la place du titre réel, mais ça créait un biais : le vrai
    portefeuille échoue parfois à exécuter une transaction (ticker
    introuvable, prix indisponible chez la source de prix), alors que SPY,
    lui, a TOUJOURS un prix disponible -- le repère accumulait donc des
    achats qui n'avaient en réalité pas eu lieu côté portefeuille réel,
    créant des écarts artificiels. Un simple "buy & hold" du S&P 500 depuis
    le premier jour évite ce biais et reste le repère le plus standard et
    le plus lisible en finance.
    """
    if trades.empty:
        return pd.DataFrame(columns=["date", "benchmark_value"])

    first_date = trades["date"].min()
    return compute_benchmark_lump_sum(10_000.0, first_date)


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


def filter_by_range(df: pd.DataFrame, date_col: str, range_key: str) -> pd.DataFrame:
    """
    Filtre un DataFrame sur une plage de temps standard ("3M", "6M", "YTD",
    "MAX") -- utilisé pour le sélecteur de période du graphique de
    performance. Ne fait QUE filtrer ; le rebasage à 10 000$ doit être
    appliqué APRÈS ce filtrage (voir normalize_to_base), pas avant, sinon
    on verrait juste un zoom sur une courbe déjà indexée depuis le tout
    début, pas la vraie performance isolée de cette fenêtre précise.
    """
    if df.empty:
        return df

    today = pd.Timestamp.today()
    if range_key == "3M":
        cutoff = today - pd.DateOffset(months=3)
    elif range_key == "6M":
        cutoff = today - pd.DateOffset(months=6)
    elif range_key == "YTD":
        cutoff = pd.Timestamp(year=today.year, month=1, day=1)
    elif range_key == "MAX":
        return df
    else:
        raise ValueError(f"range_key inconnu: '{range_key}' (attendu: 3M, 6M, YTD, MAX)")

    filtered = df[df[date_col] >= cutoff].reset_index(drop=True)
    return filtered if not filtered.empty else df  # si la fenêtre est plus courte que l'historique dispo, on garde tout plutôt qu'un graphique vide


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


def run_simulation(pilot_type: str, name: str, progress_callback=None, prefer_archive: bool = True):
    """
    Fonction principale : reconstitue le portefeuille réel d'un pilote.

    Args:
        pilot_type: "congress" ou "hedge_fund"
        name: nom du politicien ou du gérant de fonds
        progress_callback: fonction optionnelle appelée avec un message de
            progression (str) -- utile pour afficher un état d'avancement
            dans une interface interactive (Streamlit), sans coupler ce
            module à un framework d'interface en particulier.
        prefer_archive: si True (défaut), et qu'une archive existe déjà pour
            ce pilote, on l'utilise DIRECTEMENT sans requête réseau -- c'est
            ce qui rend les pilotes suivis par update_archive.py (voir
            update_archive.py) quasi INSTANTANÉS à consulter, au lieu
            d'attendre ~20 requêtes SEC (13F) ou un téléchargement complet
            (Congrès) à chaque fois. Un pilote jamais vu auparavant (pas
            encore dans l'archive) déclenche quand même la récupération en
            direct normale, qui alimente l'archive pour la prochaine fois.

    Retourne (sim, benchmark_df).
    """
    def _notify(msg):
        if progress_callback:
            progress_callback(msg)
        print(f"[core] {msg}")

    sim = PortfolioSimulator()

    if pilot_type == "congress":
        if prefer_archive:
            archived = archive.load_archive("congress", name)
            if not archived.empty:
                _notify(f"Lecture instantanée depuis l'archive locale pour {name} "
                        f"({len(archived)} transactions déjà connues).")
                stock_trades, option_trades = _normalize_congress_trades(archived)
                if not stock_trades.empty:
                    sim.process_trades(stock_trades)
                if not option_trades.empty:
                    sim.process_option_trades(option_trades)
                benchmark_df = compute_benchmark_dca(_combine_dates(stock_trades, option_trades))
                return sim, benchmark_df

        stock_trades, option_trades = build_congress_trades(name)
        total_invested_stock = stock_trades.loc[stock_trades["action"] == "buy", "dollar_amount"].sum() if not stock_trades.empty else 0
        _notify(f"{len(stock_trades)} transaction(s) d'actions et {len(option_trades)} transaction(s) d'options "
                f"à reconstituer pour {name} (~${total_invested_stock:,.0f} investis en actions, "
                "montants estimés au milieu des fourchettes déclarées).")
        if not stock_trades.empty:
            sim.process_trades(stock_trades)
        if not option_trades.empty:
            sim.process_option_trades(option_trades)
        benchmark_df = compute_benchmark_dca(_combine_dates(stock_trades, option_trades))

    elif pilot_type == "hedge_fund":
        if prefer_archive:
            archived = archive.load_archive("hedge_fund", name)
            if not archived.empty:
                cusip_map = cusip_resolver.build_cusip_to_ticker_map()
                archived["ticker"] = archived["cusip"].map(cusip_map)
                archived = archived.dropna(subset=["ticker"])
                if not archived.empty:
                    _notify(f"Lecture instantanée depuis l'archive locale pour {name} "
                            f"({archived['report_date'].nunique()} trimestres déjà connus).")
                    snapshots, first_date, first_value = _snapshots_from_history(archived)
                    sim.set_holdings_timeline(snapshots)
                    benchmark_df = compute_benchmark_lump_sum(first_value, first_date)
                    return sim, benchmark_df

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
