"""
Pilot Tracker -- application interactive.

Recherche un politicien ou un gérant de fonds et affiche la reconstitution
RÉELLE de son portefeuille (graphique de performance interactif, positions
actuelles, historique des mouvements) -- à partir des mêmes modules de
données/simulation que le script CLI (core.py), juste avec un rendu
interactif (Plotly) au lieu de PNG statiques.

Lancement local :
    streamlit run streamlit_app.py

Déploiement (gratuit) : Streamlit Community Cloud, connecté directement à
ce dépôt GitHub -- se redéploie automatiquement à chaque push.
"""
import streamlit as st
import plotly.graph_objects as go
import pandas as pd

import core
from tracked_pilots import TRACKED_CONGRESS, TRACKED_HEDGE_FUNDS

st.set_page_config(page_title="Pilot Tracker", page_icon="📊", layout="wide")


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_pilot_data(pilot_type: str, name: str):
    """
    Mis en cache 6h -- pour les pilotes de la liste suivie, la lecture est
    déjà quasi instantanée grâce à l'archive locale (voir core.run_simulation),
    ce cache Streamlit évite juste de refaire le travail de simulation à
    chaque interaction sur la même recherche pendant une session.
    """
    sim, benchmark_df = core.run_simulation(pilot_type, name)
    value_df = core.get_value_over_time(pilot_type, name, sim)
    positions_df = sim.get_current_positions()
    log_df = sim.get_transaction_log()
    return value_df, benchmark_df, positions_df, log_df


st.title("📊 Pilot Tracker")
st.caption(
    "Reconstitue la valeur RÉELLE du portefeuille d'un politicien ou d'un hedge fund, "
    "à partir de leurs déclarations publiques -- pas une simulation à échelle réduite."
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

    if st.button("Analyser", type="primary", width='stretch'):
        # Mémorisé dans session_state -- un st.button() redevient False dès
        # le PROCHAIN rerun (ex: quand on clique sur le sélecteur de période
        # 3M/6M/YTD plus bas), donc s'appuyer directement sur cette valeur
        # faisait disparaître tout le résultat au moindre autre clic sur la
        # page. session_state, lui, persiste entre les reruns.
        st.session_state.show_results = True
        st.session_state.selected_pilot_type = pilot_type
        st.session_state.selected_name = name

    st.divider()
    if pilot_type == "congress":
        st.caption(
            "⚠️ Le STOCK Act n'oblige à déclarer qu'une **fourchette** de montant, "
            "jamais un montant exact. Les montants affichés sont estimés au milieu "
            "de la fourchette déclarée. Historique limité à ~1,5 an."
        )
    else:
        st.caption(
            "✅ Le 13F donne le nombre **exact** d'actions détenues à chaque "
            "trimestre déclaré, sur les 5 dernières années."
        )

if st.session_state.get("show_results"):
    pilot_type = st.session_state.selected_pilot_type
    name = st.session_state.selected_name

    with st.spinner(f"Récupération des données réelles pour {name}... (peut prendre une minute)"):
        try:
            value_df, benchmark_df, positions_df, log_df = get_pilot_data(pilot_type, name)
        except Exception as e:
            st.error(f"❌ {e}")
            st.stop()

    if value_df.empty:
        st.warning("Aucune donnée exploitable trouvée pour ce nom.")
        st.stop()

    # --- Métriques clés ---
    last_value = value_df["total_value"].iloc[-1]
    col1, col2, col3 = st.columns(3)
    col1.metric("Valeur actuelle du portefeuille", f"${last_value:,.0f}")

    if not benchmark_df.empty:
        last_benchmark = benchmark_df["benchmark_value"].iloc[-1]
        col2.metric("Équivalent S&P 500", f"${last_benchmark:,.0f}")
        diff_pct = (last_value / last_benchmark - 1) * 100 if last_benchmark else 0
        col3.metric("Écart vs S&P 500", f"{diff_pct:+.1f}%",
                    delta=f"{diff_pct:+.1f}%", delta_color="normal")

    # --- Graphique de performance interactif (normalisé à une base commune de 10 000$) ---
    st.subheader("Performance réelle dans le temps")
    st.caption(
        "Les deux courbes sont indexées à une base commune de 10 000$ **au début de la période "
        "affichée** (pas depuis le tout début), pour comparer des **performances** plutôt que des "
        "montants absolus."
    )

    range_key = st.segmented_control(
        "Période affichée",
        options=["3M", "6M", "YTD", "MAX"],
        format_func=lambda k: {"3M": "3 mois", "6M": "6 mois", "YTD": "Depuis janvier", "MAX": "Depuis le début"}[k],
        default="MAX",
    )
    range_key = range_key or "MAX"  # segmented_control peut renvoyer None si désélectionné

    value_df_filtered = core.filter_by_range(value_df, "date", range_key)
    benchmark_df_filtered = core.filter_by_range(benchmark_df, "date", range_key) if not benchmark_df.empty else benchmark_df

    value_df_norm = core.normalize_to_base(value_df_filtered, "total_value", base=10_000)
    benchmark_df_norm = core.normalize_to_base(benchmark_df_filtered, "benchmark_value", base=10_000) if not benchmark_df_filtered.empty else benchmark_df_filtered

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=value_df_norm["date"], y=value_df_norm["total_value"],
        name=f"Portefeuille de {name}", line=dict(color="#1a3a5c", width=2),
    ))
    if not benchmark_df_norm.empty:
        fig.add_trace(go.Scatter(
            x=benchmark_df_norm["date"], y=benchmark_df_norm["benchmark_value"],
            name="S&P 500 (base 10 000$ identique)",
            line=dict(color="#888888", width=1.5, dash="dash"),
        ))
    fig.update_layout(
        hovermode="x unified", height=450,
        yaxis_title="Valeur (base 10 000$)", margin=dict(l=0, r=0, t=10, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig, width='stretch')

    # --- Positions actuelles ---
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

    # --- Historique des mouvements ---
    st.subheader("Historique des mouvements")
    st.dataframe(log_df, width='stretch', hide_index=True)

else:
    st.info("👈 Choisis un type de pilote et un nom dans la barre latérale, puis clique sur \"Analyser\".")
