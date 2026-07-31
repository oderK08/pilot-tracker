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
  2. Trouver TOUS ses dépôts 13F-HR sur la période demandée (par défaut 5 ans)
  3. Localiser et parser le fichier XML "Information Table" de chaque dépôt

Point technique important : le fichier "submissions" de la SEC ne liste en
direct ("recent") que les dépôts les plus récents, tous types confondus
(13F, 13D, correspondance...). Pour un gérant qui dépose beaucoup (comme
Berkshire Hathaway), ses 13F-HR d'il y a plusieurs années peuvent être
repoussés hors de cette liste "recent" -- on va alors chercher dans les
fichiers d'archives paginés listés dans "filings.files" pour ne pas rater
de trimestres.
"""
import os
import re
import time
import xml.etree.ElementTree as ET

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
SUBMISSIONS_ARCHIVE_URL = "https://data.sec.gov/submissions/{filename}"
ARCHIVE_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/"

# Espace de noms XML du tableau de positions 13F (schéma standard de la SEC)
INFO_TABLE_NAMESPACE = "{http://www.sec.gov/edgar/document/thirteenf/informationtable}"

DEFAULT_YEARS = 5


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

    matches = re.findall(r"<CIK>(\d+)</CIK>|<cik>(\d+)</cik>", resp.text)
    ciks = [m[0] or m[1] for m in matches]

    if not ciks:
        raise RuntimeError(
            f"[hedge_fund_13f] Aucun gérant trouvé pour '{name}' ayant déposé un 13F-HR. "
            "Vérifie l'orthographe exacte du nom tel qu'enregistré auprès de la SEC."
        )
    return ciks[0].zfill(10)


def _fetch_all_filings_metadata(cik: str) -> pd.DataFrame:
    """
    Récupère TOUTES les métadonnées de dépôt disponibles pour un CIK : la
    liste "recent" (dépôts les plus récents, tous types confondus) PLUS les
    fichiers d'archives paginés référencés dans "filings.files" (dépôts plus
    anciens) -- nécessaire pour ne pas rater de vieux 13F-HR chez un gérant
    qui dépose beaucoup d'autres documents.
    """
    url = SUBMISSIONS_URL.format(cik=cik)
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    recent = data.get("filings", {}).get("recent", {})
    all_forms = list(recent.get("form", []))
    all_accessions = list(recent.get("accessionNumber", []))
    all_filing_dates = list(recent.get("filingDate", []))
    all_report_dates = list(recent.get("reportDate", []))

    older_files = data.get("filings", {}).get("files", [])
    for file_info in older_files:
        file_url = SUBMISSIONS_ARCHIVE_URL.format(filename=file_info["name"])
        try:
            file_resp = requests.get(file_url, headers=HEADERS, timeout=TIMEOUT)
            file_resp.raise_for_status()
            file_data = file_resp.json()
            all_forms.extend(file_data.get("form", []))
            all_accessions.extend(file_data.get("accessionNumber", []))
            all_filing_dates.extend(file_data.get("filingDate", []))
            all_report_dates.extend(file_data.get("reportDate", []))
        except Exception as e:
            print(f"  [avertissement] échec de récupération des dépôts archivés ({file_url}): {e} -- ignoré")
            continue

    min_len = min(len(all_forms), len(all_accessions), len(all_filing_dates))
    return pd.DataFrame({
        "form": all_forms[:min_len],
        "accessionNumber": all_accessions[:min_len],
        "filingDate": all_filing_dates[:min_len],
        "reportDate": (all_report_dates + [None] * min_len)[:min_len],
    })


def get_13f_filings_history(cik: str, years: int = DEFAULT_YEARS) -> list:
    """
    Retourne la liste des dépôts 13F-HR des `years` dernières années pour un
    CIK donné, du plus ancien au plus récent.
    """
    all_filings = _fetch_all_filings_metadata(cik)
    thirteen_f = all_filings[all_filings["form"] == "13F-HR"].copy()

    if thirteen_f.empty:
        return []

    thirteen_f["filingDate"] = pd.to_datetime(thirteen_f["filingDate"])
    cutoff = pd.Timestamp.today() - pd.DateOffset(years=years)
    thirteen_f = thirteen_f[thirteen_f["filingDate"] >= cutoff]
    thirteen_f = thirteen_f.sort_values("filingDate")
    return thirteen_f.to_dict("records")


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


def _parse_information_table(xml_content: bytes) -> list:
    """Parse le contenu XML d'un tableau de positions 13F en liste de dicts bruts."""
    root = ET.fromstring(xml_content)
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
        })
    return records


def get_13f_holdings(name: str) -> pd.DataFrame:
    """
    Retourne les positions du dépôt 13F-HR le PLUS RÉCENT uniquement.
    Conservé pour compatibilité / usage ponctuel -- pour un historique sur
    plusieurs années, utiliser get_13f_holdings_history().

    Returns:
        DataFrame avec colonnes: name_of_issuer, cusip, value_usd, shares,
        report_date, filing_date
    """
    cik = find_cik_for_manager(name)
    filings = get_13f_filings_history(cik, years=1)
    if not filings:
        raise RuntimeError(f"[hedge_fund_13f] Aucun dépôt 13F-HR récent trouvé pour '{name}'.")
    filing = filings[-1]

    xml_url = _find_information_table_url(cik, filing["accessionNumber"])
    resp = requests.get(xml_url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()

    records = _parse_information_table(resp.content)
    for r in records:
        r["report_date"] = filing["reportDate"]
        r["filing_date"] = filing["filingDate"]

    df = pd.DataFrame(records)
    if not df.empty:
        df["value_usd"] = pd.to_numeric(df["value_usd"], errors="coerce") * 1000
        df["shares"] = pd.to_numeric(df["shares"], errors="coerce")
    return df


def get_13f_holdings_history(name: str, years: int = DEFAULT_YEARS) -> pd.DataFrame:
    """
    Retourne l'historique COMPLET des positions déclarées sur `years`
    années -- une ligne par (trimestre, ticker), avec le nombre RÉEL
    d'actions détenues à chaque trimestre déclaré (pas une estimation).

    Returns:
        DataFrame avec colonnes: report_date, filing_date, name_of_issuer,
        cusip, value_usd, shares
    """
    cik = find_cik_for_manager(name)
    filings = get_13f_filings_history(cik, years=years)
    if not filings:
        raise RuntimeError(f"[hedge_fund_13f] Aucun dépôt 13F-HR trouvé pour '{name}' sur {years} ans.")

    print(f"[hedge_fund_13f] {len(filings)} dépôts 13F-HR trouvés pour '{name}' sur {years} ans.")

    all_records = []
    for filing in filings:
        try:
            xml_url = _find_information_table_url(cik, filing["accessionNumber"])
            resp = requests.get(xml_url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            records = _parse_information_table(resp.content)
            for r in records:
                r["report_date"] = filing["reportDate"]
                r["filing_date"] = filing["filingDate"]
            all_records.extend(records)
            print(f"  [hedge_fund_13f] {filing['reportDate']}: {len(records)} positions récupérées.")
        except Exception as e:
            print(f"  [avertissement] échec du dépôt {filing['accessionNumber']} "
                  f"({filing.get('reportDate')}): {e} -- ce trimestre est ignoré.")
            continue

    df = pd.DataFrame(all_records)
    if not df.empty:
        df["value_usd"] = pd.to_numeric(df["value_usd"], errors="coerce") * 1000
        df["shares"] = pd.to_numeric(df["shares"], errors="coerce")
        df["report_date"] = pd.to_datetime(df["report_date"])
        df["filing_date"] = pd.to_datetime(df["filing_date"])
    return df


if __name__ == "__main__":
    print("Test historique 13F sur 5 ans pour Berkshire Hathaway...")
    try:
        df = get_13f_holdings_history("Berkshire Hathaway", years=5)
        print(f"  {len(df)} lignes récupérées au total, "
              f"{df['report_date'].nunique() if not df.empty else 0} trimestres distincts.")
        if not df.empty:
            print(df.sort_values("report_date").groupby("report_date").size())
    except Exception as e:
        print(f"  ÉCHEC: {e}")
