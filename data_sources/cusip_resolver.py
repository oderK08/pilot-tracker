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
import zipfile
from datetime import datetime, timedelta

import requests
import pandas as pd

BASE_URL = "https://www.sec.gov/files/data/fails-deliver-data"
TIMEOUT = 60

# La SEC exige un User-Agent identifiable sur ses requêtes, comme pour EDGAR.
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


def _generate_candidate_periods(n_months_back: int = 4):
    """
    Génère les périodes (année, mois, moitié) les plus récentes en premier.
    La donnée a un délai de publication de 2 à 6 semaines selon la période
    (la 1ère moitié d'un mois sort fin de mois, la 2ème moitié sort vers le
    15 du mois suivant), donc on remonte plusieurs mois en arrière au besoin.
    """
    candidates = []
    d = datetime.today().replace(day=1)
    for _ in range(n_months_back):
        candidates.append((d.year, d.month, "b"))
        candidates.append((d.year, d.month, "a"))
        d = (d - timedelta(days=1)).replace(day=1)  # mois précédent
    return candidates


def _find_latest_available_file() -> bytes:
    """
    Essaie les périodes les plus récentes en remontant dans le temps
    jusqu'à en trouver une disponible.
    """
    for year, month, half in _generate_candidate_periods():
        content = _try_download(year, month, half)
        if content:
            return content

    raise RuntimeError(
        "[cusip_resolver] Impossible de trouver un fichier Fails-to-Deliver récent sur "
        "plusieurs mois. Vérifie la connectivité réseau, ou si la structure d'URL de la SEC "
        "a changé (voir https://www.sec.gov/data-research/sec-markets-data/fails-deliver-data)."
    )


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


def build_cusip_to_ticker_map() -> dict:
    """
    Télécharge le fichier Fails-to-Deliver le plus récent disponible et
    construit un dict {cusip: ticker}.

    En cas de doublons (un même CUSIP apparaissant plusieurs fois dans le
    fichier, ex: plusieurs dates de règlement), on garde la dernière
    occurrence trouvée.
    """
    content = _find_latest_available_file()
    df = _parse_cusip_ticker_file(content)

    df = df.dropna(subset=["CUSIP", "SYMBOL"])
    df = df.drop_duplicates(subset="CUSIP", keep="last")
    return dict(zip(df["CUSIP"], df["SYMBOL"]))


if __name__ == "__main__":
    print("Test résolution CUSIP -> Ticker via SEC Fails-to-Deliver...")
    try:
        mapping = build_cusip_to_ticker_map()
        print(f"  {len(mapping)} correspondances CUSIP->Ticker récupérées.")
        sample = list(mapping.items())[:5]
        print("  Exemples:", sample)
    except Exception as e:
        print(f"  ÉCHEC: {e}")
