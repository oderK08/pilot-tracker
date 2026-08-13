"""
Construction de la vue "positions et variations" d'un gérant de fonds :
ses N derniers trimestres 13F côte à côte, triés par poids décroissant,
avec ce qui a bougé d'un trimestre à l'autre.

=== Le piège que cette vue existe pour éviter ===

Le poids d'une position dans un portefeuille bouge pour DEUX raisons
totalement différentes, qu'un simple "Micron est passé de 6,8% à 10,2%"
mélange sans le dire :

  - le gérant a acheté ou vendu           -> une DÉCISION  (effet flux)
  - le cours du titre a monté ou baissé   -> le MARCHÉ     (effet prix)

Une position peut gagner 3 points de poids sans qu'une seule action ait été
achetée. Lire ça comme un renforcement de conviction est un contresens, et
c'est l'erreur la plus courante dans la lecture des 13F. Ce module sépare
donc systématiquement les deux : `delta_shares_pct` mesure la décision,
`delta_weight_pts` mesure le résultat, et `price_change_pct` explique
l'écart entre les deux.

Bonus : le prix unitaire se déduit du 13F lui-même (valeur / nombre de
titres à la date du trimestre), donc cette décomposition ne nécessite
AUCUNE source de prix externe.

=== Ce qui n'est volontairement pas calculé ici ===

Aucune performance, aucun rendement, aucune comparaison à un indice. Un 13F
ne contient ni le cash, ni les positions non-US, ni les ventes à découvert,
ni le moindre mouvement intra-trimestriel, et arrive avec jusqu'à 45 jours
de retard : c'est une source excellente pour lire des POSITIONS et des
CHANGEMENTS, mauvaise pour mesurer une performance.
"""
import pandas as pd

# Identifie une position d'un trimestre à l'autre. Le put/call en fait
# partie : un put Nvidia et une position longue Nvidia partagent le CUSIP
# mais sont deux paris opposés, à suivre séparément dans le temps.
POSITION_KEYS = ["cusip", "title_of_class", "put_call", "shrs_prn_type"]

# En deçà de ce seuil, une variation du nombre de titres est un arrondi ou
# un ajustement technique, pas une décision de gestion.
FLAT_THRESHOLD_PCT = 0.5

DEFAULT_QUARTERS = 6
QUARTER_CHOICES = [4, 6, 8, 12]

# Libellés de la nature du titre, dérivés de `put_call` et `shrs_prn_type`.
TYPE_SHARES = "Actions"
TYPE_PUT = "PUT"
TYPE_CALL = "CALL"
TYPE_BOND = "Obligation"

STATUS_NEW = "Nouveau"
STATUS_INCREASED = "Renforcé"
STATUS_REDUCED = "Allégé"
STATUS_FLAT = "Inchangé"
STATUS_EXITED = "Sortie"


def _security_type(row) -> str:
    """Nature du titre détenu, telle qu'elle doit être lue par l'utilisateur."""
    if row.get("put_call") == "PUT":
        return TYPE_PUT
    if row.get("put_call") == "CALL":
        return TYPE_CALL
    if row.get("shrs_prn_type") == "PRN":
        return TYPE_BOND
    return TYPE_SHARES


def _position_label(row) -> str:
    """
    Étiquette affichée : le ticker s'il a pu être résolu depuis le CUSIP,
    sinon le nom de l'émetteur tel que déclaré. On n'écarte JAMAIS une
    position faute de ticker -- une position non résolue reste une position
    du portefeuille, et la masquer fausserait tous les poids.
    """
    ticker = row.get("ticker")
    if isinstance(ticker, str) and ticker.strip():
        return ticker.strip()
    name = row.get("name_of_issuer") or "?"
    return name.title()


def _prepare(history: pd.DataFrame, ticker_map: dict = None) -> pd.DataFrame:
    """Normalise l'historique brut et y ajoute ticker, nature du titre et prix implicite."""
    df = history.copy()
    df["report_date"] = pd.to_datetime(df["report_date"])
    if "filing_date" in df.columns:
        df["filing_date"] = pd.to_datetime(df["filing_date"])

    for col in POSITION_KEYS:
        if col not in df.columns:
            df[col] = None
    df["put_call"] = df["put_call"].where(df["put_call"].notna(), None)

    df["ticker"] = df["cusip"].map(ticker_map) if ticker_map else None
    df["security_type"] = df.apply(_security_type, axis=1)
    df["label"] = df.apply(_position_label, axis=1)

    # Prix unitaire implicite à la date du trimestre, tiré du 13F lui-même.
    # Sans objet pour une obligation, dont la "quantité" est un montant
    # nominal et non un nombre de titres.
    df["implied_price"] = (df["value_usd"] / df["shares"]).where(
        (df["shares"] > 0) & (df["shrs_prn_type"] != "PRN")
    )
    return df


def _quarter_key(df: pd.DataFrame) -> pd.Series:
    """Clé de position, robuste aux valeurs manquantes."""
    return (
        df["cusip"].fillna("?").astype(str) + "|"
        + df["title_of_class"].fillna("").astype(str) + "|"
        + df["put_call"].fillna("").astype(str) + "|"
        + df["shrs_prn_type"].fillna("SH").astype(str)
    )


def _status(delta_shares_pct, is_new: bool) -> str:
    if is_new:
        return STATUS_NEW
    if delta_shares_pct is None or pd.isna(delta_shares_pct):
        return STATUS_FLAT
    if delta_shares_pct > FLAT_THRESHOLD_PCT:
        return STATUS_INCREASED
    if delta_shares_pct < -FLAT_THRESHOLD_PCT:
        return STATUS_REDUCED
    return STATUS_FLAT


def build_quarterly_view(history: pd.DataFrame, n_quarters: int = DEFAULT_QUARTERS,
                         ticker_map: dict = None) -> dict:
    """
    Construit la vue complète d'un gérant.

    Args:
        history: positions 13F telles que renvoyées par
            hedge_fund_13f.get_13f_holdings_history() (ou l'archive).
        n_quarters: nombre de trimestres affichés, le plus récent en tête.
        ticker_map: correspondance CUSIP -> ticker (optionnelle ; une
            position non résolue reste affichée sous son nom d'émetteur).

    Returns:
        dict avec les clés :
          - "quarters"  : trimestres affichés, du plus récent au plus ancien
          - "positions" : DataFrame des positions du dernier trimestre,
                          triées par poids décroissant
          - "exits"     : DataFrame des positions liquidées au dernier
                          trimestre (absentes du tableau ci-dessus par
                          construction, et pourtant souvent l'information
                          la plus parlante)
          - "summary"   : dict de synthèse du dernier trimestre
    """
    if history is None or history.empty:
        return {"quarters": [], "positions": pd.DataFrame(), "exits": pd.DataFrame(), "summary": {}}

    df = _prepare(history, ticker_map)
    df["key"] = _quarter_key(df)

    all_quarters = sorted(df["report_date"].unique(), reverse=True)
    quarters = all_quarters[:n_quarters]
    if not quarters:
        return {"quarters": [], "positions": pd.DataFrame(), "exits": pd.DataFrame(), "summary": {}}

    latest, previous = quarters[0], (quarters[1] if len(quarters) > 1 else None)
    window = df[df["report_date"].isin(quarters)]

    # Poids de chaque position dans SON trimestre -- la somme des valeurs
    # déclarées ce trimestre-là fait le dénominateur (le 13F ne connaît pas
    # le cash, donc c'est bien "% du portefeuille déclaré", pas "% des
    # actifs sous gestion").
    totals = window.groupby("report_date")["value_usd"].sum()
    window = window.assign(weight_pct=window["value_usd"] / window["report_date"].map(totals) * 100)

    weights = window.pivot_table(index="key", columns="report_date", values="weight_pct", aggfunc="sum")
    shares = window.pivot_table(index="key", columns="report_date", values="shares", aggfunc="sum")
    values = window.pivot_table(index="key", columns="report_date", values="value_usd", aggfunc="sum")
    prices = window.pivot_table(index="key", columns="report_date", values="implied_price", aggfunc="mean")

    identity = (window.sort_values("report_date")
                .groupby("key")
                .agg(label=("label", "last"), name_of_issuer=("name_of_issuer", "last"),
                     ticker=("ticker", "last"), security_type=("security_type", "last"),
                     title_of_class=("title_of_class", "last"), cusip=("cusip", "last")))

    def _rows_for(quarter, prev_quarter, exited: bool):
        keys = weights.index[weights[quarter].notna()] if not exited else []
        if exited:
            # Détenu au trimestre précédent, absent du dernier : liquidé.
            keys = weights.index[weights[prev_quarter].notna() & weights[quarter].isna()]

        rows = []
        for key in keys:
            prev_shares = shares[prev_quarter].get(key) if prev_quarter is not None else None
            prev_weight = weights[prev_quarter].get(key) if prev_quarter is not None else None
            cur_shares = shares[quarter].get(key) if not exited else None
            cur_weight = weights[quarter].get(key) if not exited else None

            has_prev = prev_shares is not None and pd.notna(prev_shares) and prev_shares != 0
            is_new = (not exited) and not has_prev

            if exited:
                delta_shares_pct, delta_weight_pts, price_change = -100.0, -(prev_weight or 0), None
            else:
                delta_shares_pct = ((cur_shares - prev_shares) / prev_shares * 100) if has_prev else None
                delta_weight_pts = cur_weight - prev_weight if (prev_weight is not None and pd.notna(prev_weight)) else None
                prev_price = prices[prev_quarter].get(key) if prev_quarter is not None else None
                cur_price = prices[quarter].get(key)
                price_change = ((cur_price / prev_price - 1) * 100
                                if all(v is not None and pd.notna(v) and v != 0 for v in [prev_price, cur_price])
                                else None)

            row = {
                **identity.loc[key].to_dict(),
                "key": key,
                "weight_pct": cur_weight,
                "value_usd": None if exited else values[quarter].get(key),
                "shares": cur_shares,
                "delta_weight_pts": delta_weight_pts,
                "delta_shares": (cur_shares - prev_shares) if (has_prev and not exited) else None,
                "delta_shares_pct": delta_shares_pct,
                "price_change_pct": price_change,
                "status": STATUS_EXITED if exited else _status(delta_shares_pct, is_new),
                # Taille de la position AU TRIMESTRE PRÉCÉDENT. Indispensable
                # pour une sortie : sans le nombre de titres et la valeur
                # liquidés, "position vendue" reste une information abstraite,
                # et c'est justement l'ampleur qui fait l'intérêt du signal.
                "previous_weight_pct": prev_weight,
                "previous_shares": prev_shares if has_prev else None,
                "previous_value_usd": values[prev_quarter].get(key) if prev_quarter is not None else None,
            }
            for q in quarters:
                row[f"w_{pd.Timestamp(q).date()}"] = weights[q].get(key)
            rows.append(row)

        return pd.DataFrame(rows)

    positions = _rows_for(latest, previous, exited=False)
    if not positions.empty:
        positions = positions.sort_values("weight_pct", ascending=False).reset_index(drop=True)
        positions.insert(0, "rank", range(1, len(positions) + 1))

    exits = _rows_for(latest, previous, exited=True) if previous is not None else pd.DataFrame()
    if not exits.empty:
        exits = exits.sort_values("previous_weight_pct", ascending=False).reset_index(drop=True)

    return {
        "quarters": [pd.Timestamp(q) for q in quarters],
        "positions": positions,
        "exits": exits,
        "summary": _build_summary(df, window, positions, exits, latest, totals),
    }


def _build_summary(df: pd.DataFrame, window: pd.DataFrame, positions: pd.DataFrame,
                   exits: pd.DataFrame, latest, totals: pd.Series) -> dict:
    """Synthèse du dernier trimestre : taille, concentration, mouvements, fiabilité."""
    latest_rows = df[df["report_date"] == latest]
    total_value = float(totals.get(latest, 0))

    weights_sorted = positions["weight_pct"].dropna().sort_values(ascending=False) if not positions.empty else pd.Series(dtype=float)
    options_mask = positions["security_type"].isin([TYPE_PUT, TYPE_CALL]) if not positions.empty else pd.Series(dtype=bool)

    filing_date = latest_rows["filing_date"].max() if "filing_date" in latest_rows.columns else None
    lag_days = (filing_date - pd.Timestamp(latest)).days if pd.notna(filing_date) else None

    declared = latest_rows["declared_value_total"].dropna() if "declared_value_total" in latest_rows.columns else pd.Series(dtype=float)
    declared_total = float(declared.iloc[0]) if not declared.empty else None
    # Écart au total officiel de la page de couverture : un contrôle de
    # parsing indépendant. Au-delà de 5%, des lignes ont probablement été
    # ratées et les poids affichés sont à prendre avec des pincettes.
    coverage_ratio = (total_value / declared_total) if declared_total else None

    confidential = bool(latest_rows["confidential_omitted"].any()) if "confidential_omitted" in latest_rows.columns else False
    manager = latest_rows["manager_name"].dropna()

    return {
        "report_date": pd.Timestamp(latest),
        "filing_date": filing_date,
        "lag_days": lag_days,
        "manager_name": manager.iloc[0] if not manager.empty else None,
        "total_value": total_value,
        "n_positions": len(positions),
        "top5_pct": float(weights_sorted.head(5).sum()) if not weights_sorted.empty else 0.0,
        "top10_pct": float(weights_sorted.head(10).sum()) if not weights_sorted.empty else 0.0,
        "n_new": int((positions["status"] == STATUS_NEW).sum()) if not positions.empty else 0,
        "n_increased": int((positions["status"] == STATUS_INCREASED).sum()) if not positions.empty else 0,
        "n_reduced": int((positions["status"] == STATUS_REDUCED).sum()) if not positions.empty else 0,
        "n_exited": len(exits),
        "n_options": int(options_mask.sum()) if not positions.empty else 0,
        "options_weight_pct": float(positions.loc[options_mask, "weight_pct"].sum()) if not positions.empty else 0.0,
        "declared_value_total": declared_total,
        "coverage_ratio": coverage_ratio,
        "coverage_ok": (0.95 <= coverage_ratio <= 1.05) if coverage_ratio else None,
        "confidential_omitted": confidential,
        "n_quarters_available": int(df["report_date"].nunique()),
    }


def to_display_frame(positions: pd.DataFrame, quarters: list, include_history: bool = True) -> pd.DataFrame:
    """
    Met en forme les positions pour l'affichage : colonnes lisibles, une
    colonne de poids par trimestre, dans l'ordre du plus récent au plus
    ancien. Les valeurs restent numériques (le formatage final est laissé
    à l'interface, qui sait mieux aligner et colorer).
    """
    if positions.empty:
        return pd.DataFrame()

    columns = {
        "rank": "#",
        "label": "Position",
        "security_type": "Type",
        "weight_pct": "Poids %",
        "delta_weight_pts": "Δ poids (pts)",
        "shares": "Titres",
        "delta_shares_pct": "Δ titres %",
        "price_change_pct": "Δ prix %",
        "status": "Statut",
        "value_usd": "Valeur $",
    }
    available = {k: v for k, v in columns.items() if k in positions.columns}
    display = positions[list(available)].rename(columns=available)

    if include_history:
        for quarter in quarters:
            col = f"w_{pd.Timestamp(quarter).date()}"
            if col in positions.columns:
                display[quarter_label(quarter)] = positions[col]

    return display


def to_exits_frame(exits: pd.DataFrame, previous_quarter=None) -> pd.DataFrame:
    """
    Met en forme les positions liquidées : ce qu'elles pesaient, combien de
    titres et quelle valeur ont été soldés.

    Une sortie n'apparaît nulle part dans le tableau des positions détenues
    -- par définition, elle n'est plus détenue. C'est pourtant souvent le
    mouvement le plus informatif du trimestre : un gérant qui liquide sa
    première ligne dit quelque chose de bien plus net que celui qui rogne
    trois pourcents sur la cinquième.
    """
    if exits.empty:
        return pd.DataFrame()

    columns = {
        "label": "Position",
        "security_type": "Type",
        "previous_weight_pct": "Poids avant %",
        "previous_shares": "Titres soldés",
        "previous_value_usd": "Valeur soldée $",
    }
    available = {k: v for k, v in columns.items() if k in exits.columns}
    display = exits[list(available)].rename(columns=available)

    if previous_quarter is not None and "Poids avant %" in display.columns:
        display = display.rename(columns={"Poids avant %": f"Poids en {quarter_label(previous_quarter)} %"})
    return display


def quarter_label(quarter) -> str:
    """Libellé court d'un trimestre ('T3 25') pour les en-têtes de colonnes."""
    ts = pd.Timestamp(quarter)
    return f"T{ts.quarter} {ts.strftime('%y')}"
