"""
Archive persistante des données de transactions/positions, committée dans
le dépôt Git -- accumule l'historique dans le temps, en plus (pas à la
place) des sources externes en direct.

Pourquoi archiver plutôt que tout re-télécharger à chaque fois :
- Pour le Congrès : la source externe (congress-trading-monitor) n'expose
  qu'environ 1,5 an d'historique GLISSANT -- elle ne garde pas plus. Si on
  ne fait que l'interroger en direct, on sera TOUJOURS limité à 1,5 an, même
  dans 10 ans. En revanche, si on ajoute (sans jamais rien effacer) les
  nouvelles transactions à notre propre archive à chaque run, on accumule
  nous-mêmes un historique de plus en plus long au fil du temps -- jusqu'à
  dépasser ce que la source elle-même a jamais permis de voir en une fois.
- Pour le 13F : la SEC garde ses archives indéfiniment, donc pas de limite
  similaire, mais archiver évite de re-télécharger ~20 dépôts XML à chaque
  run pour un gérant suivi régulièrement -- un vrai gain de temps/requêtes.

Format : un fichier JSON par pilote suivi, dans data_archive/{pilot_type}/.
Ces fichiers sont committés dans le dépôt Git (pas dans .gitignore) --
l'archive elle-même EST la donnée qu'on construit au fil du temps.
"""
import os
import json

import pandas as pd

ARCHIVE_DIR = os.path.join(os.path.dirname(__file__), "..", "data_archive")

# Colonnes utilisées pour détecter les doublons entre l'archive existante et
# les nouvelles données récupérées en direct -- une transaction est
# considérée comme "la même" si toutes ces colonnes correspondent.
CONGRESS_DEDUP_KEYS = [
    "filer_name", "ticker", "transaction_type",
    "transaction_date", "filing_date", "amount_range_low", "amount_range_high",
]
HEDGE_FUND_DEDUP_KEYS = ["report_date", "cusip"]


def _safe_name(name: str) -> str:
    return name.lower().replace(" ", "_").replace("/", "_")


def _archive_path(pilot_type: str, name: str) -> str:
    folder = os.path.join(ARCHIVE_DIR, pilot_type)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{_safe_name(name)}.json")


def load_archive(pilot_type: str, name: str) -> pd.DataFrame:
    """Charge l'archive existante pour un pilote donné, ou un DataFrame vide si aucune archive n'existe encore."""
    path = _archive_path(pilot_type, name)
    if not os.path.exists(path):
        return pd.DataFrame()

    with open(path, "r", encoding="utf-8") as f:
        records = json.load(f)

    df = pd.DataFrame(records)
    for date_col in ["transaction_date", "filing_date", "report_date"]:
        if date_col in df.columns:
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    return df


def save_archive(pilot_type: str, name: str, df: pd.DataFrame):
    """Sauvegarde l'archive complète (déjà fusionnée/dédupliquée) pour un pilote donné."""
    path = _archive_path(pilot_type, name)
    df_to_save = df.copy()
    for date_col in ["transaction_date", "filing_date", "report_date"]:
        if date_col in df_to_save.columns:
            df_to_save[date_col] = df_to_save[date_col].astype(str)

    records = df_to_save.to_dict("records")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2, default=str)


def merge_and_save(pilot_type: str, name: str, live_df: pd.DataFrame, dedup_keys: list) -> pd.DataFrame:
    """
    Fusionne les données récupérées en direct avec l'archive existante,
    déduplique, sauvegarde l'archive mise à jour, et retourne le résultat
    fusionné (à utiliser pour la suite du traitement -- c'est LUI qui a
    l'historique le plus complet, pas la source en direct seule).

    La déduplication garde la version ARCHIVÉE en priorité en cas de
    conflit exact (keep="first", archive d'abord) -- en pratique les
    valeurs ne devraient de toute façon pas changer rétroactivement pour
    une transaction déjà déclarée.
    """
    archived_df = load_archive(pilot_type, name)
    combined = pd.concat([archived_df, live_df], ignore_index=True) if not archived_df.empty else live_df.copy()

    keys_present = [k for k in dedup_keys if k in combined.columns]
    if keys_present:
        combined = combined.drop_duplicates(subset=keys_present, keep="first").reset_index(drop=True)

    save_archive(pilot_type, name, combined)
    return combined
