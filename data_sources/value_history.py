"""
Cache disque de la valeur JOURNALIÈRE déjà calculée pour chaque pilote
suivi -- évite de recalculer tout l'historique (prix x positions, jour par
jour) à chaque fois qu'un utilisateur consulte l'appli.

C'est l'étape la plus coûteuse de tout le pipeline : value_over_time()
appelle total_value() pour CHAQUE jour de l'historique, qui lui-même
interroge le prix de chaque position détenue ce jour-là. Sans ce cache,
chaque consultation (même via l'archive des transactions/positions, déjà
rapide) redéclenchait ce calcul complet depuis le tout premier jour.

update_archive.py (le run planifié quotidien) alimente ce cache d'UN jour
supplémentaire à chaque exécution -- jamais tout l'historique à nouveau.
L'appli Streamlit se contente de LIRE ce cache pour les pilotes suivis, ce
qui rend la consultation quasi instantanée.
"""
import os
import json

import pandas as pd

VALUE_HISTORY_DIR = os.path.join(os.path.dirname(__file__), "..", "data_archive", "value_history")


def _safe_name(name: str) -> str:
    return name.lower().replace(" ", "_").replace("/", "_")


def _path(pilot_type: str, name: str) -> str:
    folder = os.path.join(VALUE_HISTORY_DIR, pilot_type)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{_safe_name(name)}.json")


def load_value_history(pilot_type: str, name: str) -> pd.DataFrame:
    """Charge la valeur journalière déjà connue, ou un DataFrame vide si rien n'est encore en cache."""
    path = _path(pilot_type, name)
    if not os.path.exists(path):
        return pd.DataFrame(columns=["date", "total_value"])
    try:
        with open(path, "r", encoding="utf-8") as f:
            records = json.load(f)
        df = pd.DataFrame(records)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception:
        return pd.DataFrame(columns=["date", "total_value"])  # cache corrompu -> repart à zéro plutôt que de planter


def save_value_history(pilot_type: str, name: str, df: pd.DataFrame):
    path = _path(pilot_type, name)
    df_to_save = df.copy()
    df_to_save["date"] = df_to_save["date"].astype(str)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(df_to_save.to_dict("records"), f)


def update_value_history(pilot_type: str, name: str, sim) -> pd.DataFrame:
    """
    Calcule et persiste la valeur journalière du portefeuille, en ne
    recalculant QUE les jours manquants depuis le dernier point déjà
    connu -- pas tout l'historique à chaque fois.

    Args:
        sim: instance de PortfolioSimulator déjà construite (positions
            déjà chargées depuis l'archive/13F/Congrès), sur laquelle on
            appelle value_over_time() uniquement pour la fenêtre manquante.

    Returns:
        L'historique complet (ancien + nouveau fusionné), prêt à afficher.
    """
    existing = load_value_history(pilot_type, name)

    if not existing.empty:
        start_date = existing["date"].max() + pd.Timedelta(days=1)
        if start_date > pd.Timestamp.today():
            return existing  # déjà à jour jusqu'à aujourd'hui, rien à recalculer

        new_values = sim.value_over_time(start_date=start_date)
        if new_values.empty:
            return existing

        combined = pd.concat([existing, new_values], ignore_index=True)
        combined = combined.drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)
    else:
        combined = sim.value_over_time()

    if not combined.empty:
        save_value_history(pilot_type, name, combined)
    return combined
