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
import os
import requests
import pandas as pd
import yfinance as yf

ALPHA_VANTAGE_API_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
TIMEOUT = 30

MIN_ACCEPTABLE_ROWS = 10   # en dessous, yfinance est considéré en échec
MAX_NAN_FRACTION = 0.05    # si plus de 5% des clôtures sont manquantes, données jugées corrompues


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


def get_price_history(ticker: str, start: str = None, end: str = None) -> pd.DataFrame:
    """
    Récupère l'historique de prix (clôture quotidienne) d'un ticker.

    Essaie yfinance en premier, puis Alpha Vantage en secours si yfinance
    échoue ou renvoie des données de mauvaise qualité (voir docstring du
    module pour le détail de cette stratégie à deux niveaux).

    Args:
        ticker: ex "AAPL"
        start: date de début au format "YYYY-MM-DD" (optionnel)
        end: date de fin au format "YYYY-MM-DD" (optionnel)

    Returns:
        DataFrame avec colonnes: date, close

    Lève une exception claire si les DEUX sources échouent, avec le détail
    des deux erreurs pour faciliter le diagnostic.
    """
    try:
        return _try_yfinance(ticker, start=start, end=end)
    except Exception as e_yf:
        print(f"[price_data] yfinance a échoué pour '{ticker}' ({e_yf}) -- "
              "tentative avec Alpha Vantage en secours...")
        try:
            df = _try_alpha_vantage(ticker, start=start, end=end)
            print(f"[price_data] Alpha Vantage a pris le relais avec succès pour '{ticker}'.")
            return df
        except Exception as e_av:
            raise RuntimeError(
                f"[price_data] Impossible de récupérer les prix de '{ticker}' -- "
                f"yfinance ET Alpha Vantage ont échoué.\n"
                f"  yfinance: {e_yf}\n"
                f"  Alpha Vantage: {e_av}"
            ) from e_av


def get_price_on_or_after(price_history: pd.DataFrame, target_date) -> float:
    """
    Retourne le prix de clôture à une date donnée, ou le premier jour de
    bourse disponible APRÈS cette date si le marché était fermé ce jour-là
    (week-end, jour férié) -- utile pour simuler un achat à une date de
    transaction qui ne tombe pas forcément un jour ouvré.
    """
    target_date = pd.to_datetime(target_date)
    candidates = price_history[price_history["date"] >= target_date]
    if candidates.empty:
        raise ValueError(f"[price_data] Aucun prix disponible à partir du {target_date.date()}.")
    return candidates.iloc[0]["close"]
