"""
Client pour récupérer des prix historiques d'actions.

⚠️ QUATRIÈME SOURCE ESSAYÉE POUR CE MODULE (voir historique du projet) :
plusieurs sources "gratuites et sans clé" se sont révélées mortes ou
payantes en cours de route : housestockwatcher.com / senatestockwatcher.com
(sites définitivement hors service), Stooq (a introduit une clé API
payante en mars 2026). Résultat : une stratégie à deux niveaux, plus
robuste qu'une seule source.

Stratégie :
  1. yfinance (Yahoo Finance) en premier -- gratuit, sans clé, mais NON
     officiel (Yahoo a fermé son API officielle en 2017 ; yfinance imite
     les appels internes du site). Peut renvoyer des données trouées ou
     casser sans préavis si Yahoo change son site (dernier cassage majeur :
     février 2025 ; un bug de données incomplètes était encore signalé fin
     juillet 2026 au moment de l'écriture de ce module).
  2. Alpha Vantage en secours si yfinance échoue ou renvoie des données de
     mauvaise qualité -- fournisseur de données officiellement licencié par
     le NASDAQ, nécessite une clé API gratuite (inscription sur
     https://www.alphavantage.co), limité à 25 requêtes/jour sur le palier
     gratuit.
"""
import json
import os
import requests
import pandas as pd
import yfinance as yf

ALPHA_VANTAGE_API_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
TIMEOUT = 30

MIN_ACCEPTABLE_ROWS = 10   # en dessous, yfinance est considéré en échec
MAX_NAN_FRACTION = 0.05    # si plus de 5% des clôtures sont manquantes, données jugées corrompues
MAX_PLAUSIBLE_DAILY_MOVE = 0.50  # au-delà de 50% de variation en un seul jour, on suspecte une donnée corrompue plutôt qu'un vrai mouvement de marché

# Cache disque persistant -- une fois l'historique d'un ticker constitué, on
# ne va chercher QUE les jours manquants depuis le dernier prix connu, pas
# tout l'historique à nouveau à chaque run. Même principe que l'archive des
# transactions Congrès (data_sources/archive.py) et le cache CUSIP
# (data_sources/cusip_resolver.py) : accumuler dans le temps plutôt que
# tout re-télécharger, économise énormément de requêtes et réduit le risque
# de rate limit (ex: YFRateLimitError observé en usage réel).
PRICE_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data_archive", "prices")


def _price_cache_path(ticker: str) -> str:
    os.makedirs(PRICE_CACHE_DIR, exist_ok=True)
    return os.path.join(PRICE_CACHE_DIR, f"{ticker.strip().upper()}.json")


def _load_price_cache(ticker: str) -> pd.DataFrame:
    path = _price_cache_path(ticker)
    if not os.path.exists(path):
        return pd.DataFrame(columns=["date", "close"])
    try:
        with open(path, "r", encoding="utf-8") as f:
            records = json.load(f)
        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)
    except Exception:
        return pd.DataFrame(columns=["date", "close"])  # cache corrompu/illisible -> repart à zéro plutôt que de planter


def _save_price_cache(ticker: str, df: pd.DataFrame):
    path = _price_cache_path(ticker)
    df_to_save = df.copy()
    df_to_save["date"] = df_to_save["date"].astype(str)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(df_to_save.to_dict("records"), f)


def _strip_implausible_trailing_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    Retire les DERNIÈRES lignes dont la clôture s'écarte de façon
    implausible (>50% en un seul jour) de la ligne précédente -- protection
    contre un bug de donnée connu chez Yahoo Finance (barres journalières
    incomplètes/corrompues signalées fin juillet 2026, voir docstring du
    module) qui peut renvoyer un dernier point à un prix absurde (proche de
    0, ou doublé) sans que ce soit un vrai mouvement de marché.

    On ne vérifie que la FIN de la série (pas le milieu) : un vrai
    krach/une vraie envolée historique reste plausible au milieu d'une
    série, mais un dernier point isolé et aberrant sur des valeurs
    autrement stables est bien plus probablement un artefact de donnée
    qu'un vrai mouvement de +/-50% en une journée sur une action établie.
    """
    df = df.sort_values("date").reset_index(drop=True)
    while len(df) >= 2:
        last_close = df.iloc[-1]["close"]
        prev_close = df.iloc[-2]["close"]
        if prev_close <= 0:
            break
        move = abs(last_close - prev_close) / prev_close
        if move <= MAX_PLAUSIBLE_DAILY_MOVE:
            break
        print(f"[price_data] Dernier point de prix ({df.iloc[-1]['date'].date()}: {last_close:.2f}) "
              f"écarté -- variation de {move:.0%} par rapport à la veille jugée implausible "
              "(probable donnée corrompue plutôt qu'un vrai mouvement de marché).")
        df = df.iloc[:-1]
    return df


def _try_yfinance(ticker: str, start: str = None, end: str = None) -> pd.DataFrame:
    yf_ticker = ticker.strip().upper()
    df = yf.download(yf_ticker, start=start, end=end, progress=False,
                      multi_level_index=False, auto_adjust=True)

    if df is None or df.empty or "Close" not in df.columns:
        raise RuntimeError(f"yfinance n'a renvoyé aucune donnée exploitable pour '{ticker}'.")

    nan_fraction = df["Close"].isna().mean()
    if nan_fraction > MAX_NAN_FRACTION:
        raise RuntimeError(
            f"yfinance a renvoyé des données trop incomplètes pour '{ticker}' "
            f"({nan_fraction:.0%} de valeurs manquantes)."
        )

    if len(df) < MIN_ACCEPTABLE_ROWS:
        raise RuntimeError(f"yfinance a renvoyé trop peu de données pour '{ticker}' ({len(df)} lignes).")

    result = df.reset_index()[["Date", "Close"]].rename(columns={"Date": "date", "Close": "close"})
    result = result.dropna(subset=["close"])
    result["date"] = pd.to_datetime(result["date"])
    return result.sort_values("date").reset_index(drop=True)


def _try_alpha_vantage(ticker: str, start: str = None, end: str = None) -> pd.DataFrame:
    if not ALPHA_VANTAGE_API_KEY:
        raise RuntimeError(
            "ALPHA_VANTAGE_API_KEY n'est pas définie -- impossible d'utiliser le secours Alpha "
            "Vantage. Inscris-toi gratuitement sur https://www.alphavantage.co pour obtenir une "
            "clé, puis définis-la comme variable d'environnement / secret GitHub."
        )

    params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": ticker.strip().upper(),
        "outputsize": "full",
        "apikey": ALPHA_VANTAGE_API_KEY,
    }
    resp = requests.get(ALPHA_VANTAGE_URL, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    if "Note" in data or "Information" in data:
        raise RuntimeError(
            f"Alpha Vantage a refusé la requête pour '{ticker}' (probablement la limite de "
            f"25 requêtes/jour atteinte): {data.get('Note') or data.get('Information')}"
        )

    series = data.get("Time Series (Daily)")
    if not series:
        raise RuntimeError(
            f"Structure de réponse Alpha Vantage inattendue pour '{ticker}': "
            f"clés trouvées {list(data.keys())}"
        )

    records = [{"date": pd.to_datetime(d), "close": float(v["4. close"])} for d, v in series.items()]
    df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)

    if start:
        df = df[df["date"] >= pd.Timestamp(start)]
    if end:
        df = df[df["date"] <= pd.Timestamp(end)]

    return df.reset_index(drop=True)


def _fetch_fresh(ticker: str, start: str = None, end: str = None) -> pd.DataFrame:
    """Récupération réseau pure (yfinance puis Alpha Vantage en secours), sans cache."""
    try:
        df = _try_yfinance(ticker, start=start, end=end)
        return _strip_implausible_trailing_rows(df)
    except Exception as e_yf:
        print(f"[price_data] yfinance a échoué pour '{ticker}' ({e_yf}) -- "
              "tentative avec Alpha Vantage en secours...")
        try:
            df = _try_alpha_vantage(ticker, start=start, end=end)
            print(f"[price_data] Alpha Vantage a pris le relais avec succès pour '{ticker}'.")
            return _strip_implausible_trailing_rows(df)
        except Exception as e_av:
            raise RuntimeError(
                f"[price_data] Impossible de récupérer les prix de '{ticker}' -- "
                f"yfinance ET Alpha Vantage ont échoué.\n"
                f"  yfinance: {e_yf}\n"
                f"  Alpha Vantage: {e_av}"
            ) from e_av


def get_price_history(ticker: str, start: str = None, end: str = None) -> pd.DataFrame:
    """
    Récupère l'historique de prix (clôture quotidienne) d'un ticker, avec
    un cache disque persistant :

    - Si aucun cache n'existe encore pour ce ticker, récupération complète
      (yfinance puis Alpha Vantage en secours) et mise en cache.
    - Si un cache existe déjà, on ne récupère QUE les jours manquants
      depuis la dernière date connue -- pas tout l'historique à nouveau.
      Une fois l'historique initial constitué (ex: 6 ans pour un 13F), les
      runs suivants ne demandent plus que quelques jours de données
      fraîches, ce qui réduit fortement le nombre de requêtes et le risque
      de limitation de débit (ex: YFRateLimitError observé en usage réel).

    Args:
        ticker: ex "AAPL"
        start: date de début au format "YYYY-MM-DD" (optionnel) -- filtre
            appliqué sur le résultat final, n'affecte pas ce qui est mis
            en cache (le cache grandit dans le temps, indépendamment de ce
            qu'un appel particulier demande)
        end: date de fin au format "YYYY-MM-DD" (optionnel)

    Returns:
        DataFrame avec colonnes: date, close

    Lève une exception claire si les DEUX sources échouent ET qu'aucun
    cache n'est disponible pour compenser.
    """
    cached = _load_price_cache(ticker)

    if not cached.empty:
        last_cached_date = cached["date"].max()
        fetch_start = (last_cached_date + pd.Timedelta(days=1))

        if fetch_start <= pd.Timestamp.today():
            try:
                fresh = _fetch_fresh(ticker, start=fetch_start.strftime("%Y-%m-%d"))
                if not fresh.empty:
                    combined = pd.concat([cached, fresh], ignore_index=True)
                    combined = combined.drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)
                    _save_price_cache(ticker, combined)
                    cached = combined
                    print(f"[price_data] '{ticker}': {len(fresh)} nouveau(x) jour(s) récupéré(s) "
                          f"et ajouté(s) au cache (total désormais: {len(cached)} jours).")
            except Exception as e:
                print(f"[price_data] Échec de la mise à jour incrémentale pour '{ticker}' ({e}) -- "
                      "utilisation du cache existant tel quel, sans les tout derniers jours.")

        result = cached
    else:
        result = _fetch_fresh(ticker, start=start, end=end)
        if not result.empty:
            _save_price_cache(ticker, result)
            print(f"[price_data] '{ticker}': cache initial constitué ({len(result)} jours).")

    if start:
        result = result[result["date"] >= pd.Timestamp(start)]
    if end:
        result = result[result["date"] <= pd.Timestamp(end)]

    return result.reset_index(drop=True)


def get_price_on_or_after(price_history: pd.DataFrame, target_date) -> float:
    """
    Retourne le prix de clôture à une date donnée, ou le premier jour de
    bourse disponible APRÈS cette date si le marché était fermé ce jour-là
    (week-end, jour férié) -- utile pour simuler un achat à une date de
    transaction qui ne tombe pas forcément un jour ouvré.

    Si AUCUNE donnée n'existe à partir de cette date (typiquement : on
    demande la valeur "aujourd'hui", mais c'est un jour non ouvré ou le
    marché n'a pas encore clôturé et la donnée n'est pas encore publiée),
    on se replie sur le DERNIER prix connu AVANT cette date -- pratique
    standard de valorisation ("dernière valeur liquidative connue") plutôt
    que de considérer la position comme sans valeur faute de donnée toute
    fraîche. Sans ce repli, la toute dernière date d'un graphique (souvent
    "aujourd'hui") s'effondrait artificiellement à 0.
    """
    target_date = pd.to_datetime(target_date)

    candidates = price_history[price_history["date"] >= target_date]
    if not candidates.empty:
        return candidates.iloc[0]["close"]

    before = price_history[price_history["date"] < target_date]
    if not before.empty:
        return before.iloc[-1]["close"]

    raise ValueError(f"[price_data] Aucun prix disponible ni avant ni après le {target_date.date()}.")
