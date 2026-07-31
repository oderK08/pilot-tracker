"""
Script CLI : reconstitue la valeur RÉELLE du portefeuille d'un "pilote"
(politicien ou gérant de fonds) et sauvegarde les graphiques (PNG) et
l'historique (CSV) sur disque -- pensé pour un run automatisé (GitHub
Actions).

Toute la logique de données/simulation vit dans core.py, partagée avec
l'application interactive (streamlit_app.py). Ce fichier ne s'occupe que du
rendu statique et de l'écriture sur disque.

Usage :
    python generate_report.py --pilot congress --name "Nancy Pelosi"
    python generate_report.py --pilot hedge_fund --name "Berkshire Hathaway"
"""
import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import core

OUTPUT_DIR = "output"


def generate(pilot_type: str, name: str, output_dir: str = OUTPUT_DIR):
    os.makedirs(output_dir, exist_ok=True)
    safe_name = name.lower().replace(" ", "_")

    sim, benchmark_df = core.run_simulation(pilot_type, name)

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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reconstitue le portefeuille réel d'un politicien ou d'un hedge fund.")
    parser.add_argument("--pilot", choices=["congress", "hedge_fund"], required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--output-dir", default=OUTPUT_DIR)
    args = parser.parse_args()

    generate(args.pilot, args.name, output_dir=args.output_dir)
