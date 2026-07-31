"""
Outil d'analyse (pas un module de production) : classe les membres du
Congrès par NOMBRE RÉEL de transactions dans la fenêtre de données
disponible (~1,5 an glissant), pour identifier objectivement qui trade le
plus -- plutôt que de se fier à des noms "connus" de réputation, qui
peuvent être datés ou biaisés par la couverture médiatique.

Usage :
    python find_most_active_congress.py
    python find_most_active_congress.py --top 20
"""
import argparse

from data_sources.congress_trades import get_all_trades


def rank_most_active(top_n: int = 15):
    df = get_all_trades()

    counts = df.groupby("filer_name").size().sort_values(ascending=False)

    print(f"Fenêtre de données : {df['transaction_date'].min().date()} -> {df['transaction_date'].max().date()}")
    print(f"Nombre total de membres avec au moins 1 transaction : {len(counts)}")
    print()
    print(f"Top {top_n} par nombre de transactions déclarées :")
    print()
    for i, (name, count) in enumerate(counts.head(top_n).items(), start=1):
        tickers_uniques = df[df["filer_name"] == name]["ticker"].nunique()
        print(f"{i:>2}. {name:<30} {count:>4} transactions  ({tickers_uniques} titres différents)")

    return counts.head(top_n)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Classe les membres du Congrès par activité de trading réelle.")
    parser.add_argument("--top", type=int, default=15, help="Nombre de membres à afficher (défaut: 15)")
    args = parser.parse_args()
    rank_most_active(top_n=args.top)
