def compute_benchmark_dca(trades: pd.DataFrame) -> pd.DataFrame:
    """
    Benchmark pour le cas Congrès : simplement la performance RÉELLE du
    S&P 500 (SPY) depuis le premier jour du portefeuille suivi, tenu sans
    y toucher -- pas une reproduction transaction par transaction.

    Une version précédente essayait de rejouer chaque achat/vente réel sur
    SPY à la place du titre réel, mais ça créait un biais : le vrai
    portefeuille échoue parfois à exécuter une transaction (ticker
    introuvable, prix indisponible chez la source de prix), alors que SPY,
    lui, a TOUJOURS un prix disponible -- le repère accumulait donc des
    achats qui n'avaient en réalité pas eu lieu côté portefeuille réel,
    créant des écarts artificiels. Un simple "buy & hold" du S&P 500 depuis
    le premier jour évite ce biais et reste le repère le plus standard et
    le plus lisible en finance.
    """
    if trades.empty:
        return pd.DataFrame(columns=["date", "benchmark_value"])

    first_date = trades["date"].min()
    return compute_benchmark_lump_sum(10_000.0, first_date)
