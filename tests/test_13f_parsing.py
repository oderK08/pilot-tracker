"""
Tests du parsing 13F, sur XML fabriqué à la main -- AUCUN appel réseau.

Pourquoi des fixtures locales plutôt qu'un vrai dépôt SEC : les trois bugs
que ces tests verrouillent (déclaration éclatée entre sous-gérants, unité de
la colonne `value`, puts confondus avec des positions longues) sont
justement ceux qui sont passés inaperçus parce qu'on ne regardait que des
totaux plausibles à l'œil nu. Des cas minimaux et explicites les rendent
vérifiables sans dépendre de la disponibilité d'EDGAR.

Lancement :
    python tests/test_13f_parsing.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_sources import hedge_fund_13f as h13f  # noqa: E402

INFO_NS = "http://www.sec.gov/edgar/document/thirteenf/informationtable"
COVER_NS = "http://www.sec.gov/edgar/thirteenffiler"


def _info_table(rows: str) -> bytes:
    return f'<?xml version="1.0"?><informationTable xmlns="{INFO_NS}">{rows}</informationTable>'.encode()


def _row(issuer, cusip, value, shares, put_call=None, title="COM", prn="SH", other_manager=None):
    put_call_xml = f"<putCall>{put_call}</putCall>" if put_call else ""
    other_xml = f"<otherManager>{other_manager}</otherManager>" if other_manager else ""
    return f"""
    <infoTable>
      <nameOfIssuer>{issuer}</nameOfIssuer>
      <titleOfClass>{title}</titleOfClass>
      <cusip>{cusip}</cusip>
      <value>{value}</value>
      <shrsOrPrnAmt><sshPrnamt>{shares}</sshPrnamt><sshPrnamtType>{prn}</sshPrnamtType></shrsOrPrnAmt>
      {put_call_xml}
      <investmentDiscretion>DFND</investmentDiscretion>
      {other_xml}
      <votingAuthority><Sole>{shares}</Sole><Shared>0</Shared><None>0</None></votingAuthority>
    </infoTable>"""


def _cover(is_amendment="false", amendment_type=None, value_total=None, entry_total=None,
           report_type="13F HOLDINGS REPORT", instruction5="Y"):
    amendment_xml = (f"<amendmentInfo><amendmentType>{amendment_type}</amendmentType></amendmentInfo>"
                     if amendment_type else "")
    summary = ""
    if value_total is not None:
        summary = (f"<summaryPage><otherIncludedManagersCount>3</otherIncludedManagersCount>"
                   f"<tableEntryTotal>{entry_total}</tableEntryTotal>"
                   f"<tableValueTotal>{value_total}</tableValueTotal></summaryPage>")
    return f"""<?xml version="1.0"?>
    <edgarSubmission xmlns="{COVER_NS}">
      <formData>
        <coverPage>
          <reportCalendarOrQuarter>06-30-2021</reportCalendarOrQuarter>
          <isAmendment>{is_amendment}</isAmendment>
          {amendment_xml}
          <filingManager><name>TEST CAPITAL MANAGEMENT</name></filingManager>
          <reportType>{report_type}</reportType>
          <provideInfoForInstruction5>{instruction5}</provideInfoForInstruction5>
        </coverPage>
        {summary}
      </formData>
    </edgarSubmission>""".encode()


CHECKS = []


def check(name):
    def decorator(fn):
        CHECKS.append((name, fn))
        return fn
    return decorator


# --- Bug n°1 : déclaration éclatée entre sous-gérants -------------------

@check("Les lignes d'une même position éclatée entre sous-gérants sont additionnées")
def test_aggregation_sums_managers():
    # Cas réel de Berkshire : Apple déclaré par plusieurs sous-gérants. Une
    # version précédente n'en gardait qu'UNE ligne (692 000 titres au lieu
    # de la position complète).
    # Valeurs déclarées en milliers de dollars (dépôt de 2021), au cours
    # Apple de l'époque (~137$) pour rester réaliste.
    xml = _info_table(
        _row("APPLE INC", "037833100", 94_804, 692_000, other_manager="1")
        + _row("APPLE INC", "037833100", 31_510, 230_000, other_manager="2")
        + _row("APPLE INC", "037833100", 10_686, 78_000, other_manager="4")
    )
    filing = {"filingDate": "2021-08-16", "reportDate": "2021-06-30", "form": "13F-HR"}
    df = h13f._aggregate_positions(h13f._parse_information_table(xml), filing, "test")

    assert len(df) == 1, f"attendu 1 position consolidée, obtenu {len(df)}"
    assert df["shares"].iloc[0] == 1_000_000, f"attendu 1 000 000 titres, obtenu {df['shares'].iloc[0]}"
    assert df["n_source_rows"].iloc[0] == 3
    assert df["value_usd"].iloc[0] == 137_000 * 1000, f"obtenu {df['value_usd'].iloc[0]:,.0f}$"


@check("Un put et une position longue sur le même CUSIP restent deux positions distinctes")
def test_put_not_merged_with_long():
    xml = _info_table(
        _row("NVIDIA CORP", "67066G104", 100_000, 500, put_call=None)
        + _row("NVIDIA CORP", "67066G104", 900_000, 4_500, put_call="Put")
    )
    filing = {"filingDate": "2025-11-14", "reportDate": "2025-09-30", "form": "13F-HR"}
    df = h13f._aggregate_positions(h13f._parse_information_table(xml), filing, "test")

    assert len(df) == 2, f"un put et une action longue ne doivent pas fusionner (obtenu {len(df)} ligne(s))"
    puts = df[df["put_call"] == "PUT"]
    assert len(puts) == 1 and puts["shares"].iloc[0] == 4_500


@check("Les classes d'actions et les montants nominaux d'obligations ne fusionnent pas")
def test_class_and_prn_kept_separate():
    xml = _info_table(
        _row("ALPHABET INC", "02079K305", 1_000, 10, title="CL A")
        + _row("ALPHABET INC", "02079K107", 2_000, 20, title="CL C")
        + _row("SOME CORP", "111111111", 5_000, 5_000_000, title="NOTE", prn="PRN")
    )
    filing = {"filingDate": "2024-02-14", "reportDate": "2023-12-31", "form": "13F-HR"}
    df = h13f._aggregate_positions(h13f._parse_information_table(xml), filing, "test")

    assert len(df) == 3
    assert set(df["shrs_prn_type"]) == {"SH", "PRN"}


# --- Bug n°2 : unité de la colonne `value` -----------------------------

@check("Avant 2023 la valeur est en milliers, à partir de 2023 en dollars entiers")
def test_value_multiplier_by_filing_date():
    assert h13f._value_multiplier("2022-11-14") == 1000
    assert h13f._value_multiplier("2023-02-14") == 1
    # La règle porte sur la date de DÉPÔT, pas sur le trimestre : un 13F du
    # T4 2022 déposé en février 2023 est déjà en dollars entiers.
    assert h13f._value_multiplier("2023-01-03") == 1


@check("Une valeur post-2023 n'est pas multipliée par 1000")
def test_recent_filing_not_inflated():
    # 1 000 titres à ~180$ = ~180 000$. Le bug précédent affichait 180 M$.
    xml = _info_table(_row("MICRON TECHNOLOGY INC", "595112103", 180_000, 1_000))
    filing = {"filingDate": "2025-11-14", "reportDate": "2025-09-30", "form": "13F-HR"}
    df = h13f._aggregate_positions(h13f._parse_information_table(xml), filing, "test")

    implied_price = df["value_usd"].iloc[0] / df["shares"].iloc[0]
    assert 1 < implied_price < 10_000, f"prix unitaire implicite absurde: {implied_price}"
    assert df["value_usd"].iloc[0] == 180_000


@check("Un gérant qui déclare encore en milliers après 2023 est rattrapé par le garde-fou")
def test_sanity_check_rescales_stragglers():
    # 1 000 titres déclarés 180 (milliers) après le changement de règle :
    # la règle de date donnerait 180$ pour 1 000 titres, soit 0,18$/action.
    xml = _info_table(
        _row("MICRON TECHNOLOGY INC", "595112103", 180, 1_000)
        + _row("APPLE INC", "037833100", 250, 1_000)
    )
    filing = {"filingDate": "2025-11-14", "reportDate": "2025-09-30", "form": "13F-HR"}
    df = h13f._aggregate_positions(h13f._parse_information_table(xml), filing, "test")

    implied = (df["value_usd"] / df["shares"]).median()
    assert 1 < implied < 10_000, f"le garde-fou n'a pas corrigé l'unité (prix médian {implied})"


@check("Le garde-fou ignore les puts pour estimer le prix unitaire")
def test_sanity_check_ignores_options():
    # Un put dont le notionnel domine ne doit pas fausser l'estimation du
    # prix unitaire faite sur les actions ordinaires.
    xml = _info_table(
        _row("PALANTIR TECHNOLOGIES INC", "69608A108", 900_000_000, 5_000_000, put_call="Put")
        + _row("MICRON TECHNOLOGY INC", "595112103", 180_000, 1_000)
    )
    filing = {"filingDate": "2025-11-14", "reportDate": "2025-09-30", "form": "13F-HR"}
    df = h13f._aggregate_positions(h13f._parse_information_table(xml), filing, "test")

    micron = df[df["cusip"] == "595112103"]
    assert micron["value_usd"].iloc[0] == 180_000, "la valeur des actions a été rééchelonnée à tort"


# --- Page de couverture et amendements ---------------------------------

@check("La page de couverture livre le type de rapport, les totaux et le drapeau confidentiel")
def test_cover_page():
    info = h13f._parse_cover_page(_cover(value_total="1234567", entry_total="42", instruction5="N"))
    assert info["manager_name"] == "TEST CAPITAL MANAGEMENT"
    assert info["is_amendment"] is False
    assert info["declared_value_total"] == 1234567.0
    assert info["declared_entry_total"] == 42
    assert info["other_managers_count"] == 3
    assert info["confidential_omitted"] is True, "les positions omises pour confidentialité doivent être signalées"


@check("Un amendement RESTATEMENT remplace le trimestre, NEW HOLDINGS s'y ajoute")
def test_amendments():
    filing_o = {"filingDate": "2021-08-16", "reportDate": "2021-06-30", "form": "13F-HR",
                "accessionNumber": "0000000000-21-000001"}
    filing_a = {"filingDate": "2021-09-01", "reportDate": "2021-06-30", "form": "13F-HR/A",
                "accessionNumber": "0000000000-21-000002"}

    original = h13f._aggregate_positions(
        h13f._parse_information_table(_info_table(_row("APPLE INC", "037833100", 1_000, 100))),
        filing_o, "test")
    extra = h13f._aggregate_positions(
        h13f._parse_information_table(_info_table(_row("KRAFT HEINZ CO", "500754106", 2_000, 200))),
        filing_a, "test")

    restated = h13f._apply_amendments([
        (_cover_info(), original, filing_o),
        (_cover_info(is_amendment=True, amendment_type="RESTATEMENT"), extra, filing_a),
    ])
    assert len(restated) == 1 and restated["cusip"].iloc[0] == "500754106", \
        "un RESTATEMENT doit remplacer le tableau, pas s'y ajouter"

    added = h13f._apply_amendments([
        (_cover_info(), original, filing_o),
        (_cover_info(is_amendment=True, amendment_type="NEW HOLDINGS"), extra, filing_a),
    ])
    assert len(added) == 2, "un NEW HOLDINGS doit compléter le tableau existant"


@check("Un dépôt de type NOTICE ne se confond pas avec un portefeuille vide")
def test_notice_report_type():
    info = h13f._parse_cover_page(_cover(report_type="13F NOTICE"))
    assert "NOTICE" in info["report_type"].upper()


def _cover_info(is_amendment=False, amendment_type=None):
    return {"is_amendment": is_amendment, "amendment_type": amendment_type,
            "manager_name": "TEST", "declared_value_total": None, "confidential_omitted": False}


if __name__ == "__main__":
    failures = 0
    for name, fn in CHECKS:
        try:
            fn()
            print(f"  ✅ {name}")
        except AssertionError as e:
            failures += 1
            print(f"  ❌ {name}\n       {e}")
        except Exception as e:
            failures += 1
            print(f"  💥 {name}\n       {type(e).__name__}: {e}")

    print(f"\n{len(CHECKS) - failures}/{len(CHECKS)} tests passés.")
    sys.exit(1 if failures else 0)
