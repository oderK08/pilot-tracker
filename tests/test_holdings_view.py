"""
Tests de la vue positions/variations -- aucun appel réseau, données
fabriquées à la main.

Le cas central testé ici est celui qui a motivé la vue : une position dont
le POIDS grimpe sans qu'une seule action ait été achetée. Tant que ce test
passe, l'interface ne peut pas présenter une hausse de cours comme un
renforcement de conviction.

Lancement :
    python tests/test_holdings_view.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analysis import holdings_view as hv  # noqa: E402

Q3, Q2, Q1 = pd.Timestamp("2025-09-30"), pd.Timestamp("2025-06-30"), pd.Timestamp("2025-03-31")


def _pos(report_date, issuer, cusip, value, shares, put_call=None, title="COM", prn="SH"):
    return {
        "report_date": report_date,
        "filing_date": report_date + pd.Timedelta(days=45),
        "name_of_issuer": issuer,
        "cusip": cusip,
        "title_of_class": title,
        "put_call": put_call,
        "shrs_prn_type": prn,
        "value_usd": float(value),
        "shares": float(shares),
        "declared_value_total": None,
        "confidential_omitted": False,
        "manager_name": "TEST CAPITAL",
    }


def _history():
    """
    Portefeuille de test sur 3 trimestres :
      - MICRON : nombre de titres STRICTEMENT identique, cours qui double.
                 Son poids monte donc sans aucune décision de gestion.
      - LULU   : titres réduits de moitié (vraie décision d'allègement)
      - PLTR   : put ouvert au dernier trimestre (nouveau, baissier)
      - BABA   : détenu jusqu'au trimestre précédent, liquidé depuis
    """
    return pd.DataFrame([
        _pos(Q1, "MICRON TECHNOLOGY INC", "595112103", 45_000_000, 500_000),
        _pos(Q1, "LULULEMON ATHLETICA INC", "550021109", 40_000_000, 100_000),
        _pos(Q1, "ALIBABA GROUP HLDG LTD", "01609W102", 30_000_000, 300_000),

        _pos(Q2, "MICRON TECHNOLOGY INC", "595112103", 50_000_000, 500_000),
        _pos(Q2, "LULULEMON ATHLETICA INC", "550021109", 40_000_000, 100_000),
        _pos(Q2, "ALIBABA GROUP HLDG LTD", "01609W102", 30_000_000, 300_000),

        # Micron : mêmes 500 000 titres, valeur qui double -> effet PRIX pur.
        _pos(Q3, "MICRON TECHNOLOGY INC", "595112103", 100_000_000, 500_000),
        _pos(Q3, "LULULEMON ATHLETICA INC", "550021109", 20_000_000, 50_000),
        _pos(Q3, "PALANTIR TECHNOLOGIES INC", "69608A108", 60_000_000, 400_000, put_call="PUT"),
    ])


CHECKS = []


def check(name):
    def decorator(fn):
        CHECKS.append((name, fn))
        return fn
    return decorator


@check("Une hausse de poids sans achat est identifiée comme un effet prix, pas un renforcement")
def test_micron_price_effect():
    view = hv.build_quarterly_view(_history(), n_quarters=6)
    micron = view["positions"].set_index("label").loc["Micron Technology Inc"]

    assert micron["delta_shares_pct"] == 0, "aucun titre n'a été acheté, Δ titres doit être nul"
    assert micron["delta_weight_pts"] > 5, "le poids doit pourtant avoir nettement monté"
    assert micron["price_change_pct"] == 100, f"le cours a doublé, obtenu {micron['price_change_pct']}"
    assert micron["status"] == hv.STATUS_FLAT, \
        f"statut attendu '{hv.STATUS_FLAT}' (aucune décision), obtenu '{micron['status']}'"


@check("Un vrai allègement est distingué d'un effet prix")
def test_real_reduction():
    view = hv.build_quarterly_view(_history(), n_quarters=6)
    lulu = view["positions"].set_index("label").loc["Lululemon Athletica Inc"]

    assert lulu["delta_shares_pct"] == -50, f"obtenu {lulu['delta_shares_pct']}"
    assert lulu["status"] == hv.STATUS_REDUCED
    assert lulu["price_change_pct"] == 0, "le cours n'a pas bougé, seule la quantité a changé"


@check("Les positions sont triées par poids décroissant")
def test_sorted_by_weight():
    view = hv.build_quarterly_view(_history(), n_quarters=6)
    weights = view["positions"]["weight_pct"].tolist()
    assert weights == sorted(weights, reverse=True), f"tri incorrect: {weights}"
    assert view["positions"]["rank"].tolist() == [1, 2, 3]


@check("Un put est typé comme tel et n'est pas confondu avec une position longue")
def test_put_typed():
    view = hv.build_quarterly_view(_history(), n_quarters=6)
    pltr = view["positions"].set_index("label").loc["Palantir Technologies Inc"]

    assert pltr["security_type"] == hv.TYPE_PUT
    assert pltr["status"] == hv.STATUS_NEW


@check("Une position liquidée apparaît dans le bloc des sorties, pas dans le tableau principal")
def test_exits():
    view = hv.build_quarterly_view(_history(), n_quarters=6)

    assert "Alibaba Group Hldg Ltd" not in view["positions"]["label"].tolist(), \
        "une position liquidée ne doit plus figurer parmi les positions détenues"
    exits = view["exits"].set_index("label")
    assert "Alibaba Group Hldg Ltd" in exits.index, "la sortie doit être remontée explicitement"
    assert exits.loc["Alibaba Group Hldg Ltd", "delta_shares_pct"] == -100
    assert exits.loc["Alibaba Group Hldg Ltd", "previous_weight_pct"] > 0


@check("Les poids d'un trimestre totalisent 100%")
def test_weights_sum_to_100():
    view = hv.build_quarterly_view(_history(), n_quarters=6)
    total = view["positions"]["weight_pct"].sum()
    assert abs(total - 100) < 1e-6, f"somme des poids = {total}"


@check("La synthèse compte correctement mouvements, concentration et options")
def test_summary():
    view = hv.build_quarterly_view(_history(), n_quarters=6)
    s = view["summary"]

    assert s["n_positions"] == 3
    assert s["n_new"] == 1 and s["n_exited"] == 1 and s["n_reduced"] == 1
    assert s["n_options"] == 1
    assert abs(s["top5_pct"] - 100) < 1e-6, "avec 3 positions, le top 5 vaut tout le portefeuille"
    assert s["lag_days"] == 45
    assert s["total_value"] == 180_000_000


@check("La fenêtre d'affichage limite bien le nombre de trimestres")
def test_quarter_window():
    view = hv.build_quarterly_view(_history(), n_quarters=2)
    assert len(view["quarters"]) == 2
    assert view["quarters"][0] == Q3 and view["quarters"][1] == Q2
    assert view["summary"]["n_quarters_available"] == 3


@check("Un CUSIP non résolu en ticker reste affiché sous son nom d'émetteur")
def test_unresolved_cusip_kept():
    view = hv.build_quarterly_view(_history(), n_quarters=6, ticker_map={"595112103": "MU"})
    labels = view["positions"]["label"].tolist()

    assert "MU" in labels, "un CUSIP résolu doit s'afficher sous son ticker"
    assert "Palantir Technologies Inc" in labels, \
        "une position non résolue doit rester affichée, sinon les poids sont faussés"
    assert abs(view["positions"]["weight_pct"].sum() - 100) < 1e-6


@check("Un unique trimestre disponible ne fait pas planter la vue")
def test_single_quarter():
    history = _history()
    view = hv.build_quarterly_view(history[history["report_date"] == Q3], n_quarters=6)

    assert len(view["positions"]) == 3
    assert view["exits"].empty
    assert view["positions"]["status"].tolist() == [hv.STATUS_NEW] * 3


@check("Une obligation (montant nominal) n'est pas traitée comme des actions")
def test_bond_not_priced():
    history = pd.concat([_history(), pd.DataFrame([
        _pos(Q3, "SOME CORP NOTE", "111111111", 5_000_000, 5_000_000, title="NOTE", prn="PRN")
    ])], ignore_index=True)
    view = hv.build_quarterly_view(history, n_quarters=6)
    bond = view["positions"].set_index("label").loc["Some Corp Note"]

    assert bond["security_type"] == hv.TYPE_BOND
    assert pd.isna(bond["price_change_pct"]), "un nominal d'obligation n'a pas de prix unitaire"


@check("La mise en forme produit une colonne de poids par trimestre")
def test_display_frame():
    view = hv.build_quarterly_view(_history(), n_quarters=6)
    display = hv.to_display_frame(view["positions"], view["quarters"])

    assert "Position" in display.columns and "Δ titres %" in display.columns
    assert "T3 25" in display.columns and "T1 25" in display.columns
    assert len(display) == 3


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
