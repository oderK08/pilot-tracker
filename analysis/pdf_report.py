"""
Rapport PDF d'un gérant de fonds : le tableau complet de ses positions et
de leurs variations trimestrielles, plus celui de ses positions liquidées.

Volontairement TABULAIRE, sans aucun graphique. Un portefeuille 13F se lit
ligne par ligne -- combien de titres, quelle décision, quel effet prix --
et une courbe de poids ne dit rien de tout ça. Le PDF est là pour être lu,
imprimé ou envoyé tel quel.

⚠️ Contrainte d'encodage : les polices standard d'un PDF (Helvetica & co.)
utilisent WinAnsi, qui ne contient PAS le delta grec « Δ ». Les en-têtes
disent donc « Var. » et non « Δ », sans quoi ces colonnes s'afficheraient
en caractères manquants chez le lecteur. Les accents français, eux, sont
bien dans WinAnsi.
"""
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from analysis import holdings_view

# Palette sobre : l'information est dans les chiffres, la couleur ne sert
# qu'à rendre la colonne "Statut" lisible d'un coup d'œil.
INK = colors.HexColor("#1a2733")
MUTED = colors.HexColor("#6b7280")
RULE = colors.HexColor("#d6dce2")
HEADER_BG = colors.HexColor("#1a3a5c")
BAND_BG = colors.HexColor("#f4f6f8")
WARN_BG = colors.HexColor("#fdf6e3")

STATUS_COLORS = {
    holdings_view.STATUS_NEW: colors.HexColor("#1f6feb"),
    holdings_view.STATUS_INCREASED: colors.HexColor("#1a7f45"),
    holdings_view.STATUS_REDUCED: colors.HexColor("#b4530a"),
    holdings_view.STATUS_EXITED: colors.HexColor("#b91c1c"),
    holdings_view.STATUS_FLAT: MUTED,
}


def _fr(formatted: str) -> str:
    """
    Convertit un nombre formaté à l'anglaise (« 1,234.5 ») en notation
    française (« 1 234,5 »).

    Le passage par un caractère tampon est nécessaire : remplacer
    directement la virgule par une espace puis le point par une virgule
    réintroduirait des virgules là où on venait d'en retirer.
    """
    return formatted.replace(",", "\x00").replace(".", ",").replace("\x00", " ")


def _num(value, decimals=0, suffix="", signed=False):
    """
    Nombre formaté à la française, ou un tiret cadratin si la valeur n'a
    pas de sens.

    Un tiret n'est PAS un zéro : il signale qu'aucune comparaison n'est
    possible (position nouvelle, donc sans trimestre de référence).
    """
    if value is None or pd.isna(value):
        return "—"
    formatted = f"{value:+,.{decimals}f}" if signed else f"{value:,.{decimals}f}"
    return _fr(formatted) + suffix


def _money(value):
    """Valeur en dollars, à l'échelle la plus lisible (Md$ / M$ / $)."""
    if value is None or pd.isna(value):
        return "—"
    if abs(value) >= 1e9:
        return _fr(f"{value / 1e9:,.2f}") + " Md$"
    if abs(value) >= 1e6:
        return _fr(f"{value / 1e6:,.1f}") + " M$"
    return _fr(f"{value:,.0f}") + " $"


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontSize=17, leading=21,
                                alignment=TA_LEFT, textColor=INK, spaceAfter=2),
        "subtitle": ParagraphStyle("st", parent=base["Normal"], fontSize=9.5, leading=13,
                                   textColor=MUTED, spaceAfter=10),
        # keepWithNext : un titre de section, et l'explication qui le suit,
        # ne doivent jamais rester orphelins en bas de page, séparés du
        # tableau qu'ils annoncent -- reportlab reporte alors le bloc entier
        # sur la page suivante.
        "section": ParagraphStyle("s", parent=base["Heading2"], fontSize=12, leading=15,
                                  textColor=INK, spaceBefore=14, spaceAfter=5, keepWithNext=1),
        "body": ParagraphStyle("b", parent=base["Normal"], fontSize=8.5, leading=11.5, textColor=INK),
        "note": ParagraphStyle("n", parent=base["Normal"], fontSize=8, leading=11, textColor=MUTED,
                               keepWithNext=1),
        "warn": ParagraphStyle("w", parent=base["Normal"], fontSize=8.5, leading=12,
                               textColor=colors.HexColor("#8a5a00"), backColor=WARN_BG,
                               borderPadding=6, spaceBefore=6, spaceAfter=6),
    }


def _table_style(numeric_from: int = 2) -> TableStyle:
    """Style commun : en-tête foncé, lignes alternées, chiffres alignés à droite."""
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 7),
        ("FONTSIZE", (0, 1), (-1, -1), 7.2),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("ALIGN", (numeric_from, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, RULE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BAND_BG]),
    ]
    return TableStyle(commands)


def _positions_table(positions: pd.DataFrame, quarters: list) -> Table:
    """Tableau principal : une ligne par position détenue, poids décroissant."""
    quarter_labels = [holdings_view.quarter_label(q) for q in quarters]
    header = ["#", "Position", "Type", "Poids %", "Var. poids", "Titres",
              "Var. titres", "Var. prix", "Statut", "Valeur"] + quarter_labels

    rows = [header]
    status_colors = []
    for i, (_, row) in enumerate(positions.iterrows(), start=1):
        rows.append([
            str(int(row["rank"])),
            str(row["label"]),
            str(row["security_type"]),
            _num(row["weight_pct"], 1),
            _num(row["delta_weight_pts"], 1, signed=True),
            _num(row["shares"], 0),
            _num(row["delta_shares_pct"], 1, " %", signed=True),
            _num(row["price_change_pct"], 1, " %", signed=True),
            str(row["status"]),
            _money(row["value_usd"]),
        ] + [_num(row.get(f"w_{pd.Timestamp(q).date()}"), 1) for q in quarters])
        status_colors.append((i, STATUS_COLORS.get(row["status"], INK)))

    widths = [0.7 * cm, 1.75 * cm, 1.3 * cm, 1.35 * cm, 1.5 * cm, 2.15 * cm,
              1.5 * cm, 1.4 * cm, 1.7 * cm, 2.0 * cm]
    remaining = 27.6 * cm - sum(widths)
    widths += [max(1.15 * cm, remaining / max(len(quarters), 1))] * len(quarters)

    table = Table(rows, colWidths=widths, repeatRows=1)
    style = _table_style()
    style.add("ALIGN", (0, 0), (0, -1), "RIGHT")
    style.add("FONTNAME", (1, 1), (1, -1), "Helvetica-Bold")
    for row_index, color in status_colors:
        style.add("TEXTCOLOR", (8, row_index), (8, row_index), color)
    table.setStyle(style)
    return table


def _exits_table(exits: pd.DataFrame, previous_quarter) -> Table:
    """Tableau des positions liquidées, avec la taille réellement soldée."""
    label = holdings_view.quarter_label(previous_quarter) if previous_quarter is not None else "trim. préc."
    header = ["Position", "Type", f"Poids en {label}", "Titres soldés", "Valeur soldée"]

    rows = [header]
    for _, row in exits.iterrows():
        rows.append([
            str(row["label"]),
            str(row["security_type"]),
            _num(row["previous_weight_pct"], 1, " %"),
            _num(row.get("previous_shares"), 0),
            _money(row.get("previous_value_usd")),
        ])

    table = Table(rows, colWidths=[3.2 * cm, 2.2 * cm, 3.4 * cm, 3.6 * cm, 3.4 * cm], repeatRows=1)
    style = _table_style()
    style.add("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold")
    style.add("TEXTCOLOR", (0, 1), (0, -1), STATUS_COLORS[holdings_view.STATUS_EXITED])
    table.setStyle(style)
    return table


def _summary_table(summary: dict) -> Table:
    """Bandeau de synthèse : la taille, la concentration et les mouvements du trimestre."""
    cells = [
        ["Valeur déclarée", "Positions", "Top 10", "Entrées", "Sorties", "Renforcées", "Allégées"],
        [
            _money(summary["total_value"]),
            str(summary["n_positions"]),
            _num(summary["top10_pct"], 0, " %"),
            str(summary["n_new"]),
            str(summary["n_exited"]),
            str(summary["n_increased"]),
            str(summary["n_reduced"]),
        ],
    ]
    table = Table(cells, colWidths=[4.2 * cm] + [3.0 * cm] * 6)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, 0), 7.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 1), (-1, 1), 12),
        ("TEXTCOLOR", (0, 1), (-1, 1), INK),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
        ("TOPPADDING", (0, 1), (-1, 1), 0),
        ("LINEBELOW", (0, 1), (-1, 1), 0.5, RULE),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
    ]))
    return table


def _warnings(summary: dict, styles: dict) -> list:
    """
    Avertissements portant sur CE dépôt précis -- ce qui empêcherait de
    lire le tableau de travers.
    """
    notes = []
    if summary.get("n_options"):
        notes.append(Paragraph(
            f"<b>{summary['n_options']} position(s) en options</b>, soit {summary['options_weight_pct']:.0f} % du "
            "tableau. Un 13F déclare le <b>notionnel du sous-jacent</b>, pas la prime engagée : une ligne de puts "
            "pesant 15 % du tableau ne signifie pas que 15 % du capital est misé dessus, probablement quelques "
            "pourcents. Les poids des options ne sont pas comparables à ceux des actions.", styles["warn"]))
    if summary.get("confidential_omitted"):
        notes.append(Paragraph(
            "Ce dépôt signale des positions retirées du tableau au titre du <b>traitement confidentiel</b> accordé "
            "par la SEC : le portefeuille publié est sciemment incomplet.", styles["warn"]))
    if summary.get("coverage_ok") is False:
        notes.append(Paragraph(
            f"Le total des positions lues s'écarte de {abs(1 - summary['coverage_ratio']) * 100:.0f} % du total "
            "officiel figurant sur la page de couverture du dépôt : des lignes ont probablement été mal lues, "
            "les poids sont à prendre avec précaution.", styles["warn"]))
    return notes


def _footer(canvas, doc, name: str):
    """Pied de page : source et pagination, sur chaque page."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(1.05 * cm, 0.75 * cm,
                      f"{name} — positions déclarées au formulaire 13F-HR, source SEC EDGAR")
    canvas.drawRightString(landscape(A4)[0] - 1.05 * cm, 0.75 * cm, f"page {canvas.getPageNumber()}")
    canvas.restoreState()


def build_pdf(name: str, view: dict, output_path: str) -> str:
    """
    Écrit le rapport PDF d'un gérant et retourne le chemin du fichier.

    Args:
        name: nom du gérant, tel qu'affiché en titre.
        view: sortie de holdings_view.build_quarterly_view().
        output_path: chemin du PDF à écrire.
    """
    positions, exits, quarters, summary = view["positions"], view["exits"], view["quarters"], view["summary"]
    if positions.empty:
        raise RuntimeError(f"[pdf_report] Aucune position à publier pour '{name}'.")

    styles = _styles()
    report_date = summary["report_date"].date()
    filing_date = summary.get("filing_date")
    previous_quarter = quarters[1] if len(quarters) > 1 else None

    document = SimpleDocTemplate(
        output_path, pagesize=landscape(A4),
        leftMargin=1.05 * cm, rightMargin=1.05 * cm, topMargin=1.1 * cm, bottomMargin=1.3 * cm,
        title=f"{name} — positions 13F au {report_date}", author="Pilot Tracker",
    )

    filed = (f"Déposé le {pd.Timestamp(filing_date).date()}, soit {summary['lag_days']} jours après la clôture "
             "du trimestre." if filing_date is not None and pd.notna(filing_date) else "Date de dépôt inconnue.")

    story = [
        Paragraph(f"{name} — positions au {report_date}", styles["title"]),
        Paragraph(f"{filed} Un 13F est une photo de fin de trimestre : il ne contient ni liquidités, ni positions "
                  "non américaines, ni ventes à découvert, et aucun mouvement intermédiaire.", styles["subtitle"]),
        _summary_table(summary),
        Spacer(1, 4),
    ]
    story += _warnings(summary, styles)

    story += [
        Paragraph("Positions détenues", styles["section"]),
        Paragraph("<b>Var. titres</b> mesure la décision du gérant (ce qu'il a acheté ou vendu). "
                  "<b>Var. poids</b> mesure le résultat dans le portefeuille, effet de cours compris. "
                  "Les deux se lisent ensemble : un poids qui monte avec une variation de titres nulle, "
                  "c'est le cours qui a monté — pas une conviction renforcée. Les colonnes de droite donnent "
                  "le poids (%) à chaque trimestre, du plus récent au plus ancien.", styles["note"]),
        Spacer(1, 6),
        _positions_table(positions, quarters),
    ]

    if not exits.empty:
        story += [
            Paragraph("Positions liquidées ce trimestre", styles["section"]),
            Paragraph("Détenues au trimestre précédent, absentes de ce dépôt : elles ne peuvent donc pas figurer "
                      "dans le tableau ci-dessus. C'est pourtant souvent le mouvement le plus parlant — liquider "
                      "entièrement une ligne dit plus qu'en rogner quelques pourcents.", styles["note"]),
            Spacer(1, 6),
            _exits_table(exits, previous_quarter),
        ]

    document.build(story, onFirstPage=lambda c, d: _footer(c, d, name),
                   onLaterPages=lambda c, d: _footer(c, d, name))
    return output_path
