"""
Client pour récupérer les positions déclarées par un investisseur
institutionnel (hedge fund, gérant d'actifs...) via ses formulaires 13F-HR
déposés sur SEC EDGAR -- gratuit, aucune clé API, données publiques.

Tout gérant d'actifs gérant plus de 100M$ doit déposer trimestriellement un
13F-HR listant ses positions en actions US cotées. Contrairement aux
concepts XBRL utilisés ailleurs dans ce projet (frames/companyfacts), un
13F est un document séparé avec son propre schéma XML, d'où ce module dédié.

Un dépôt 13F contient DEUX documents, et ce module lit les deux :
  - `primary_doc.xml` : la page de couverture (type de rapport, amendement
    ou non, totaux officiels déclarés).
  - le tableau des positions ("Information Table") : une ligne par position.

Étapes :
  1. Trouver le CIK du gérant à partir de son nom (recherche EDGAR)
  2. Trouver TOUS ses dépôts 13F-HR *et 13F-HR/A* sur la période demandée
  3. Parser la couverture + le tableau de positions de chaque dépôt
  4. Recomposer chaque trimestre en appliquant ses éventuels amendements

Point technique important : le fichier "submissions" de la SEC ne liste en
direct ("recent") que les dépôts les plus récents, tous types confondus
(13F, 13D, correspondance...). Pour un gérant qui dépose beaucoup (comme
Berkshire Hathaway), ses 13F-HR d'il y a plusieurs années peuvent être
repoussés hors de cette liste "recent" -- on va alors chercher dans les
fichiers d'archives paginés listés dans "filings.files" pour ne pas rater
de trimestres.

=== TROIS PIÈGES DU FORMAT 13F, traités ici ===

1. **Une position = PLUSIEURS lignes.** Un gérant qui déclare pour le
   compte de plusieurs sous-gérants (champ `otherManager`) éclate la même
   position sur autant de lignes qu'il y a de sous-gérants. Berkshire
   déclare ainsi Apple sur une dizaine de lignes. Il faut les ADDITIONNER :
   ne garder qu'une ligne par CUSIP (ce que faisait une version précédente
   de ce projet) revient à ne conserver qu'une miette de la position --
   Apple apparaissait figé à 692 000 titres au lieu de ~887 millions.

2. **L'unité de la colonne `value` a changé.** Jusqu'aux dépôts de fin
   2022, elle était exprimée en MILLIERS de dollars. Depuis les dépôts
   déposés à partir du 3 janvier 2023 (amendements SEC de 2022), elle est
   en dollars ENTIERS. Multiplier systématiquement par 1000 gonfle donc
   tous les dépôts récents d'un facteur 1000. Voir `_value_multiplier()`,
   doublé d'un garde-fou statistique (`_sanity_check_scale()`).

3. **`putCall` change complètement le sens d'une ligne.** Une ligne peut
   être une position longue en actions, mais aussi un PUT ou un CALL. Le
   nombre déclaré est alors le nombre d'actions SOUS-JACENTES (le
   notionnel), pas la prime engagée. Ignorer ce champ revient à afficher
   un pari baissier comme une conviction haussière -- une erreur totale
   pour un gérant comme Scion Asset Management (Michael Burry), dont les
   positions les plus médiatisées sont des puts.
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

DEFAULT_YEARS = 5

# Formulaires retenus : le dépôt initial du trimestre ET ses amendements.
# Une version précédente ne prenait que "13F-HR", donc ignorait purement et
# simplement les corrections déposées ensuite par le gérant.
FORM_ORIGINAL = "13F-HR"
FORM_AMENDMENT = "13F-HR/A"

# Depuis les dépôts effectués à partir de cette date, la colonne `value`
# d'un 13F est en dollars entiers ; avant, elle était en milliers de
# dollars (amendements SEC du Form 13F, adoptés en octobre 2022).
VALUE_IN_WHOLE_DOLLARS_FROM = pd.Timestamp("2023-01-03")

# Garde-fou : bornes du prix unitaire MÉDIAN (valeur / nombre de titres)
# d'un dépôt entier, au-delà desquelles l'unité déclarée n'est pas celle
# attendue. Bornes volontairement très larges : il ne s'agit pas de juger
# une position isolée (une action à 0,50$ existe) mais un portefeuille
# complet, dont le prix médian ne peut pas être inférieur à 1$ ni dépasser
# 100 000$ -- seul un fonds investi uniquement en Berkshire classe A
# (~700 000$ l'action) s'en approcherait, cas assez rare pour être écarté.
MIN_PLAUSIBLE_PRICE = 1.0
MAX_PLAUSIBLE_PRICE = 100_000.0

# Colonnes identifiant une position UNIQUE au sein d'un trimestre. Deux
# lignes qui partagent ces valeurs sont deux morceaux de la même position
# (déclarés par des sous-gérants différents) et doivent être additionnées.
# Le put/call et la classe de titre en font partie : un put Nvidia et une
# position longue Nvidia partagent le même CUSIP mais n'ont rien à voir.
POSITION_KEYS = ["cusip", "title_of_class", "put_call", "shrs_prn_type"]


def _local(tag: str) -> str:
    """Nom de balise sans son espace de noms XML ('{...}value' -> 'value')."""
    return tag.rsplit("}", 1)[-1]


def _text_of(element, *path) -> str:
    """
    Cherche une valeur texte en descendant par noms de balise LOCAUX, sans
    dépendre de l'espace de noms -- la SEC en a plusieurs (un pour la
    couverture, un pour le tableau de positions) et leurs URLs ont changé
    au fil des révisions du schéma. Comparer sur le nom local rend le
    parsing insensible à ces variations.
    """
    current = element
    for name in path:
        found = None
        for child in current:
            if _local(child.tag) == name:
                found = child
                break
        if found is None:
            return None
        current = found
    return current.text.strip() if current.text else None


def _iter_local(element, name):
    """Tous les descendants dont le nom de balise local est `name`."""
    for child in element.iter():
        if _local(child.tag) == name:
            yield child


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

    conformed = re.findall(r"<conformed-name>([^<]+)</conformed-name>", resp.text)
    if conformed:
        # Journalisé pour repérer un mauvais appariement : la recherche EDGAR
        # est floue, "Berkshire" peut renvoyer un homonyme sans rapport et on
        # tracerait alors le portefeuille de quelqu'un d'autre sans le voir.
        print(f"[hedge_fund_13f] '{name}' -> CIK {ciks[0].zfill(10)} ({conformed[0]})")
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
    Retourne la liste des dépôts 13F-HR ET 13F-HR/A des `years` dernières
    années pour un CIK donné, du plus ancien au plus récent (ordre de dépôt
    -- c'est celui dans lequel les amendements doivent être appliqués).
    """
    all_filings = _fetch_all_filings_metadata(cik)
    thirteen_f = all_filings[all_filings["form"].isin([FORM_ORIGINAL, FORM_AMENDMENT])].copy()

    if thirteen_f.empty:
        return []

    thirteen_f["filingDate"] = pd.to_datetime(thirteen_f["filingDate"])
    cutoff = pd.Timestamp.today() - pd.DateOffset(years=years)
    thirteen_f = thirteen_f[thirteen_f["filingDate"] >= cutoff]
    thirteen_f = thirteen_f.sort_values(["filingDate", "accessionNumber"])
    return thirteen_f.to_dict("records")


def _list_filing_documents(cik: str, accession_number: str) -> tuple:
    """
    Liste le contenu du dossier d'un dépôt et retourne
    (url_de_base, [noms de fichiers]) -- un seul appel réseau, réutilisé
    pour localiser À LA FOIS la page de couverture et le tableau de
    positions (leurs noms exacts varient d'un dépôt à l'autre).
    """
    cik_int = int(cik)
    accession_nodash = accession_number.replace("-", "")
    base_url = ARCHIVE_INDEX_URL.format(cik_int=cik_int, accession_nodash=accession_nodash)

    resp = requests.get(base_url + "index.json", headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    items = resp.json().get("directory", {}).get("item", [])
    return base_url, [item["name"] for item in items]


def _pick_information_table(filenames: list) -> str:
    """
    Choisit le fichier XML du tableau de positions parmi les fichiers du
    dépôt. On écarte explicitement la page de couverture (primary_doc), qui
    est un XML valide mais ne contient aucune position.
    """
    xmls = [f for f in filenames if f.lower().endswith(".xml") and "primary" not in f.lower()]
    named = [f for f in xmls if "info" in f.lower() or "table" in f.lower()]
    candidates = named or xmls
    return candidates[0] if candidates else None


def _pick_primary_doc(filenames: list) -> str:
    """Choisit le fichier XML de la page de couverture."""
    for f in filenames:
        if f.lower().endswith(".xml") and "primary" in f.lower():
            return f
    return None


def _parse_cover_page(xml_content: bytes) -> dict:
    """
    Parse la page de couverture d'un 13F.

    Ce document porte des informations qu'aucune ligne de position ne
    donne : s'agit-il d'un amendement (et de quel type), le gérant
    déclare-t-il lui-même ses positions ou renvoie-t-il vers un autre
    gérant, et surtout les TOTAUX officiels -- qui servent de contrôle
    indépendant pour vérifier qu'on n'a pas raté de lignes au parsing.
    """
    root = ET.fromstring(xml_content)

    cover = next(_iter_local(root, "coverPage"), None)
    summary = next(_iter_local(root, "summaryPage"), None)

    info = {
        "manager_name": None,
        "report_type": None,
        "is_amendment": False,
        "amendment_type": None,
        "confidential_omitted": False,
        "declared_entry_total": None,
        "declared_value_total": None,
        "other_managers_count": None,
    }

    if cover is not None:
        info["manager_name"] = _text_of(cover, "filingManager", "name")
        info["report_type"] = _text_of(cover, "reportType")
        is_amendment = (_text_of(cover, "isAmendment") or "").strip().lower()
        info["is_amendment"] = is_amendment in ("true", "y", "yes", "1")
        info["amendment_type"] = _text_of(cover, "amendmentInfo", "amendmentType")
        # "provideInfoForInstruction5" à "N" signale des positions retirées
        # du tableau au titre du traitement confidentiel : le portefeuille
        # publié est alors sciemment incomplet.
        info["confidential_omitted"] = (_text_of(cover, "provideInfoForInstruction5") or "").strip().upper() == "N"

    if summary is not None:
        entry_total = _text_of(summary, "tableEntryTotal")
        value_total = _text_of(summary, "tableValueTotal")
        managers = _text_of(summary, "otherIncludedManagersCount")
        info["declared_entry_total"] = int(entry_total) if entry_total and entry_total.isdigit() else None
        info["declared_value_total"] = float(value_total.replace(",", "")) if value_total else None
        info["other_managers_count"] = int(managers) if managers and managers.isdigit() else None

    return info


def _parse_information_table(xml_content: bytes) -> list:
    """
    Parse le tableau de positions d'un 13F en liste de dicts BRUTS (valeurs
    telles que déclarées, sans conversion d'unité -- voir
    `_value_multiplier`, appliqué ensuite au niveau du dépôt entier).

    Tous les champs du schéma sont extraits, y compris ceux qu'une version
    précédente laissait tomber et qui changent le sens d'une ligne :
    `putCall` (option plutôt qu'action), `sshPrnamtType` (SH = titres,
    PRN = montant nominal d'obligation) et `titleOfClass` (classe d'action).
    """
    root = ET.fromstring(xml_content)
    records = []

    for info_table in _iter_local(root, "infoTable"):
        shares = _text_of(info_table, "shrsOrPrnAmt", "sshPrnamt")
        prn_type = _text_of(info_table, "shrsOrPrnAmt", "sshPrnamtType")
        put_call = _text_of(info_table, "putCall")

        records.append({
            "name_of_issuer": _text_of(info_table, "nameOfIssuer"),
            "title_of_class": _text_of(info_table, "titleOfClass"),
            "cusip": _text_of(info_table, "cusip"),
            "raw_value": _text_of(info_table, "value"),
            "shares": shares,
            # Normalisé en majuscules : les gérants écrivent aussi bien
            # "Put", "PUT" que "put", et une comparaison exacte plus loin
            # (filtrage des options) raterait sinon des lignes.
            "shrs_prn_type": (prn_type or "SH").strip().upper(),
            "put_call": put_call.strip().upper() if put_call else None,
            "investment_discretion": _text_of(info_table, "investmentDiscretion"),
            "other_manager": _text_of(info_table, "otherManager"),
            "voting_sole": _text_of(info_table, "votingAuthority", "Sole"),
            "voting_shared": _text_of(info_table, "votingAuthority", "Shared"),
            "voting_none": _text_of(info_table, "votingAuthority", "None"),
        })

    return records


def _value_multiplier(filing_date) -> int:
    """
    Facteur de conversion de la colonne `value` vers des dollars entiers,
    selon la DATE DE DÉPÔT (pas la date du trimestre) : la règle SEC porte
    sur le moment où le document est déposé. Un 13F du trimestre 2022-Q4
    déposé en février 2023 est donc déjà en dollars entiers.
    """
    filing_date = pd.to_datetime(filing_date)
    return 1 if filing_date >= VALUE_IN_WHOLE_DOLLARS_FROM else 1000


def _sanity_check_scale(df: pd.DataFrame, label: str) -> pd.DataFrame:
    """
    Garde-fou indépendant de la règle de date : recalcule le prix unitaire
    implicite (valeur / nombre de titres) sur les lignes d'actions
    ordinaires du dépôt. Si le prix médian obtenu est absurde, c'est que
    l'unité déclarée n'est pas celle attendue -- certains gérants ont
    continué à déclarer en milliers après le changement de règle, et
    quelques-uns déclaraient déjà en dollars avant.

    On corrige alors d'un facteur 1000 et on le journalise, plutôt que de
    publier un portefeuille faux d'un facteur 1000 en silence.
    """
    plain = df[(df["put_call"].isna()) & (df["shrs_prn_type"] == "SH") & (df["shares"] > 0)]
    if plain.empty:
        return df

    median_price = (plain["value_usd"] / plain["shares"]).median()
    if pd.isna(median_price) or median_price == 0:
        return df

    # On ne corrige que si le facteur 1000 ramène EFFECTIVEMENT le prix
    # médian dans la fourchette plausible -- sinon le problème vient
    # d'ailleurs (données corrompues, quantités aberrantes) et appliquer un
    # facteur au hasard ne ferait que déplacer l'erreur.
    if median_price < MIN_PLAUSIBLE_PRICE and MIN_PLAUSIBLE_PRICE <= median_price * 1000 <= MAX_PLAUSIBLE_PRICE:
        print(f"  [correction d'unité] {label}: prix unitaire médian implicite de {median_price:.4f}$ "
              "-- valeurs manifestement en milliers, multipliées par 1000.")
        df["value_usd"] = df["value_usd"] * 1000
    elif median_price > MAX_PLAUSIBLE_PRICE and MIN_PLAUSIBLE_PRICE <= median_price / 1000 <= MAX_PLAUSIBLE_PRICE:
        print(f"  [correction d'unité] {label}: prix unitaire médian implicite de {median_price:,.0f}$ "
              "-- valeurs manifestement déjà en dollars, divisées par 1000.")
        df["value_usd"] = df["value_usd"] / 1000
    elif not (MIN_PLAUSIBLE_PRICE <= median_price <= MAX_PLAUSIBLE_PRICE):
        print(f"  [avertissement] {label}: prix unitaire médian implicite de {median_price:,.4f}$, hors de toute "
              "fourchette plausible et non explicable par un facteur 1000 -- données laissées telles quelles.")

    return df


def _aggregate_positions(records: list, filing: dict, label: str) -> pd.DataFrame:
    """
    Transforme les lignes brutes d'un dépôt en positions consolidées : une
    ligne par (CUSIP, classe, put/call, type de quantité), les morceaux
    déclarés par les différents sous-gérants étant ADDITIONNÉS.

    C'est l'étape qui manquait : sans elle, une position éclatée sur dix
    sous-gérants n'était comptée que pour un dixième de sa taille réelle.
    """
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["value_usd"] = pd.to_numeric(df["raw_value"], errors="coerce") * _value_multiplier(filing["filingDate"])
    df["shares"] = pd.to_numeric(df["shares"], errors="coerce")
    for col in ["voting_sole", "voting_shared", "voting_none"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df = _sanity_check_scale(df, label)

    n_raw = len(df)
    grouped = df.groupby(POSITION_KEYS, dropna=False).agg(
        name_of_issuer=("name_of_issuer", "first"),
        value_usd=("value_usd", "sum"),
        shares=("shares", "sum"),
        voting_sole=("voting_sole", "sum"),
        voting_shared=("voting_shared", "sum"),
        voting_none=("voting_none", "sum"),
        investment_discretion=("investment_discretion", "first"),
        n_source_rows=("cusip", "size"),
    ).reset_index()

    if n_raw != len(grouped):
        print(f"  [agrégation] {label}: {n_raw} lignes déclarées consolidées en {len(grouped)} positions "
              f"(déclaration éclatée entre sous-gérants).")

    return grouped


def _fetch_filing(cik: str, filing: dict) -> tuple:
    """
    Récupère un dépôt complet : (couverture, positions consolidées).
    Trois requêtes réseau -- l'index du dossier, la couverture, le tableau.
    """
    base_url, filenames = _list_filing_documents(cik, filing["accessionNumber"])
    label = f"{pd.to_datetime(filing['reportDate']).date() if filing.get('reportDate') else '?'} ({filing['form']})"

    cover = {}
    primary_name = _pick_primary_doc(filenames)
    if primary_name:
        time.sleep(0.15)  # usage responsable de la SEC
        resp = requests.get(base_url + primary_name, headers=HEADERS, timeout=TIMEOUT)
        if resp.ok:
            try:
                cover = _parse_cover_page(resp.content)
            except ET.ParseError as e:
                print(f"  [avertissement] couverture illisible pour {label}: {e} -- totaux de contrôle indisponibles.")

    table_name = _pick_information_table(filenames)
    if not table_name:
        # Cas légitime : un "13F NOTICE" déclare que les positions sont
        # reportées par un autre gérant et ne contient aucun tableau. Ce
        # n'est pas une erreur, et surtout pas un portefeuille vide.
        report_type = (cover.get("report_type") or "").upper()
        if "NOTICE" in report_type:
            print(f"  [info] {label}: dépôt de type NOTICE (positions déclarées par un autre gérant) -- aucun tableau.")
            return cover, pd.DataFrame()
        raise RuntimeError(
            f"[hedge_fund_13f] Aucun tableau de positions trouvé dans le dépôt "
            f"{filing['accessionNumber']} (CIK {cik}). Fichiers présents: {filenames}"
        )

    time.sleep(0.15)
    resp = requests.get(base_url + table_name, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    positions = _aggregate_positions(_parse_information_table(resp.content), filing, label)

    return cover, positions


def _check_against_declared_total(positions: pd.DataFrame, cover: dict, filing: dict, label: str):
    """
    Compare notre total parsé au total OFFICIEL de la page de couverture --
    un contrôle indépendant, gratuit, qui détecte immédiatement un tableau
    partiellement lu (fichier tronqué, lignes non reconnues).
    """
    declared = cover.get("declared_value_total")
    if not declared or positions.empty:
        return None

    declared_usd = declared * _value_multiplier(filing["filingDate"])
    parsed_usd = positions["value_usd"].sum()
    if declared_usd <= 0:
        return None

    ratio = parsed_usd / declared_usd
    # Tolérance large : le total déclaré est arrondi par le gérant, et un
    # écart de quelques pourcents est courant. On ne signale que les écarts
    # qui trahissent un vrai problème de parsing.
    if not (0.95 <= ratio <= 1.05) and not (0.95 <= ratio * 1000 <= 1.05) and not (0.95 <= ratio / 1000 <= 1.05):
        print(f"  [avertissement] {label}: total parsé {parsed_usd:,.0f}$ vs {declared_usd:,.0f}$ déclarés "
              f"sur la couverture ({ratio:.2f}x) -- parsing possiblement incomplet.")
    return declared_usd


def _apply_amendments(filings_for_quarter: list) -> pd.DataFrame:
    """
    Recompose les positions d'UN trimestre à partir de son dépôt initial et
    de ses éventuels amendements, appliqués dans l'ordre de dépôt.

    Deux types d'amendement, au sens opposé (d'où la nécessité de lire la
    page de couverture pour les distinguer) :
      - RESTATEMENT : le tableau REMPLACE intégralement le précédent.
      - NEW HOLDINGS : le tableau AJOUTE des positions au précédent.
    Un amendement sans type déclaré est traité comme un remplacement, cas
    de loin le plus fréquent en pratique.
    """
    current = pd.DataFrame()

    for cover, positions, filing in filings_for_quarter:
        amendment_type = (cover.get("amendment_type") or "").upper()
        is_amendment = cover.get("is_amendment") or filing["form"] == FORM_AMENDMENT

        if not is_amendment:
            current = positions
        elif "NEW HOLDINGS" in amendment_type:
            current = pd.concat([current, positions], ignore_index=True) if not current.empty else positions
            if not current.empty:
                current = current.groupby(POSITION_KEYS, dropna=False).agg({
                    "name_of_issuer": "first", "value_usd": "sum", "shares": "sum",
                    "voting_sole": "sum", "voting_shared": "sum", "voting_none": "sum",
                    "investment_discretion": "first", "n_source_rows": "sum",
                }).reset_index()
        else:
            # RESTATEMENT (ou type non déclaré) : un tableau vide est une
            # correction légitime seulement s'il est explicite ; sinon on
            # garde ce qu'on avait plutôt que d'effacer le trimestre.
            if not positions.empty:
                current = positions

    return current


def get_13f_holdings_history(name: str, years: int = DEFAULT_YEARS) -> pd.DataFrame:
    """
    Retourne l'historique COMPLET des positions déclarées sur `years`
    années -- une ligne par (trimestre, position), avec le nombre RÉEL de
    titres détenus à chaque trimestre déclaré (pas une estimation), les
    puts et calls identifiés comme tels, et les déclarations éclatées entre
    sous-gérants correctement additionnées.

    Returns:
        DataFrame avec colonnes: report_date, filing_date, accession, form,
        name_of_issuer, cusip, title_of_class, put_call, shrs_prn_type,
        value_usd, shares, voting_sole/shared/none, investment_discretion,
        declared_value_total, confidential_omitted, manager_name
    """
    cik = find_cik_for_manager(name)
    filings = get_13f_filings_history(cik, years=years)
    if not filings:
        raise RuntimeError(f"[hedge_fund_13f] Aucun dépôt 13F trouvé pour '{name}' sur {years} ans.")

    n_amendments = sum(1 for f in filings if f["form"] == FORM_AMENDMENT)
    print(f"[hedge_fund_13f] {len(filings)} dépôts 13F trouvés pour '{name}' sur {years} ans "
          f"({n_amendments} amendement(s)).")

    # Regroupés par trimestre : un trimestre peut avoir plusieurs dépôts
    # (l'original puis ses corrections), qu'il faut fusionner entre eux.
    by_quarter = {}
    for filing in filings:
        label = f"{filing.get('reportDate') or '?'} ({filing['form']})"
        try:
            cover, positions = _fetch_filing(cik, filing)
            _check_against_declared_total(positions, cover, filing, label)
            by_quarter.setdefault(filing["reportDate"], []).append((cover, positions, filing))
            print(f"  [hedge_fund_13f] {label}: {len(positions)} positions.")
        except Exception as e:
            print(f"  [avertissement] échec du dépôt {filing['accessionNumber']} ({label}): {e} "
                  "-- ce dépôt est ignoré.")
            continue

    all_frames = []
    for report_date, quarter_filings in by_quarter.items():
        positions = _apply_amendments(quarter_filings)
        if positions.empty:
            continue

        last_cover, _, last_filing = quarter_filings[-1]
        positions = positions.copy()
        positions["report_date"] = report_date
        positions["filing_date"] = last_filing["filingDate"]
        positions["accession"] = last_filing["accessionNumber"]
        positions["form"] = last_filing["form"]
        positions["manager_name"] = last_cover.get("manager_name")
        positions["confidential_omitted"] = bool(last_cover.get("confidential_omitted"))
        declared = last_cover.get("declared_value_total")
        positions["declared_value_total"] = (
            declared * _value_multiplier(last_filing["filingDate"]) if declared else None
        )
        all_frames.append(positions)

    if not all_frames:
        raise RuntimeError(f"[hedge_fund_13f] Aucune position exploitable récupérée pour '{name}'.")

    df = pd.concat(all_frames, ignore_index=True)
    df["report_date"] = pd.to_datetime(df["report_date"])
    df["filing_date"] = pd.to_datetime(df["filing_date"])
    return df.sort_values(["report_date", "value_usd"], ascending=[True, False]).reset_index(drop=True)


def get_13f_holdings(name: str) -> pd.DataFrame:
    """
    Retourne les positions du trimestre le PLUS RÉCENT uniquement.
    Raccourci pratique -- pour l'historique, utiliser
    get_13f_holdings_history().
    """
    df = get_13f_holdings_history(name, years=1)
    if df.empty:
        raise RuntimeError(f"[hedge_fund_13f] Aucun dépôt 13F récent trouvé pour '{name}'.")
    return df[df["report_date"] == df["report_date"].max()].reset_index(drop=True)


if __name__ == "__main__":
    for manager in ["Berkshire Hathaway", "Scion Asset Management"]:
        print(f"\n=== Test historique 13F sur 2 ans : {manager} ===")
        try:
            df = get_13f_holdings_history(manager, years=2)
            print(f"  {len(df)} positions au total, {df['report_date'].nunique()} trimestres.")
            last = df[df["report_date"] == df["report_date"].max()]
            total = last["value_usd"].sum()
            print(f"  Dernier trimestre ({last['report_date'].iloc[0].date()}): "
                  f"{len(last)} positions, {total/1e9:,.1f} Md$.")
            for _, row in last.nlargest(5, "value_usd").iterrows():
                kind = row["put_call"] or "ACTIONS"
                print(f"    {row['name_of_issuer'][:30]:30} {kind:8} "
                      f"{row['shares']:>14,.0f} titres  {row['value_usd']/total*100:5.1f}%")
        except Exception as e:
            print(f"  ÉCHEC: {e}")
