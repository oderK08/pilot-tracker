"""
Client pour les transactions boursières déclarées par les membres du
Congrès américain (Sénat + Chambre des représentants), via les projets
House Stock Watcher / Senate Stock Watcher.

⚠️ AVERTISSEMENT IMPORTANT : les domaines housestockwatcher.com et
senatestockwatcher.com n'étaient pas accessibles depuis l'environnement de
développement (restriction réseau du bac à sable), donc ce client n'a PAS
pu être testé en conditions réelles -- contrairement à la quasi-totalité
des autres sources de ce projet. La structure de requête est basée sur la
documentation trouvée en ligne (des projets tiers qui utilisent
effectivement cette API), mais un premier run réel peut révéler un
ajustement nécessaire.

Par ailleurs, le fichier précalculé du dépôt GitHub
timothycarambat/senate-stock-watcher-data (aggregate/all_transactions_for_senators.json)
s'est avéré à l'usage ARRÊTÉ DEPUIS FIN 2020 -- plus mis à jour, donc pas
utilisé ici comme source malgré son accessibilité technique.

RECOMMANDATION : teste ce module seul (voir le bloc `if __name__ == "__main__"`
en bas de ce fichier) avant de construire le reste du pipeline dessus.
"""
import re
import requests
import pandas as pd

HOUSE_API_URL = "https://housestockwatcher.com/api/transactions"
SENATE_API_URL = "https://senatestockwatcher.com/api/transactions"
TIMEOUT = 30

# Noms de champs alternatifs à essayer, au cas où la structure exacte de
# l'API en direct diffère légèrement de la documentation trouvée.
FIELD_ALIASES = {
    "person_name": ["representative", "senator", "name", "office"],
    "transaction_date": ["transaction_date"],
    "disclosure_date": ["disclosure_date", "date_recieved"],
    "ticker": ["ticker"],
    "transaction_type": ["type", "transaction_type"],
    "amount_range": ["amount"],
    "asset_description": ["asset_description"],
}


def _get_field(entry: dict, field: str):
    for alias in FIELD_ALIASES[field]:
        if alias in entry and entry[alias]:
            return entry[alias]
    return None


def _clean_ticker(raw_ticker) -> str:
    """Nettoie le champ ticker (parfois du HTML embarqué, parfois '--' pour inconnu)."""
    if not raw_ticker or raw_ticker == "--":
        return None
    # Retire toute balise HTML éventuelle (ex: "<a href=...>DNKN</a>")
    text = re.sub(r"<[^>]+>", "", str(raw_ticker)).strip()
    return text if text and text != "--" else None


def parse_amount_range(amount_str: str) -> float:
    """
    Convertit une fourchette de montant déclarée (ex: "$1,001 - $15,000")
    en une valeur numérique représentative (le milieu de la fourchette).
    Les déclarations du STOCK Act ne donnent jamais le montant exact, donc
    cette approximation est une limite intrinsèque de la donnée source, pas
    de ce code.
    """
    if not amount_str:
        return None
    numbers = re.findall(r"[\d,]+", amount_str)
    if not numbers:
        return None
    values = [float(n.replace(",", "")) for n in numbers]
    if len(values) == 1:
        return values[0]
    return sum(values) / len(values)


def _fetch_transactions(url: str, chamber: str) -> pd.DataFrame:
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    raw = resp.json()

    if not isinstance(raw, list):
        raise RuntimeError(
            f"[congress_trades] Structure de réponse inattendue depuis {url} "
            f"(attendu une liste, reçu {type(raw)}). Réponse brute (tronquée): {str(raw)[:500]}"
        )

    records = []
    for entry in raw:
        ticker = _clean_ticker(_get_field(entry, "ticker"))
        if ticker is None:
            continue  # transaction sans ticker identifiable -> pas simulable

        records.append({
            "chamber": chamber,
            "person_name": _get_field(entry, "person_name"),
            "transaction_date": _get_field(entry, "transaction_date"),
            "disclosure_date": _get_field(entry, "disclosure_date"),
            "ticker": ticker,
            "transaction_type": _get_field(entry, "transaction_type"),
            "amount_estimate": parse_amount_range(_get_field(entry, "amount_range")),
        })

    df = pd.DataFrame(records)
    if not df.empty:
        df["transaction_date"] = pd.to_datetime(df["transaction_date"], errors="coerce")
        df["disclosure_date"] = pd.to_datetime(df["disclosure_date"], errors="coerce")
    return df


def get_house_transactions() -> pd.DataFrame:
    """Retourne toutes les transactions déclarées par les membres de la Chambre des représentants."""
    return _fetch_transactions(HOUSE_API_URL, "house")


def get_senate_transactions() -> pd.DataFrame:
    """Retourne toutes les transactions déclarées par les sénateurs."""
    return _fetch_transactions(SENATE_API_URL, "senate")


def filter_by_politician(df: pd.DataFrame, name: str) -> pd.DataFrame:
    """
    Filtre les transactions d'un politicien donné par correspondance
    partielle sur le nom (insensible à la casse) -- les noms complets
    varient souvent d'une déclaration à l'autre (ex: "Ron Wyden" vs
    "Ronald L Wyden"), donc une correspondance exacte serait trop fragile.
    """
    if df.empty or "person_name" not in df.columns:
        return df
    mask = df["person_name"].fillna("").str.lower().str.contains(name.lower())
    return df[mask].sort_values("transaction_date").reset_index(drop=True)


if __name__ == "__main__":
    # Test rapide et isolé de ce module seul, à lancer AVANT de construire
    # le reste du pipeline dessus (simulateur, rapport).
    print("Test House Stock Watcher...")
    try:
        house_df = get_house_transactions()
        print(f"  {len(house_df)} transactions récupérées.")
        print(house_df.head())
    except Exception as e:
        print(f"  ÉCHEC: {e}")

    print("\nTest Senate Stock Watcher...")
    try:
        senate_df = get_senate_transactions()
        print(f"  {len(senate_df)} transactions récupérées.")
        print(senate_df.head())
    except Exception as e:
        print(f"  ÉCHEC: {e}")
