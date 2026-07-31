"""
Client pour récupérer les positions déclarées par un investisseur
institutionnel (hedge fund, gérant d'actifs...) via ses formulaires 13F-HR
déposés sur SEC EDGAR -- gratuit, aucune clé API, données publiques.

Tout gérant d'actifs gérant plus de 100M$ doit déposer trimestriellement un
13F-HR listant ses positions en actions US cotées. Contrairement aux
concepts XBRL utilisés ailleurs dans ce projet (frames/companyfacts), un
13F est un document séparé avec son propre schéma XML ("Information
Table"), d'où ce module dédié.

Étapes :
  1. Trouver le CIK du gérant à partir de son nom (recherche EDGAR)
  2. Trouver son dépôt 13F-HR le plus récent
  3. Localiser et parser le fichier XML "Information Table" de ce dépôt
"""
import os
import re
import requests
import pandas as pd

# La SEC EXIGE un User-Agent au format "Nom Prénom email@exemple.com" -- un
# texte générique se fait bloquer avec une erreur 403. Cette variable
# d'environnement suit exactement le même principe que EDGAR_USER_AGENT
# dans le projet research-dashboard principal.
EDGAR_USER_AGENT = os.environ.get("EDGAR_USER_AGENT", "")
if not EDGAR_USER_AGENT:
    print("[hedge_fund_13f] ATTENTION: EDGAR_USER_AGENT n'est pas définie. "
          "La SEC exige un User-Agent identifiable (nom + email), sinon les requêtes "
          "seront bloquées avec une erreur 403.")
HEADERS = {"User-Agent": EDGAR_USER_AGENT or "pilot-tracker contact@example.com"}
TIMEOUT = 30

SEARCH_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/"

# Espace de noms XML du tableau de positions 13F (schéma standard de la SEC)
INFO_TABLE_NAMESPACE = "{http://www.sec.gov/edgar/document/thirteenf/informationtable}"


def find_cik_for_manager(name: str) -> str:
    """
    Cherche le CIK d'un gérant d'actifs par son nom, en filtrant sur les
    entreprises ayant déposé au moins un 13F-HR.

    Retourne le CIK (chaîne, zero-paddée sur 10 chiffres), ou lève une
    exception claire si aucune correspondance n'est trouvée.
    """
    params = {
        "action": "getcompany",
        "company": name,
        "type": "13F-HR",
        "dateb": "",
        "owner": "include",
        "count": "10",
        "output": "atom",
    }
    resp = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()

    # Le flux Atom contient le CIK dans des balises <cik>NNNNNNNNNN</cik>
    matches = re.findall(r"<CIK>(\d+)</CIK>|<cik>(\d+)</cik>", resp.text)
    ciks = [m[0] or m[1] for m in matches]

    if not ciks:
        raise RuntimeError(
            f"[hedge_fund_13f] Aucun gérant trouvé pour '{name}' ayant déposé un 13F-HR. "
            "Vérifie l'orthographe exacte du nom tel qu'enregistré auprès de la SEC."
        )
    return ciks[0].zfill(10)


def get_latest_13f_filing(cik: str) -> dict:
    """
    Retourne les métadonnées (accessionNumber, filingDate, reportDate) du
    dépôt 13F-HR le plus récent pour un CIK donné.
    """
    url = SUBMISSIONS_URL.format(cik=cik)
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accession_numbers = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])

    for i, form in enumerate(forms):
        if form == "13F-HR":
            return {
                "accessionNumber": accession_numbers[i],
                "filingDate": filing_dates[i],
                "reportDate": report_dates[i] if i < len(report_dates) else None,
            }

    raise RuntimeError(
        f"[hedge_fund_13f] Aucun dépôt 13F-HR trouvé dans les soumissions récentes du CIK {cik}."
    )


def _find_information_table_url(cik: str, accession_number: str) -> str:
    """
    Localise le fichier XML "Information Table" (le tableau des positions)
    dans le dossier du dépôt -- son nom exact varie d'un dépôt à l'autre,
    donc on liste le contenu du dossier et on cherche un candidat plausible.
    """
    cik_int = int(cik)
    accession_nodash = accession_number.replace("-", "")
    index_url = ARCHIVE_INDEX_URL.format(cik_int=cik_int, accession_nodash=accession_nodash) + "index.json"

    resp = requests.get(index_url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    items = resp.json().get("directory", {}).get("item", [])

    candidates = [
        item["name"] for item in items
        if item["name"].lower().endswith(".xml")
        and ("info" in item["name"].lower() or "table" in item["name"].lower())
    ]
    if not candidates:
        # repli : parfois un seul fichier XML existe dans le dossier (hors "primary_doc")
        candidates = [
            item["name"] for item in items
            if item["name"].lower().endswith(".xml") and "primary" not in item["name"].lower()
        ]

    if not candidates:
        raise RuntimeError(
            f"[hedge_fund_13f] Aucun fichier XML de tableau de positions trouvé dans le dépôt "
            f"{accession_number} (CIK {cik}). Fichiers présents: {[i['name'] for i in items]}"
        )

    base_url = ARCHIVE_INDEX_URL.format(cik_int=cik_int, accession_nodash=accession_nodash)
    return base_url + candidates[0]


def get_13f_holdings(name: str) -> pd.DataFrame:
    """
    Fonction principale : à partir du nom d'un gérant, retourne ses
    dernières positions déclarées (13F-HR le plus récent).

    Returns:
        DataFrame avec colonnes: name_of_issuer, cusip, value_usd, shares,
        report_date, filing_date
    """
    cik = find_cik_for_manager(name)
    filing = get_latest_13f_filing(cik)
    xml_url = _find_information_table_url(cik, filing["accessionNumber"])

    resp = requests.get(xml_url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()

    import xml.etree.ElementTree as ET
    root = ET.fromstring(resp.content)

    ns = INFO_TABLE_NAMESPACE
    records = []
    for info_table in root.findall(f".//{ns}infoTable"):
        def _find_text(tag):
            el = info_table.find(f"{ns}{tag}")
            return el.text.strip() if el is not None and el.text else None

        shares_el = info_table.find(f"{ns}shrsOrPrnAmt/{ns}sshPrnamt")
        shares = shares_el.text.strip() if shares_el is not None and shares_el.text else None

        records.append({
            "name_of_issuer": _find_text("nameOfIssuer"),
            "cusip": _find_text("cusip"),
            "value_usd": _find_text("value"),
            "shares": shares,
            "report_date": filing["reportDate"],
            "filing_date": filing["filingDate"],
        })

    df = pd.DataFrame(records)
    if not df.empty:
        df["value_usd"] = pd.to_numeric(df["value_usd"], errors="coerce") * 1000  # 13F déclare en milliers de $
        df["shares"] = pd.to_numeric(df["shares"], errors="coerce")

    return df


if __name__ == "__main__":
    # Test rapide et isolé -- à lancer avant de brancher le simulateur dessus.
    print("Test 13F pour Berkshire Hathaway...")
    try:
        df = get_13f_holdings("Berkshire Hathaway")
        print(f"  {len(df)} positions récupérées.")
        if not df.empty:
            print(df.sort_values("value_usd", ascending=False).head())
    except Exception as e:
        print(f"  ÉCHEC: {e}")
