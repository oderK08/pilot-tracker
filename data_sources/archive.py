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
#
# ⚠️ Ce mécanisme vaut pour le CONGRÈS uniquement, où chaque ligne est un
# événement ponctuel et indépendant. Il ne convient PAS aux positions 13F :
# un même CUSIP y apparaît légitimement plusieurs fois dans un trimestre
# (déclaration éclatée entre sous-gérants, ou position longue doublée d'un
# put), et déduplicer sur (report_date, cusip) détruisait la position en
# n'en gardant qu'un morceau. Les 13F passent par
# `replace_quarters_and_save` -- voir cette fonction.
CONGRESS_DEDUP_KEYS = [
    "filer_name", "ticker", "transaction_type",
    "transaction_date", "filing_date", "amount_range_low", "amount_range_high",
]

# Colonnes dont la présence atteste qu'une archive 13F a été produite par la
# version actuelle du parsing. Une archive dépourvue de `put_call` vient de
# l'ancien parsing : ses positions sont amputées (une ligne conservée par
# CUSIP au lieu de la somme) et ses valeurs post-2022 gonflées d'un facteur
# 1000. Rien ne permet de la réparer sur place -- les lignes manquantes ne
# sont nulle part -- donc on la reconstruit intégralement depuis la SEC, qui
# conserve ses archives indéfiniment.
HEDGE_FUND_REQUIRED_COLUMNS = ["put_call", "title_of_class", "shrs_prn_type"]


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


def delete_archive(pilot_type: str, name: str) -> bool:
    """
    Supprime l'archive d'un pilote. Retourne True si un fichier a bien été
    supprimé.

    ⚠️ À n'utiliser QUE pour les hedge funds : leurs données sont
    intégralement re-téléchargeables depuis EDGAR, qui conserve ses
    archives indéfiniment. L'archive du Congrès, elle, est irremplaçable --
    sa source ne publie qu'environ 1,5 an d'historique glissant, donc tout
    ce qui en est effacé est définitivement perdu (voir l'en-tête de ce
    module).
    """
    path = _archive_path(pilot_type, name)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def is_legacy_hedge_fund_archive(df: pd.DataFrame) -> bool:
    """
    Vrai si l'archive 13F vient de l'ancien parsing (voir
    HEDGE_FUND_REQUIRED_COLUMNS) et doit donc être jetée plutôt que
    complétée.
    """
    if df.empty:
        return False
    return any(col not in df.columns for col in HEDGE_FUND_REQUIRED_COLUMNS)


def load_hedge_fund_archive(name: str) -> pd.DataFrame:
    """
    Charge l'archive 13F d'un gérant, en refusant explicitement les
    archives à l'ancien schéma : mieux vaut repartir de zéro (les données
    sont intégralement re-téléchargeables depuis EDGAR) que d'afficher des
    positions amputées avec l'assurance d'une archive.
    """
    df = load_archive("hedge_fund", name)
    if is_legacy_hedge_fund_archive(df):
        print(f"[archive] Archive 13F obsolète pour '{name}' (ancien schéma, positions incomplètes) "
              "-- ignorée, elle sera reconstruite depuis EDGAR.")
        return pd.DataFrame()
    return df


def replace_quarters_and_save(name: str, live_df: pd.DataFrame) -> pd.DataFrame:
    """
    Fusionne des positions 13F fraîchement récupérées avec l'archive, au
    niveau du TRIMESTRE et non de la ligne.

    Un 13F n'est pas un flux d'événements mais une PHOTO : le dépôt le plus
    récent d'un trimestre fait autorité sur tout ce qu'on avait pour ce
    trimestre (c'est d'ailleurs à ça que servent les amendements 13F-HR/A).
    On remplace donc les trimestres re-téléchargés en bloc, et on conserve
    intacts les trimestres absents des données fraîches -- ce qui préserve
    l'historique accumulé au-delà de la fenêtre interrogée.

    Fusionner ligne à ligne serait faux dans les deux sens : on ne peut ni
    additionner (on doublerait les positions à chaque exécution), ni
    déduupliquer sur (trimestre, CUSIP) (on n'en garderait qu'un morceau).
    """
    archived = load_hedge_fund_archive(name)

    if archived.empty:
        combined = live_df.copy()
    elif live_df.empty:
        combined = archived
    else:
        refreshed_quarters = set(pd.to_datetime(live_df["report_date"]).unique())
        kept = archived[~pd.to_datetime(archived["report_date"]).isin(refreshed_quarters)]
        n_replaced = archived["report_date"].nunique() - kept["report_date"].nunique()
        if n_replaced:
            print(f"[archive] {name}: {n_replaced} trimestre(s) remplacé(s) par la version fraîche, "
                  f"{kept['report_date'].nunique()} trimestre(s) plus ancien(s) conservé(s).")
        combined = pd.concat([kept, live_df], ignore_index=True)

    combined = combined.sort_values(["report_date", "value_usd"], ascending=[True, False]).reset_index(drop=True)
    save_archive("hedge_fund", name, combined)
    return combined
