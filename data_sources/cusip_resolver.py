"""
Résolution CUSIP -> Ticker via les données "Fails-to-Deliver" de la SEC
(https://www.sec.gov/data-research/sec-markets-data/fails-deliver-data),
gratuites, officielles, mises à jour deux fois par mois.

Ces données existent à l'origine pour tout autre chose (le suivi des
échecs de livraison de titres, un sujet de réglementation boursière sans
rapport avec notre usage), mais chaque fichier contient une table
CUSIP/Ticker/Nom d'entreprise qui sert ici uniquement de source de
correspondance -- gratuite et officielle, sans avoir besoin d'un service
tiers payant pour ça.

Format du fichier (documenté par la SEC) : texte "pipe-delimited" (colonnes
séparées par "|"), zippé, avec les colonnes SETTLEMENT DATE | CUSIP |
SYMBOL | QUANTITY (FAILS) | DESCRIPTION | PRICE.
"""
import io
import json
import os
import time
import zipfile
from datetime import datetime, timedelta

import requests
import pandas as pd

BASE_URL = "https://www.sec.gov/files/data/fails-deliver-data"
TIMEOUT = 60

# Cache disque -- ce fichier ne change que 2 fois par mois côté SEC, donc
# pas besoin de le retélécharger à chaque appel. Sans ce cache, chaque
# recherche d'un fonds (13F) devait re-télécharger et re-parser ce fichier
# volumineux, une source majeure de lenteur perçue par l'utilisateur.
CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "data_archive", "cusip_map_cache.json")
CACHE_MAX_AGE_DAYS = 7

# La SEC exige un User-Agent identifiable sur ses requêtes, comme pour EDGAR
# -- même variable d'environnement que dans hedge_fund_13f.py et le projet
# research-dashboard principal.
EDGAR_USER_AGENT = os.environ.get("EDGAR_USER_AGENT", "")
if not EDGAR_USER_AGENT:
    print("[cusip_resolver] ATTENTION: EDGAR_USER_AGENT n'est pas définie. "
          "La SEC exige un User-Agent identifiable (nom + email), sinon les requêtes "
          "seront bloquées avec une erreur 403.")
HEADERS = {"User-Agent": EDGAR_USER_AGENT or "pilot-tracker contact@example.com"}


def _build_url(year: int, month: int, half: str) -> str:
    return f"{BASE_URL}/cnsfails{year}{month:02d}{half}.zip"


def _try_download(year: int, month: int, half: str) -> bytes:
    url = _build_url(year, month, half)
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    if resp.status_code != 200:
        return None
    return resp.content


def _generate_candidate_periods(n_months_back: int = 24):
    """
    Génère les périodes (année, mois, moitié) les plus récentes en premier,
    sur `n_months_back` mois en arrière (24 par défaut, soit 2 ans).

    ⚠️ Ce nombre a été augmenté de 4 à 24 mois : un seul instantané récent
    ne couvre que les titres ACTUELLEMENT en situation de fails-to-deliver
    -- un titre détenu par un fonds il y a un an, mais qui n'est plus en
    situation de fails-to-deliver aujourd'hui, ratait complètement la
    résolution. Ça faussait la valorisation des trimestres 13F anciens
    (positions non résolues = valeur ignorée), créant l'apparence d'une
    "performance" énorme et artificielle par rapport aux trimestres plus
    récents mieux couverts.
    """
    candidates = []
    d = datetime.today().replace(day=1)
    for _ in range(n_months_back):
        candidates.append((d.year, d.month, "b"))
        candidates.append((d.year, d.month, "a"))
        d = (d - timedelta(days=1)).replace(day=1)  # mois précédent
    return candidates


def _fetch_and_merge_all_available(n_months_back: int = 24) -> dict:
    """
    Télécharge et FUSIONNE tous les instantanés disponibles sur
    `n_months_back` mois (pas juste le plus récent) -- construit une
    correspondance CUSIP->ticker cumulative, bien plus complète qu'un seul
    instantané, indispensable pour résoudre correctement des positions
    détenues il y a plusieurs années (voir docstring de
    _generate_candidate_periods).
    """
    merged_mapping = {}
    n_files_used = 0

    for year, month, half in _generate_candidate_periods(n_months_back):
        time.sleep(0.1)  # usage responsable de la SEC, pas de rafale de dizaines de requêtes d'un coup
        content = _try_download(year, month, half)
        if not content:
            continue
        try:
            df = _parse_cusip_ticker_file(content)
        except Exception:
            continue  # fichier corrompu/illisible -> ignoré, on continue avec les autres mois

        df = df.dropna(subset=["CUSIP", "SYMBOL"])
        for cusip, symbol in zip(df["CUSIP"], df["SYMBOL"]):
            merged_mapping.setdefault(cusip, symbol)  # garde la 1ère correspondance trouvée (peu importe laquelle, un CUSIP ne change pas de ticker)
        n_files_used += 1

    if n_files_used == 0:
        raise RuntimeError(
            "[cusip_resolver] Aucun fichier Fails-to-Deliver n'a pu être récupéré sur "
            f"{n_months_back} mois. Vérifie la connectivité réseau."
        )

    print(f"[cusip_resolver] {n_files_used} instantané(s) mensuel(s) fusionné(s), "
          f"{len(merged_mapping)} correspondances CUSIP->Ticker au total.")
    return merged_mapping


def _parse_cusip_ticker_file(zip_content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(zip_content)) as zf:
        txt_candidates = [n for n in zf.namelist() if n.lower().endswith(".txt")]
        if not txt_candidates:
            raise RuntimeError(
                f"[cusip_resolver] Aucun fichier .txt trouvé dans l'archive. "
                f"Contenu de l'archive: {zf.namelist()}"
            )
        with zf.open(txt_candidates[0]) as f:
            df = pd.read_csv(f, sep="|", dtype=str, on_bad_lines="skip")

    df.columns = [c.strip().upper() for c in df.columns]
    if "CUSIP" not in df.columns or "SYMBOL" not in df.columns:
        raise RuntimeError(
            f"[cusip_resolver] Structure de fichier inattendue. Colonnes trouvées: {list(df.columns)}"
        )
    return df


def _load_disk_cache() -> dict:
    """Charge le cache disque s'il existe et n'est pas trop vieux, sinon retourne None."""
    if not os.path.exists(CACHE_PATH):
        return None
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            cached = json.load(f)
        cached_at = datetime.fromisoformat(cached["cached_at"])
        if datetime.today() - cached_at > timedelta(days=CACHE_MAX_AGE_DAYS):
            return None  # cache trop vieux, on va rafraîchir
        return cached["mapping"]
    except Exception:
        return None  # cache corrompu/illisible -> on retélécharge, pas d'erreur bloquante


def _save_disk_cache(mapping: dict):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump({"cached_at": datetime.today().isoformat(), "mapping": mapping}, f)


def build_cusip_to_ticker_map(force_refresh: bool = False) -> dict:
    """
    Construit un dict {cusip: ticker} en FUSIONNANT tous les instantanés
    Fails-to-Deliver disponibles sur les 2 dernières années (pas juste le
    plus récent) -- lu depuis un cache disque si disponible et récent
    (moins de 7 jours), sinon reconstruit et re-mis en cache.

    Fusionner plusieurs mois plutôt qu'un seul instantané est nécessaire
    pour résoudre correctement des positions détenues il y a plusieurs
    trimestres/années par un fonds -- un titre qui n'est plus en situation
    de fails-to-deliver aujourd'hui peut très bien l'avoir été il y a un an
    (voir docstring de _generate_candidate_periods pour le détail du
    problème que ça causait sans cette fusion).
    """
    if not force_refresh:
        cached = _load_disk_cache()
        if cached is not None:
            return cached

    mapping = _fetch_and_merge_all_available()
    _save_disk_cache(mapping)
    return mapping


if __name__ == "__main__":
    print("Test résolution CUSIP -> Ticker via SEC Fails-to-Deliver...")
    try:
        mapping = build_cusip_to_ticker_map()
        print(f"  {len(mapping)} correspondances CUSIP->Ticker récupérées.")
        sample = list(mapping.items())[:5]
        print("  Exemples:", sample)
    except Exception as e:
        print(f"  ÉCHEC: {e}")
