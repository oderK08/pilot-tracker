"""
Client pour les transactions boursières déclarées par les membres du
Congrès américain (Sénat + Chambre + exécutif), via le projet open source
"Congress Trading Monitor" (https://github.com/kadoa-org/congress-trading-monitor).

⚠️ TROISIÈME SOURCE ESSAYÉE POUR CE MODULE :
  1. housestockwatcher.com / senatestockwatcher.com -> définitivement hors
     service (certificat SSL expiré, domaine ne résolvant plus).
  2. API Quiver Quantitative -> l'accès programmatique (pas juste le site
     web) nécessite un abonnement payant, contrairement à ce que suggérait
     la documentation générale du palier "gratuit".
  3. Cette source (celle utilisée ici) : un projet open source qui republie
     en JSON statique, sur GitHub, les déclarations officielles agrégées
     depuis le Clerk de la Chambre, le système eFD du Sénat, et l'Office of
     Government Ethics -- gratuit, sans clé API, mis à jour quotidiennement,
     hébergé directement sur GitHub (donc pas de risque de paywall soudain
     comme un service commercial).

Limite connue : le fichier ne couvre que les transactions les plus
récentes (environ 1,5 an d'historique glissant au moment de l'écriture),
pas l'historique complet depuis 2012 que le projet propose par ailleurs sur
son site -- largement suffisant pour notre usage (simuler un portefeuille
qui suit les transactions à venir/récentes d'un pilote).
"""
import requests
import pandas as pd

TRADES_URL = "https://raw.githubusercontent.com/kadoa-org/congress-trading-monitor/main/public/data/trades.json"
TIMEOUT = 30

_cached_df = None  # cache mémoire pour la durée du run -- voir get_all_trades()


def get_all_trades(force_refresh: bool = False) -> pd.DataFrame:
    """
    Récupère toutes les transactions disponibles (tous filers confondus).

    Mis en cache en mémoire pour la durée du run : important maintenant
    qu'on traite plusieurs politiciens dans le même script
    (update_archive.py) -- sans ce cache, on retélécharge le même gros
    fichier une fois par politicien suivi, pour rien.
    """
    global _cached_df
    if _cached_df is not None and not force_refresh:
        return _cached_df

    resp = requests.get(TRADES_URL, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    if not isinstance(data, list):
        raise RuntimeError(
            f"[congress_trades] Structure de réponse inattendue (attendu une liste, "
            f"reçu {type(data)}). Le format du fichier a peut-être changé -- voir "
            f"{TRADES_URL}"
        )

    df = pd.DataFrame(data)
    expected_cols = {"filer_name", "ticker", "transaction_type", "transaction_date", "filing_date"}
    missing = expected_cols - set(df.columns)
    if missing:
        raise RuntimeError(
            f"[congress_trades] Colonnes attendues manquantes: {missing}. "
            f"Colonnes présentes: {list(df.columns)}"
        )

    df["transaction_date"] = pd.to_datetime(df["transaction_date"], errors="coerce")
    df["filing_date"] = pd.to_datetime(df["filing_date"], errors="coerce")
    _cached_df = df
    return df


def get_transactions_for_politician(name: str) -> pd.DataFrame:
    """
    Filtre les transactions d'un politicien donné par correspondance
    partielle sur le nom (insensible à la casse).
    """
    df = get_all_trades()
    mask = df["filer_name"].fillna("").str.lower().str.contains(name.lower())
    result = df[mask].sort_values("transaction_date").reset_index(drop=True)

    if result.empty:
        raise RuntimeError(
            f"[congress_trades] Aucune transaction trouvée pour '{name}' dans les données "
            "disponibles (environ 1,5 an d'historique glissant). Vérifie l'orthographe, "
            "ou le politicien n'a peut-être pas fait de transaction récente."
        )
    return result


if __name__ == "__main__":
    print("Test Congress Trading Monitor -- transactions de Nancy Pelosi...")
    try:
        df = get_transactions_for_politician("Nancy Pelosi")
        print(f"  {len(df)} transactions récupérées.")
        print(df[["transaction_date", "ticker", "transaction_type", "amount_range_label"]].head())
    except Exception as e:
        print(f"  ÉCHEC: {e}")
