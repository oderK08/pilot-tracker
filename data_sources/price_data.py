"""
Client pour récupérer des prix historiques d'actions via Stooq
(https://stooq.com), gratuit et sans clé API.

Pourquoi Stooq plutôt que Yahoo Finance : l'API officielle Yahoo Finance a
été fermée en 2017. La librairie `yfinance` fonctionne en scrapant le site,
ce qui casse régulièrement à chaque refonte du site (dernier cassage
majeur : février 2025, plus des soucis de données rapportés en juillet
2026). Stooq expose un simple endpoint CSV, stable depuis des années,
largement utilisé dans les outils quantitatifs comme remplacement direct.
"""
import io
import requests
import pandas as pd

BASE_URL = "https://stooq.com/q/d/l/"
TIMEOUT = 30


def _normalize_ticker(ticker: str) -> str:
    """
    Stooq attend un suffixe de marché (ex: 'aapl.us' pour une action US).
    Si le ticker ne contient pas déjà de point, on suppose une action US
    et on ajoute '.us' automatiquement.
    """
    ticker = ticker.strip().lower()
    if "." not in ticker:
        return f"{ticker}.us"
    return ticker


def get_price_history(ticker: str, start: str = None, end: str = None) -> pd.DataFrame:
    """
    Récupère l'historique de prix (clôture quotidienne) d'un ticker.

    Args:
        ticker: ex "AAPL" (le suffixe ".us" est ajouté automatiquement si absent)
        start: date de début au format "YYYY-MM-DD" (optionnel, filtre appliqué APRÈS
            téléchargement, pas envoyé à Stooq -- voir note ci-dessous)
        end: date de fin au format "YYYY-MM-DD" (idem)

    Returns:
        DataFrame avec colonnes: date, close

    Note technique : on ne transmet PAS les paramètres de plage de dates
    (d1/d2) à Stooq -- un test réel a montré une erreur 404 lors de l'usage
    du paramètre d1 seul pour certains tickers (ex: SPY), et ce comportement
    n'a pas pu être vérifié davantage (domaine non testable en direct depuis
    l'environnement de développement). Récupérer tout l'historique puis
    filtrer nous-mêmes en local est plus lent mais beaucoup plus robuste,
    puisqu'on ne dépend plus d'un comportement d'API qu'on ne peut pas
    valider.

    Lève une exception claire si le ticker est introuvable ou si Stooq ne
    renvoie aucune donnée (au lieu de silencieusement renvoyer un DataFrame
    vide qui ferait planter les calculs plus loin sans message clair).
    """
    stooq_ticker = _normalize_ticker(ticker)
    params = {"s": stooq_ticker, "i": "d"}  # i=d -> fréquence quotidienne, pas de d1/d2

    resp = requests.get(BASE_URL, params=params, timeout=TIMEOUT)
    resp.raise_for_status()

    text = resp.text.strip()
    if not text or text.startswith("N/D") or "Exceeded the daily hits limit" in text:
        raise RuntimeError(
            f"[price_data] Aucune donnée Stooq pour le ticker '{stooq_ticker}'. "
            "Vérifie l'orthographe du ticker, ou réessaie plus tard si la limite "
            "de requêtes quotidienne de Stooq a été atteinte."
        )

    df = pd.read_csv(io.StringIO(text))
    if "Date" not in df.columns or "Close" not in df.columns:
        raise RuntimeError(
            f"[price_data] Structure de réponse Stooq inattendue pour '{stooq_ticker}'. "
            f"Colonnes trouvées: {list(df.columns)}"
        )

    df = df.rename(columns={"Date": "date", "Close": "close"})
    df["date"] = pd.to_datetime(df["date"])
    df = df[["date", "close"]].sort_values("date").reset_index(drop=True)

    if start:
        df = df[df["date"] >= pd.Timestamp(start)]
    if end:
        df = df[df["date"] <= pd.Timestamp(end)]

    return df.reset_index(drop=True)


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
