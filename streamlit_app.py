"""
Pilot Tracker -- application interactive.

Deux écrans, parce que les deux sources ne disent pas la même chose :

  - 🏛️ Politicien (STOCK Act) : des TRANSACTIONS datées -> reconstitution
    de portefeuille et courbe de performance vs S&P 500.
  - 🏦 Hedge fund (13F) : des POSITIONS trimestrielles -> tableau des
    positions et de leurs variations d'un trimestre à l'autre, SANS
    performance (voir analysis/holdings_view.py pour le pourquoi).

Lancement local :
    streamlit run streamlit_app.py

Déploiement (gratuit) : Streamlit Community Cloud, connecté directement à
ce dépôt GitHub -- se redéploie automatiquement à chaque push.
"""
import streamlit as st
import plotly.graph_objects as go
import pandas as pd

import core
from analysis import holdings_view
from tracked_pilots import TRACKED_CONGRESS, TRACKED_HEDGE_FUNDS

st.set_page_config(page_title="Pilot Tracker", page_icon="📊", layout="wide")


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_congress_data(name: str):
    """Mis en cache 6h -- la lecture depuis l'archive locale est déjà quasi instantanée."""
    sim, benchmark_df = core.run_simulation("congress", name)
    value_df = core.get_value_over_time("congress", name, sim)
    performance_df = core.get_performance_index_over_time("congress", name, sim)
    return value_df, performance_df, benchmark_df, sim.get_current_positions(), sim.get_transaction_log()


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_hedge_fund_view(name: str, n_quarters: int):
    return core.get_hedge_fund_view(name, n_quarters=n_quarters)


st.title("📊 Pilot Tracker")
st.caption(
    "Ce que détiennent réellement les hedge funds et les politiciens américains, "
    "à partir de leurs seules déclarations publiques."
)

with st.sidebar:
    st.header("Choisir un pilote")
    if st.button("🔄 Vider le cache (forcer un recalcul complet)", width='stretch'):
        st.cache_data.clear()
        st.success("Cache vidé -- le prochain calcul repartira de zéro.")

    pilot_type = st.radio(
        "Type de pilote",
        ["congress", "hedge_fund"],
        format_func=lambda x: "🏛️ Politicien (Congrès)" if x == "congress" else "🏦 Hedge Fund (13F)",
    )

    # Menu déroulant FERMÉ -- pas de champ de texte libre. La plateforme
    # affiche uniquement les pilotes qu'on a choisi de suivre (voir
    # tracked_pilots.py), pas n'importe quel nom tapé au clavier.
    options = TRACKED_CONGRESS if pilot_type == "congress" else TRACKED_HEDGE_FUNDS
    name = st.selectbox("Choisir dans la liste suivie", options)

    n_quarters = holdings_view.DEFAULT_QUARTERS
    if pilot_type == "hedge_fund":
        n_quarters = st.select_slider(
            "Trimestres affichés",
            options=holdings_view.QUARTER_CHOICES,
            value=holdings_view.DEFAULT_QUARTERS,
        )

    if st.button("Analyser", type="primary", width='stretch'):
        # Mémorisé dans session_state -- un st.button() redevient False dès
        # le PROCHAIN rerun (ex: quand on bouge le curseur de trimestres),
        # donc s'appuyer directement sur cette valeur faisait disparaître
        # tout le résultat au moindre autre clic. session_state, lui,
        # persiste entre les reruns.
        st.session_state.show_results = True
        st.session_state.selected_pilot_type = pilot_type
        st.session_state.selected_name = name

    st.session_state.n_quarters = n_quarters

    st.divider()
    if pilot_type == "congress":
        st.caption(
            "⚠️ Le STOCK Act n'oblige à déclarer qu'une **fourchette** de montant, "
            "jamais un montant exact. Les montants affichés sont estimés au milieu "
            "de la fourchette déclarée. Historique limité à ~1,5 an."
        )
    else:
        st.caption(
            "✅ Le 13F donne le nombre **exact** de titres détenus à chaque trimestre.\n\n"
            "⚠️ Mais il ne dit rien du cash, des positions non-US, des ventes à découvert "
            "ni d'aucun mouvement à l'intérieur du trimestre — et il arrive avec jusqu'à "
            "45 jours de retard. D'où un écran de **positions**, pas de performance."
        )


def render_hedge_fund(name: str, n_quarters: int):
    with st.spinner(f"Lecture des dépôts 13F de {name}..."):
        try:
            view = get_hedge_fund_view(name, n_quarters)
        except Exception as e:
            st.error(f"❌ {e}")
            st.stop()

    positions, exits, quarters, summary = view["positions"], view["exits"], view["quarters"], view["summary"]
    if positions.empty:
        st.warning("Aucune position exploitable trouvée pour ce gérant.")
        st.stop()

    # --- En-tête : de quand date cette photo, et que vaut-elle ---
    report = summary["report_date"].date()
    filed = summary["filing_date"].date() if summary["filing_date"] is not None and pd.notna(summary["filing_date"]) else None
    lag = summary["lag_days"]
    st.subheader(f"{name} — positions au {report}")
    st.caption(
        f"Déposé le {filed} ({lag} jours après la clôture du trimestre)."
        if filed else "Date de dépôt inconnue."
    )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Valeur déclarée", f"{summary['total_value']/1e9:,.1f} Md$")
    col2.metric("Positions", f"{summary['n_positions']}")
    col3.metric("Top 10", f"{summary['top10_pct']:.0f} %", help="Poids cumulé des 10 plus grosses positions.")
    col4.metric(
        "Mouvements",
        f"{summary['n_new']} ↑ / {summary['n_exited']} ↓",
        help=f"{summary['n_new']} entrée(s), {summary['n_exited']} sortie(s), "
             f"{summary['n_increased']} renforcement(s), {summary['n_reduced']} allègement(s).",
    )

    # --- Avertissements sur la fiabilité de CE dépôt précis ---
    if summary.get("coverage_ok") is False:
        st.warning(
            f"⚠️ Le total des positions lues ({summary['total_value']:,.0f} $) s'écarte de "
            f"{abs(1 - summary['coverage_ratio']) * 100:.0f} % du total officiel déclaré sur la page de "
            "couverture du dépôt. Des lignes ont probablement été mal lues : les poids ci-dessous "
            "sont à prendre avec précaution."
        )
    if summary.get("confidential_omitted"):
        st.info(
            "ℹ️ Ce dépôt signale des positions retirées du tableau au titre du **traitement "
            "confidentiel** accordé par la SEC. Le portefeuille publié est donc sciemment incomplet."
        )
    if summary["n_options"]:
        st.warning(
            f"⚠️ **{summary['n_options']} position(s) en options** ({summary['options_weight_pct']:.0f} % du "
            "tableau). Un 13F déclare le **notionnel du sous-jacent**, pas la prime réellement engagée : "
            "un put pesant 60 % du tableau ne veut PAS dire que 60 % du capital est misé dessus — "
            "probablement quelques pourcents. Les poids des options ne sont pas comparables à ceux des actions."
        )

    # --- Le tableau ---
    st.markdown("#### Positions détenues")
    st.caption(
        "**Δ titres %** = ce que le gérant a décidé (acheté ou vendu). "
        "**Δ poids** = ce qui en résulte dans le portefeuille, cours compris. "
        "Les deux se lisent ensemble : un poids qui monte avec un Δ titres nul, "
        "c'est le **cours** qui a monté, pas une conviction renforcée."
    )

    display = holdings_view.to_display_frame(positions, quarters)
    quarter_cols = [holdings_view.quarter_label(q) for q in quarters]

    st.dataframe(
        display,
        width='stretch',
        hide_index=True,
        column_config={
            "#": st.column_config.NumberColumn(width="small"),
            "Poids %": st.column_config.NumberColumn(format="%.1f %%"),
            "Δ poids (pts)": st.column_config.NumberColumn(format="%+.1f"),
            "Titres": st.column_config.NumberColumn(format="localized"),
            "Δ titres %": st.column_config.NumberColumn(format="%+.1f %%"),
            "Δ prix %": st.column_config.NumberColumn(format="%+.1f %%"),
            "Valeur $": st.column_config.NumberColumn(format="compact"),
            **{
                col: st.column_config.NumberColumn(col, format="%.1f", help=f"Poids (%) au trimestre {col}")
                for col in quarter_cols
            },
        },
    )

    # --- Les sorties : invisibles par construction dans le tableau ci-dessus ---
    if not exits.empty:
        st.markdown("#### Positions liquidées ce trimestre")
        st.caption(
            "Absentes du dernier 13F, donc invisibles dans le tableau ci-dessus — "
            "et pourtant souvent l'information la plus parlante."
        )
        exits_display = exits[["label", "security_type", "previous_weight_pct"]].rename(columns={
            "label": "Position", "security_type": "Type", "previous_weight_pct": "Poids au trimestre précédent %",
        })
        st.dataframe(
            exits_display, width='stretch', hide_index=True,
            column_config={"Poids au trimestre précédent %": st.column_config.NumberColumn(format="%.1f %%")},
        )

    # --- Évolution du poids des plus grosses positions ---
    top = positions.head(10)
    if len(quarters) > 1 and not top.empty:
        st.markdown("#### Évolution du poids des 10 premières positions")
        fig = go.Figure()
        ordered_quarters = list(reversed(quarters))
        for _, row in top.iterrows():
            weights = [row.get(f"w_{pd.Timestamp(q).date()}") for q in ordered_quarters]
            fig.add_trace(go.Scatter(
                x=[holdings_view.quarter_label(q) for q in ordered_quarters],
                y=weights, name=row["label"], mode="lines+markers",
                connectgaps=False,  # un trou = position non détenue ce trimestre-là, à ne PAS relier
            ))
        fig.update_layout(
            height=450, hovermode="x unified", yaxis_title="Poids dans le portefeuille (%)",
            yaxis_ticksuffix=" %", margin=dict(l=0, r=0, t=10, b=0),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )
        st.plotly_chart(fig, width='stretch')

    st.download_button(
        "⬇️ Télécharger le tableau (CSV)",
        display.to_csv(index=False).encode("utf-8"),
        file_name=f"{name.lower().replace(' ', '_')}_positions_{report}.csv",
        mime="text/csv",
    )

    available = summary["n_quarters_available"]
    if available > len(quarters):
        st.caption(f"{available} trimestres disponibles dans l'archive ; {len(quarters)} affichés "
                   "(réglable dans la barre latérale).")


def render_congress(name: str):
    with st.spinner(f"Récupération des données réelles pour {name}..."):
        try:
            value_df, performance_df, benchmark_df, positions_df, log_df = get_congress_data(name)
        except Exception as e:
            st.error(f"❌ {e}")
            st.stop()

    if value_df.empty:
        st.warning("Aucune donnée exploitable trouvée pour ce nom.")
        st.stop()

    last_value = value_df["total_value"].iloc[-1]
    col1, col2, col3 = st.columns(3)
    col1.metric("Valeur actuelle du portefeuille", f"${last_value:,.0f}")

    if not benchmark_df.empty:
        last_benchmark = benchmark_df["benchmark_value"].iloc[-1]
        col2.metric("Équivalent S&P 500", f"${last_benchmark:,.0f}")
        diff_pct = (last_value / last_benchmark - 1) * 100 if last_benchmark else 0
        col3.metric("Écart vs S&P 500", f"{diff_pct:+.1f}%", delta=f"{diff_pct:+.1f}%", delta_color="normal")

    st.subheader("Performance réelle dans le temps")
    st.caption(
        "Les deux courbes sont exprimées en **performance (%) depuis le début de la période "
        "affichée** (0% au premier point)."
    )

    range_key = st.segmented_control(
        "Période affichée",
        options=["3M", "6M", "YTD", "MAX"],
        format_func=lambda k: {"3M": "3 mois", "6M": "6 mois", "YTD": "Depuis janvier", "MAX": "Depuis le début"}[k],
        default="MAX",
    )
    range_key = range_key or "MAX"  # segmented_control peut renvoyer None si désélectionné

    performance_df_filtered = core.filter_by_range(performance_df, "date", range_key)
    benchmark_df_filtered = core.filter_by_range(benchmark_df, "date", range_key) if not benchmark_df.empty else benchmark_df

    # Re-rebase à 0% au début de LA FENÊTRE choisie (pas depuis le tout début).
    value_df_norm = core.to_percentage_return(performance_df_filtered, "performance_index")
    benchmark_df_norm = core.to_percentage_return(benchmark_df_filtered, "benchmark_value") if not benchmark_df_filtered.empty else benchmark_df_filtered

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=value_df_norm["date"], y=value_df_norm["performance_index"],
        name=f"Portefeuille de {name}", line=dict(color="#1a3a5c", width=2),
    ))
    if not benchmark_df_norm.empty:
        fig.add_trace(go.Scatter(
            x=benchmark_df_norm["date"], y=benchmark_df_norm["benchmark_value"],
            name="S&P 500", line=dict(color="#888888", width=1.5, dash="dash"),
        ))
    fig.update_layout(
        hovermode="x unified", height=450,
        yaxis_title="Performance (%)", yaxis_ticksuffix="%",
        margin=dict(l=0, r=0, t=10, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig, width='stretch')

    st.subheader("Positions actuelles")
    positions_clean = positions_df.dropna(subset=["current_value"]) if not positions_df.empty else positions_df
    if not positions_clean.empty:
        positions_clean = positions_clean.sort_values("current_value", ascending=True)
        fig2 = go.Figure(go.Bar(
            x=positions_clean["current_value"], y=positions_clean["ticker"],
            orientation="h", marker_color="#2e8b57",
        ))
        fig2.update_layout(
            height=max(300, len(positions_clean) * 32),
            xaxis_title="Valeur actuelle ($)", margin=dict(l=0, r=0, t=10, b=0),
        )
        st.plotly_chart(fig2, width='stretch')
    else:
        st.info("Aucune position actuelle exploitable (tout a été vendu, ou prix indisponible).")

    st.subheader("Historique des mouvements")
    st.dataframe(log_df, width='stretch', hide_index=True)


if st.session_state.get("show_results"):
    selected_type = st.session_state.selected_pilot_type
    selected_name = st.session_state.selected_name

    if selected_type == "hedge_fund":
        render_hedge_fund(selected_name, st.session_state.get("n_quarters", holdings_view.DEFAULT_QUARTERS))
    else:
        render_congress(selected_name)
else:
    st.info("👈 Choisis un type de pilote et un nom dans la barre latérale, puis clique sur \"Analyser\".")
