"""
Logique centrale de reconstitution de portefeuille -- partagée entre le
script CLI (generate_report.py, pour les runs automatisés GitHub Actions)
et l'application interactive (streamlit_app.py). Ce module ne fait AUCUN
rendu graphique ni écriture de fichier : uniquement la récupération des
données réelles et leur mise en forme.

⚠️ Les deux types de pilote ne sont PAS traités de la même façon, parce
que leurs sources ne disent pas la même chose :

  - "congress" : le STOCK Act déclare des TRANSACTIONS (des événements
    datés), mais seulement par FOURCHETTE de montant, jamais un montant
    exact ni un nombre d'actions. Le montant utilisé est le milieu de la
    fourchette. Un portefeuille peut donc être reconstitué transaction par
    transaction, et sa valeur simulée dans le temps -- avec l'imprécision
    des fourchettes.

  - "hedge_fund" : le 13F déclare des POSITIONS (une photo trimestrielle),
    avec le nombre exact de titres, mais sans aucun mouvement entre deux
    photos. On n'en tire donc PAS de performance : voir
    analysis/holdings_view.py, qui en tire ce que cette source sait
    réellement dire -- les positions et leurs variations d'un trimestre à
    l'autre. Le simulateur de portefeuille ne sert plus qu'au Congrès.
"""
import pandas as pd

from analysis import holdings_view
from data_sources import congress_trades, hedge_fund_13f, cusip_resolver, archive, value_history
from data_sources.options_parser import parse_option_details
from data_sources.price_data import get_price_history, get_price_on_or_after
from simulation.portfolio_simulator import PortfolioSimulator

BENCHMARK_TICKER = "SPY"
HEDGE_FUND_YEARS = 5


def get_value_over_time(pilot_type: str, name: str, sim: PortfolioSimulator) -> pd.DataFrame:
    """
    Retourne la valeur ABSOLUE journalière du portefeuille ($), en
    utilisant le cache pré-calculé (voir data_sources/value_history.py) si
    disponible. Représente la valeur des positions RÉELLEMENT SUIVIES par
    la source (13F ou Congrès) -- peut légitimement baisser si le pilote
    réduit sa taille de position (ex: argent déplacé vers du cash, que le
    13F ne capture pas), sans que ce soit une vraie perte de performance
    (voir get_performance_index_over_time pour la mesure de performance
    qui neutralise cet effet).
    """
    return value_history.update_value_history(pilot_type, name, sim)


def get_performance_index_over_time(pilot_type: str, name: str, sim: PortfolioSimulator) -> pd.DataFrame:
    """
    Retourne un indice de PERFORMANCE (base 100) par rendement pondéré dans
    le temps -- neutralise les rééquilibrages et changements de taille de
    portefeuille (voir PortfolioSimulator.performance_index_over_time),
    contrairement à la valeur absolue qui peut chuter simplement parce
    qu'un fonds a réduit sa taille de position ou déplacé du capital vers
    un actif non suivi par le 13F (le cash, notamment).

    Mis en cache séparément de la valeur absolue (voir
    data_sources/value_history.py, réutilisé avec une clé de pilote
    distincte) -- même principe d'accumulation incrémentale jour par jour.
    """
    return value_history.update_value_history(
        f"{pilot_type}_performance", name, sim, compute_fn=lambda s, **kw: s.performance_index_over_time(**kw)
    )


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


def build_hedge_fund_holdings(name: str, years: int = HEDGE_FUND_YEARS) -> pd.DataFrame:
    """
    Récupère l'historique des positions 13F d'un gérant et le fusionne dans
    l'archive persistante -- trimestre par trimestre, le dépôt le plus
    récent faisant autorité (voir archive.replace_quarters_and_save).

    Retourne le DataFrame des positions, prêt pour holdings_view.
    """
    live_history = hedge_fund_13f.get_13f_holdings_history(name, years=years)
    if live_history.empty:
        raise RuntimeError(f"Aucune position 13F trouvée pour '{name}' sur {years} ans.")

    return archive.replace_quarters_and_save(name, live_history)


def get_hedge_fund_view(name: str, n_quarters: int = holdings_view.DEFAULT_QUARTERS,
                        prefer_archive: bool = True, progress_callback=None) -> dict:
    """
    Point d'entrée unique de la vue hedge fund : positions du dernier
    trimestre triées par poids, variations par rapport aux trimestres
    précédents, sorties, puts et calls.

    Args:
        n_quarters: profondeur d'historique affichée.
        prefer_archive: si True et qu'une archive AU SCHÉMA ACTUEL existe,
            elle est lue directement, sans aucune requête réseau (voir
            archive.load_hedge_fund_archive -- une archive produite par
            l'ancien parsing est refusée et déclenche une reconstruction).

    ⚠️ La résolution CUSIP -> ticker (voir cusip_resolver.py) est
    incomplète par nature : elle s'appuie sur le fichier Fails-to-Deliver
    de la SEC, qui ne couvre pas tous les titres. Une position non résolue
    est conservée et affichée sous le nom de son émetteur -- l'écarter
    fausserait tous les poids en pourcentage, ce qui serait bien pire
    qu'un libellé moins joli.
    """
    def _notify(msg):
        if progress_callback:
            progress_callback(msg)
        print(f"[core] {msg}")

    history = archive.load_hedge_fund_archive(name) if prefer_archive else pd.DataFrame()

    if history.empty:
        _notify(f"Récupération des dépôts 13F de {name} auprès de la SEC...")
        history = build_hedge_fund_holdings(name)
    else:
        _notify(f"Lecture instantanée depuis l'archive locale pour {name} "
                f"({history['report_date'].nunique()} trimestres déjà connus).")

    cusip_map = cusip_resolver.build_cusip_to_ticker_map()
    view = holdings_view.build_quarterly_view(history, n_quarters=n_quarters, ticker_map=cusip_map)

    summary = view.get("summary", {})
    if summary:
        _notify(f"{name}: {summary['n_positions']} positions au {summary['report_date'].date()}, "
                f"{summary['n_new']} entrée(s), {summary['n_exited']} sortie(s), "
                f"{summary['n_options']} position(s) optionnelle(s).")
    return view


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
    return _benchmark_lump_sum(10_000.0, first_date)


def _benchmark_lump_sum(amount: float, start_date) -> pd.DataFrame:
    """
    "Si le montant avait été investi en une fois dans le S&P 500 (SPY) à
    cette date". Utilisé uniquement par le repère du Congrès -- les 13F ne
    donnent plus lieu à aucun calcul de performance.
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

def to_percentage_return(df: pd.DataFrame, value_col: str) -> pd.DataFrame:
    """
    Convertit une série de valeurs en PERFORMANCE EN POURCENTAGE depuis le
    début de la période affichée (0% au premier point) -- affichage plus
    standard qu'une base arbitraire de 10 000$ pour comparer des
    performances entre deux séries à des échelles différentes.
    """
    df = df.copy()
    if df.empty:
        return df
    first_value = df[value_col].iloc[0]
    if not first_value:
        return df
    df[value_col] = (df[value_col] / first_value - 1) * 100
    return df

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
    Reconstitue le portefeuille réel d'un pilote du CONGRÈS, transaction
    par transaction.

    ⚠️ Réservé à "congress". Les hedge funds passent par
    get_hedge_fund_view() : un 13F est une photo trimestrielle sans aucun
    mouvement intermédiaire, en simuler une valeur quotidienne donnait une
    courbe dont la précision affichée n'existait pas dans la source.

    Args:
        pilot_type: "congress"
        name: nom du politicien
        progress_callback: fonction optionnelle appelée avec un message de
            progression (str) -- utile pour afficher un état d'avancement
            dans une interface interactive (Streamlit), sans coupler ce
            module à un framework d'interface en particulier.
        prefer_archive: si True (défaut), et qu'une archive existe déjà pour
            ce pilote, on l'utilise DIRECTEMENT sans requête réseau -- c'est
            ce qui rend les pilotes suivis par update_archive.py quasi
            INSTANTANÉS à consulter, au lieu d'attendre un téléchargement
            complet à chaque fois. Un pilote jamais vu auparavant déclenche
            quand même la récupération en direct normale, qui alimente
            l'archive pour la prochaine fois.

    Retourne (sim, benchmark_df).
    """
    if pilot_type != "congress":
        raise ValueError(
            f"run_simulation ne traite que le Congrès, reçu '{pilot_type}'. "
            "Pour un gérant de fonds, utiliser core.get_hedge_fund_view() -- un 13F "
            "ne permet pas de reconstituer une performance (voir analysis/holdings_view.py)."
        )

    def _notify(msg):
        if progress_callback:
            progress_callback(msg)
        print(f"[core] {msg}")

    sim = PortfolioSimulator()

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

    return sim, benchmark_df
