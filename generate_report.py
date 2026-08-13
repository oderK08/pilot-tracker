"""
Script CLI : produit un rapport sur disque pour un "pilote" -- pensé pour
un run automatisé (GitHub Actions).

Deux rapports différents, parce que les deux sources ne disent pas la même
chose (voir l'en-tête de core.py) :
  - congress   : graphiques de performance + positions + journal des
                 transactions.
  - hedge_fund : tableau des positions et de leurs variations
                 trimestrielles (CSV) + graphique des poids. AUCUNE
                 performance -- un 13F ne permet pas de la calculer
                 honnêtement (voir analysis/holdings_view.py).

Toute la logique de données vit dans core.py, partagée avec l'application
interactive (streamlit_app.py). Ce fichier ne s'occupe que du rendu
statique et de l'écriture sur disque.

Usage :
    python generate_report.py --pilot congress --name "Nancy Pelosi"
    python generate_report.py --pilot hedge_fund --name "Berkshire Hathaway"
    python generate_report.py --pilot hedge_fund --name "Scion Asset Management" --quarters 8
"""
import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import core
from analysis import holdings_view

OUTPUT_DIR = "output"


def generate_congress(name: str, output_dir: str = OUTPUT_DIR):
    os.makedirs(output_dir, exist_ok=True)
    safe_name = name.lower().replace(" ", "_")

    sim, benchmark_df = core.run_simulation("congress", name)

    value_df = sim.value_over_time()
    if value_df.empty:
        raise RuntimeError(
            f"[generate_report] Aucun mouvement n'a pu être reconstitué pour '{name}' -- "
            "impossible de calculer une performance. Vérifie le journal "
            "(sim.get_transaction_log()) pour comprendre pourquoi."
        )

    # --- Graphique 1 : performance réelle vs S&P 500 (normalisé à une base commune de 10 000$) ---
    value_df_norm = core.normalize_to_base(value_df, "total_value", base=10_000)
    benchmark_df_norm = core.normalize_to_base(benchmark_df, "benchmark_value", base=10_000) if not benchmark_df.empty else benchmark_df

    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(value_df_norm["date"], value_df_norm["total_value"], color="#1a3a5c", linewidth=2,
            label=f"Portefeuille de {name}")
    if not benchmark_df_norm.empty:
        ax.plot(benchmark_df_norm["date"], benchmark_df_norm["benchmark_value"], color="#888888",
                linewidth=1.5, linestyle="--", label="S&P 500 (base 10 000$ identique)")
    ax.set_ylabel("Valeur (base 10 000$)")
    ax.set_title(f"Portefeuille réel reconstitué : {name}", fontsize=13, fontweight="bold", loc="left")
    ax.legend(loc="upper left", frameon=False)
    fig.tight_layout()
    perf_path = os.path.join(output_dir, f"{safe_name}_performance.png")
    fig.savefig(perf_path, dpi=150)
    plt.close(fig)
    print(f"[generate_report] Graphique de performance sauvegardé: {perf_path}")

    # --- Graphique 2 : positions actuelles réelles ---
    positions = sim.get_current_positions()
    if not positions.empty:
        positions = positions.dropna(subset=["current_value"])

    positions_path = None
    if not positions.empty:
        positions = positions.sort_values("current_value", ascending=True)
        fig, ax = plt.subplots(figsize=(10, max(4, len(positions) * 0.4)))
        ax.barh(positions["ticker"], positions["current_value"], color="#2e8b57")
        ax.set_xlabel("Valeur actuelle réelle ($)")
        ax.set_title(f"Positions actuelles réelles : {name}", fontsize=13, fontweight="bold", loc="left")
        fig.tight_layout()
        positions_path = os.path.join(output_dir, f"{safe_name}_positions.png")
        fig.savefig(positions_path, dpi=150)
        plt.close(fig)
        print(f"[generate_report] Graphique des positions sauvegardé: {positions_path}")
    else:
        print("[generate_report] Aucune position actuelle exploitable à afficher.")

    # --- Export 3 : historique réel des mouvements ---
    log = sim.get_transaction_log()
    log_path = os.path.join(output_dir, f"{safe_name}_transactions.csv")
    log.to_csv(log_path, index=False)
    print(f"[generate_report] Historique des transactions sauvegardé: {log_path}")

    return {"performance": perf_path, "positions": positions_path, "transactions": log_path}


def generate_hedge_fund(name: str, n_quarters: int = holdings_view.DEFAULT_QUARTERS,
                        output_dir: str = OUTPUT_DIR):
    """
    Rapport 13F : le tableau des positions et de leurs variations, plus un
    graphique de l'évolution des poids des principales lignes.
    """
    os.makedirs(output_dir, exist_ok=True)
    safe_name = name.lower().replace(" ", "_")

    view = core.get_hedge_fund_view(name, n_quarters=n_quarters)
    positions, quarters, summary = view["positions"], view["quarters"], view["summary"]
    if positions.empty:
        raise RuntimeError(f"[generate_report] Aucune position 13F exploitable pour '{name}'.")

    print(f"[generate_report] {name} au {summary['report_date'].date()}: "
          f"{summary['n_positions']} positions, top 10 = {summary['top10_pct']:.0f}%, "
          f"{summary['n_new']} entrée(s), {summary['n_exited']} sortie(s).")

    # --- Export 1 : le tableau des positions ---
    display = holdings_view.to_display_frame(positions, quarters)
    positions_path = os.path.join(output_dir, f"{safe_name}_positions.csv")
    display.to_csv(positions_path, index=False)
    print(f"[generate_report] Tableau des positions sauvegardé: {positions_path}")

    # --- Export 2 : les sorties, invisibles dans le tableau ci-dessus ---
    exits_path = None
    if not view["exits"].empty:
        exits_path = os.path.join(output_dir, f"{safe_name}_sorties.csv")
        view["exits"][["label", "security_type", "previous_weight_pct"]].to_csv(exits_path, index=False)
        print(f"[generate_report] Positions liquidées sauvegardées: {exits_path}")

    # --- Graphique : poids des 10 premières lignes au fil des trimestres ---
    chart_path = None
    if len(quarters) > 1:
        ordered = list(reversed(quarters))
        labels = [holdings_view.quarter_label(q) for q in ordered]

        fig, ax = plt.subplots(figsize=(11, 6))
        for _, row in positions.head(10).iterrows():
            weights = [row.get(f"w_{q.date()}") for q in ordered]
            ax.plot(labels, weights, marker="o", linewidth=1.8, label=row["label"])
        ax.set_ylabel("Poids dans le portefeuille déclaré (%)")
        ax.set_title(f"Positions principales : {name} (au {summary['report_date'].date()})",
                     fontsize=13, fontweight="bold", loc="left")
        ax.legend(loc="upper left", frameon=False, fontsize=8, ncol=2)
        fig.tight_layout()
        chart_path = os.path.join(output_dir, f"{safe_name}_poids.png")
        fig.savefig(chart_path, dpi=150)
        plt.close(fig)
        print(f"[generate_report] Graphique des poids sauvegardé: {chart_path}")

    return {"positions": positions_path, "exits": exits_path, "weights_chart": chart_path}


def generate(pilot_type: str, name: str, n_quarters: int = holdings_view.DEFAULT_QUARTERS,
             output_dir: str = OUTPUT_DIR):
    if pilot_type == "congress":
        return generate_congress(name, output_dir=output_dir)
    if pilot_type == "hedge_fund":
        return generate_hedge_fund(name, n_quarters=n_quarters, output_dir=output_dir)
    raise ValueError(f"pilot_type inconnu: '{pilot_type}'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Produit le rapport d'un politicien ou d'un hedge fund.")
    parser.add_argument("--pilot", choices=["congress", "hedge_fund"], required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--quarters", type=int, default=holdings_view.DEFAULT_QUARTERS,
                        help="Nombre de trimestres affichés (hedge fund uniquement).")
    parser.add_argument("--output-dir", default=OUTPUT_DIR)
    args = parser.parse_args()

    generate(args.pilot, args.name, n_quarters=args.quarters, output_dir=args.output_dir)
