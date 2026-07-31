"""
Simulateur de portefeuille virtuel qui copie les transactions d'un
"pilote" (politicien ou gérant de fonds), sans connecter de vrai compte de
courtage -- calcule uniquement la valeur théorique dans le temps à partir
de vrais prix historiques (Stooq).

Règle de simulation (simplification assumée et documentée clairement) :
- Un ACHAT investit une fraction fixe (par défaut 10%) de la valeur totale
  actuelle du portefeuille virtuel dans le titre concerné, au prix du jour
  de divulgation (transaction Congrès) ou de rapport (13F).
- Une VENTE liquide l'intégralité de la position existante sur ce titre.

Pourquoi une fraction fixe plutôt que le montant exact déclaré : les
montants déclarés sont à une échelle totalement différente du capital
virtuel de départ (fourchettes du STOCK Act en millions de $, positions 13F
en dizaines/centaines de millions) -- copier le montant exact n'aurait
aucun sens pour un portefeuille de test à 10 000$. La fraction fixe permet
de répliquer fidèlement la STRUCTURE des décisions (quels titres, à quel
moment) sans prétendre reproduire l'échelle réelle des transactions.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
from data_sources.price_data import get_price_history, get_price_on_or_after


class PortfolioSimulator:
    def __init__(self, starting_cash: float = 10_000.0, buy_fraction: float = 0.10):
        """
        Args:
            starting_cash: capital virtuel de départ
            buy_fraction: fraction de la valeur totale du portefeuille
                investie à chaque achat simulé (défaut 10%)
        """
        self.starting_cash = starting_cash
        self.buy_fraction = buy_fraction
        self.cash = starting_cash
        self.holdings = {}       # ticker -> nombre d'actions détenues
        self.price_cache = {}    # ticker -> DataFrame de prix (mis en cache, pas retéléchargé à chaque appel)
        self.history = []        # historique de TOUTES les transactions tentées (exécutées ou non)

    def _get_price_series(self, ticker: str) -> pd.DataFrame:
        if ticker not in self.price_cache:
            self.price_cache[ticker] = get_price_history(ticker)
        return self.price_cache[ticker]

    def _current_holdings_value(self, as_of_date) -> float:
        total = 0.0
        for ticker, shares in self.holdings.items():
            if shares <= 0:
                continue
            try:
                price = get_price_on_or_after(self._get_price_series(ticker), as_of_date)
                total += shares * price
            except Exception:
                continue  # prix indisponible à cette date (ex: titre pas encore coté à l'époque) -> ignoré
        return total

    def total_value(self, as_of_date) -> float:
        """Valeur totale du portefeuille (liquidités + positions) à une date donnée."""
        return self.cash + self._current_holdings_value(as_of_date)

    def process_trades(self, trades: pd.DataFrame):
        """
        Traite une liste de transactions à simuler.

        Args:
            trades: DataFrame avec colonnes obligatoires:
                - date: date à laquelle simuler l'action (date de divulgation, pas la date réelle de trade)
                - ticker: symbole boursier
                - action: "buy" ou "sell"
        """
        trades = trades.sort_values("date").reset_index(drop=True)

        for _, trade in trades.iterrows():
            date = trade["date"]
            ticker = trade["ticker"]
            action = str(trade["action"]).lower()

            try:
                price_series = self._get_price_series(ticker)
                price = get_price_on_or_after(price_series, date)
            except Exception as e:
                self.history.append({
                    "date": date, "ticker": ticker, "action": action,
                    "status": "ignoré (prix indisponible)", "detail": str(e),
                    "price": None, "amount_usd": None, "shares": None,
                })
                continue

            if action == "buy":
                self._execute_buy(date, ticker, price)
            elif action == "sell":
                self._execute_sell(date, ticker, price)
            else:
                self.history.append({
                    "date": date, "ticker": ticker, "action": action,
                    "status": f"ignoré (action inconnue: '{action}')",
                    "price": price, "amount_usd": None, "shares": None,
                })

    def _execute_buy(self, date, ticker, price):
        portfolio_value = self.total_value(date)
        amount_to_invest = min(portfolio_value * self.buy_fraction, self.cash)

        if amount_to_invest <= 0:
            self.history.append({
                "date": date, "ticker": ticker, "action": "buy",
                "status": "ignoré (pas de liquidités disponibles)",
                "price": price, "amount_usd": None, "shares": None,
            })
            return

        shares_bought = amount_to_invest / price
        self.holdings[ticker] = self.holdings.get(ticker, 0) + shares_bought
        self.cash -= amount_to_invest
        self.history.append({
            "date": date, "ticker": ticker, "action": "buy",
            "status": "exécuté", "price": price,
            "amount_usd": amount_to_invest, "shares": shares_bought,
        })

    def _execute_sell(self, date, ticker, price):
        shares_held = self.holdings.get(ticker, 0)
        if shares_held <= 0:
            self.history.append({
                "date": date, "ticker": ticker, "action": "sell",
                "status": "ignoré (aucune position détenue à vendre)",
                "price": price, "amount_usd": None, "shares": None,
            })
            return

        proceeds = shares_held * price
        self.cash += proceeds
        self.holdings[ticker] = 0
        self.history.append({
            "date": date, "ticker": ticker, "action": "sell",
            "status": "exécuté", "price": price,
            "amount_usd": proceeds, "shares": shares_held,
        })

    def value_over_time(self, start_date=None, end_date=None, freq: str = "W") -> pd.DataFrame:
        """
        Calcule la valeur totale du portefeuille à intervalles réguliers
        (hebdomadaire par défaut) entre la première transaction exécutée et
        aujourd'hui (ou end_date si fourni).
        """
        executed = [h for h in self.history if h.get("status") == "exécuté"]
        if not executed:
            return pd.DataFrame(columns=["date", "total_value"])

        if start_date is None:
            start_date = min(h["date"] for h in executed)
        if end_date is None:
            end_date = pd.Timestamp.today()

        dates = pd.date_range(start_date, end_date, freq=freq)
        records = [{"date": d, "total_value": self.total_value(d)} for d in dates]
        return pd.DataFrame(records)

    def get_transaction_log(self) -> pd.DataFrame:
        """Retourne l'historique complet des transactions tentées (exécutées ou non, avec la raison)."""
        return pd.DataFrame(self.history)

    def get_current_positions(self) -> pd.DataFrame:
        """Retourne les positions actuellement détenues (hors positions à zéro)."""
        today = pd.Timestamp.today()
        records = []
        for ticker, shares in self.holdings.items():
            if shares <= 0:
                continue
            try:
                price = get_price_on_or_after(self._get_price_series(ticker), today)
                records.append({"ticker": ticker, "shares": shares, "current_price": price,
                                 "current_value": shares * price})
            except Exception:
                records.append({"ticker": ticker, "shares": shares, "current_price": None,
                                 "current_value": None})
        return pd.DataFrame(records)
