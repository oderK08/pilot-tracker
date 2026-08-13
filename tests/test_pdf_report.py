"""
Tests du rapport PDF -- aucun appel réseau, PDF écrit dans un dossier
temporaire puis relu.

Ces tests visent surtout les deux façons dont un PDF casse SILENCIEUSEMENT,
sans lever d'exception : des caractères que la police standard ne sait pas
rendre (le delta grec en est un, d'où « Var. » dans les en-têtes), et des
nombres formatés à l'anglaise dans un document français.

Lancement :
    python tests/test_pdf_report.py
"""
import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analysis import holdings_view as hv  # noqa: E402
from analysis import pdf_report  # noqa: E402

Q3, Q2 = pd.Timestamp("2025-09-30"), pd.Timestamp("2025-06-30")

# Jeu de caractères réellement disponible dans la police standard d'un PDF
# (WinAnsi). Tout caractère hors de cette table sort en carré vide chez le
# lecteur, sans la moindre erreur à la génération.
WINANSI_SAFE = set(
    "0123456789 .,;:!?'\"()[]/%$&+-—–…°#*@"
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "àâäçéèêëîïôöùûüÿœæÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŒÆ\n\t"
)


def _pos(report_date, issuer, cusip, value, shares, put_call=None):
    return {
        "report_date": report_date, "filing_date": report_date + pd.Timedelta(days=45),
        "name_of_issuer": issuer, "cusip": cusip, "title_of_class": "COM",
        "put_call": put_call, "shrs_prn_type": "SH",
        "value_usd": float(value), "shares": float(shares),
        "declared_value_total": None, "confidential_omitted": False, "manager_name": "TEST",
    }


def _view():
    history = pd.DataFrame([
        _pos(Q2, "MICRON TECHNOLOGY INC", "595112103", 50_000_000, 500_000),
        _pos(Q2, "ALIBABA GROUP HLDG LTD", "01609W102", 30_000_000, 300_000),
        _pos(Q3, "MICRON TECHNOLOGY INC", "595112103", 100_000_000, 500_000),
        _pos(Q3, "PALANTIR TECHNOLOGIES INC", "69608A108", 60_000_000, 400_000, put_call="PUT"),
    ])
    # Avec la correspondance CUSIP -> ticker, comme en usage réel : c'est le
    # ticker qui doit apparaître dans le PDF, pas la raison sociale.
    tickers = {"595112103": "MU", "01609W102": "BABA", "69608A108": "PLTR"}
    return hv.build_quarterly_view(history, n_quarters=6, ticker_map=tickers)


CHECKS = []


def check(name):
    def decorator(fn):
        CHECKS.append((name, fn))
        return fn
    return decorator


def _build_and_read():
    from pypdf import PdfReader
    path = os.path.join(tempfile.mkdtemp(), "rapport.pdf")
    pdf_report.build_pdf("Test Capital", _view(), path)
    reader = PdfReader(path)
    return reader, "\n".join(page.extract_text() for page in reader.pages)


@check("Le PDF contient le tableau des positions détenues")
def test_positions_table():
    _, text = _build_and_read()
    for expected in ["Test Capital", "Positions détenues", "MU", "PLTR", "PUT", "500 000"]:
        assert expected in text, f"'{expected}' absent du PDF"


@check("Le PDF contient le tableau des positions liquidées, avec leur taille")
def test_exits_table():
    _, text = _build_and_read()
    assert "Positions liquidées" in text
    assert "BABA" in text, "la position liquidée doit apparaître"
    assert "300 000" in text, "le nombre de titres soldés doit apparaître"


@check("Aucun caractère que la police standard ne sait pas rendre")
def test_no_unrenderable_characters():
    # Le delta grec est le piège principal : il ne lèverait aucune erreur à
    # la génération, mais s'afficherait en carré vide.
    _, text = _build_and_read()
    unsupported = {c for c in text if c not in WINANSI_SAFE}
    assert not unsupported, f"caractères non rendus par la police standard: {sorted(unsupported)}"
    assert "Var. titres" in text, "les en-têtes doivent dire 'Var.' plutôt qu'un delta grec"


@check("Les nombres sont écrits à la française")
def test_french_number_format():
    assert pdf_report._num(1234567.8, 1) == "1 234 567,8"
    assert pdf_report._num(12.5, 1, " %", signed=True) == "+12,5 %"
    assert pdf_report._money(1_380_000_000) == "1,38 Md$"
    assert pdf_report._money(912_100_000) == "912,1 M$"
    assert pdf_report._money(54_963) == "54 963 $"


@check("Une valeur absente donne un tiret, jamais un zéro trompeur")
def test_missing_values_are_dashes():
    assert pdf_report._num(None) == "—"
    assert pdf_report._num(float("nan"), 1) == "—"
    assert pdf_report._money(None) == "—"

    _, text = _build_and_read()
    assert "—" in text, "les positions nouvelles n'ont pas de variation, ce doit être visible"


@check("Le PDF porte un titre de document exploitable")
def test_document_metadata():
    reader, _ = _build_and_read()
    assert "Test Capital" in (reader.metadata.title or "")
    assert len(reader.pages) >= 1


@check("Les avertissements sur les options figurent dans le PDF")
def test_options_warning():
    _, text = _build_and_read()
    assert "notionnel" in text, "le PDF doit rappeler qu'un put est déclaré en notionnel"


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
