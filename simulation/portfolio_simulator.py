"""
Reconstitue la valeur RÉELLE (pas simulée à échelle réduite) d'un
portefeuille qui suit les positions déclarées d'un "pilote" (politicien ou
gérant de fonds), à partir de vrais montants/quantités et de vrais prix
historiques (Stooq).

Deux sources, deux niveaux de précision différents (limite légale, pas un
choix de modélisation) :
- 13F (hedge funds) : le dépôt donne le nombre RÉEL et EXACT d'actions
  détenues -- on les utilise telles quelles, aucune estimation.
- Congrès (STOCK Act) : la loi n'oblige à déclarer qu'une FOURCHETTE de
  montant (ex: "$1,001 - $15,000"), jamais un montant exact ni un nombre
  d'actions. Le montant réel investi est donc estimé au MILIEU de la
  fourchette déclarée -- la meilleure estimation possible, mais une
  estimation, pas un chiffre exact, parce que le gouvernement lui-même ne
  rend pas le chiffre exact public.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
from data_sources.price_data import get_price_history, get_price_on_or_after


class PortfolioSimulator:
    def __init__(self):
        self.holdings = {}          # utilisé par process_trades (Congrès) -- état courant cumulatif
        self.holdings_timeline = [] # utilisé par set_holdings_timeline (13F multi-trimestres) -- liste de (date, {ticker: shares}), triée
        self.price_cache = {}       # ticker -> DataFrame de prix (mis en cache)
        self.history = []           # historique de tous les mouvements (achats/ventes/positions/trimestres)

    def _get_price_series(self, ticker: str) -> pd.DataFrame:
        if ticker not in self.price_cache:
            self.price_cache[ticker] = get_price_history(ticker)
        return self.price_cache[ticker]

    def _holdings_at(self, as_of_date) -> dict:
        """
        Retourne les positions actives à une date donnée.

        Si une timeline de trimestres 13F a été fournie (set_holdings_timeline),
        on prend le DERNIER trimestre connu à cette date ou avant (les
        positions ne changent qu'aux dates de dépôt réelles, pas en continu).
        Sinon, on utilise l'état cumulatif classique (cas Congrès).
        """
        if self.holdings_timeline:
            as_of_date = pd.Timestamp(as_of_date)
            applicable = [(d, h) for d, h in self.holdings_timeline if d <= as_of_date]
            if not applicable:
                return {}
            return applicable[-1][1]
        return self.holdings

    def total_value(self, as_of_date) -> float:
        """Valeur de marché réelle du portefeuille (actions détenues x prix) à une date donnée."""
        holdings = self._holdings_at(as_of_date)
        total = 0.0
        for ticker, shares in holdings.items():
            if shares <= 0:
                continue
            try:
                price = get_price_on_or_after(self._get_price_series(ticker), as_of_date)
                total += shares * price
            except Exception:
                continue  # prix indisponible à cette date -> ignoré
        return total

    def process_trades(self, trades: pd.DataFrame):
        """
        Traite une liste de transactions RÉELLES (montants estimés au milieu
        de la fourchette déclarée pour le Congrès).

        Args:
            trades: DataFrame avec colonnes obligatoires:
                - date: date de divulgation (pas la date réelle du trade)
                - ticker: symbole boursier
                - action: "buy" ou "sell"
                - dollar_amount: montant réel estimé de la transaction (pour un achat)
        """
        trades = trades.sort_values("date").reset_index(drop=True)

        for _, trade in trades.iterrows():
            date = trade["date"]
            ticker = trade["ticker"]
            action = str(trade["action"]).lower()
            dollar_amount = trade.get("dollar_amount")

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
                if not dollar_amount or dollar_amount <= 0:
                    self.history.append({
                        "date": date, "ticker": ticker, "action": "buy",
                        "status": "ignoré (montant manquant)",
                        "price": price, "amount_usd": None, "shares": None,
                    })
                    continue
                shares_bought = dollar_amount / price
                self.holdings[ticker] = self.holdings.get(ticker, 0) + shares_bought
                self.history.append({
                    "date": date, "ticker": ticker, "action": "buy",
                    "status": "exécuté", "price": price,
                    "amount_usd": dollar_amount, "shares": shares_bought,
                })

            elif action == "sell":
                shares_held = self.holdings.get(ticker, 0)
                if shares_held <= 0:
                    self.history.append({
                        "date": date, "ticker": ticker, "action": "sell",
                        "status": "ignoré (aucune position suivie à vendre)",
                        "price": price, "amount_usd": None, "shares": None,
                    })
                    continue
                proceeds = shares_held * price
                self.holdings[ticker] = 0
                self.history.append({
                    "date": date, "ticker": ticker, "action": "sell",
                    "status": "exécuté", "price": price,
                    "amount_usd": proceeds, "shares": shares_held,
                })
            else:
                self.history.append({
                    "date": date, "ticker": ticker, "action": action,
                    "status": f"ignoré (action inconnue: '{action}')",
                    "price": price, "amount_usd": None, "shares": None,
                })

    def set_holdings_timeline(self, snapshots: list):
        """
        Fixe une SUITE de positions réelles dans le temps -- un trimestre 13F
        après l'autre, chaque nouveau trimestre REMPLAÇANT entièrement les
        positions précédentes sur les titres concernés (contrairement à un
        simple cumul), pour refléter fidèlement les vrais changements de
        portefeuille d'un gérant d'un trimestre à l'autre.

        Calcule aussi la DIFFÉRENCE avec le trimestre précédent pour chaque
        titre, afin de journaliser explicitement s'il s'agit d'un
        renforcement, d'une réduction, d'une nouvelle position ou d'une
        position totalement clôturée -- sans ce calcul, chaque trimestre
        apparaîtrait comme un simple "instantané" sans indiquer le sens du
        mouvement (achat ou vente).

        Args:
            snapshots: liste de (date, {ticker: nombre_actions_reel}), pas
                besoin d'être pré-triée (fait automatiquement)
        """
        self.holdings_timeline = sorted(snapshots, key=lambda s: s[0])
        previous_holdings = {}

        for date, holdings in self.holdings_timeline:
            all_tickers = set(holdings.keys()) | set(previous_holdings.keys())
            for ticker in all_tickers:
                shares_now = holdings.get(ticker, 0)
                shares_before = previous_holdings.get(ticker, 0)
                delta_shares = shares_now - shares_before

                try:
                    price = get_price_on_or_after(self._get_price_series(ticker), date)
                    value_now = shares_now * price
                    delta_value = delta_shares * price
                except Exception:
                    price, value_now, delta_value = None, None, None

                if shares_before == 0 and shares_now > 0:
                    action = "achat (nouvelle position)"
                elif shares_now == 0 and shares_before > 0:
                    action = "vente (position clôturée)"
                elif delta_shares > 0:
                    action = "achat (renforcement)"
                elif delta_shares < 0:
                    action = "vente (réduction)"
                else:
                    action = "position inchangée"

                self.history.append({
                    "date": date, "ticker": ticker, "action": action,
                    "status": "exécuté", "price": price,
                    "amount_usd": value_now, "shares": shares_now,
                    "shares_change": delta_shares, "value_change_usd": delta_value,
                })

            previous_holdings = holdings

    def value_over_time(self, start_date=None, end_date=None, freq: str = "W") -> pd.DataFrame:
        """
        Calcule la valeur réelle du portefeuille à intervalles réguliers
        (hebdomadaire par défaut) entre le premier mouvement exécuté et
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
        """Retourne l'historique complet des mouvements (exécutés ou non, avec la raison)."""
        return pd.DataFrame(self.history)

    def get_current_positions(self) -> pd.DataFrame:
        """Retourne les positions actuellement suivies (hors positions à zéro), avec leur valeur réelle."""
        today = pd.Timestamp.today()
        holdings = self._holdings_at(today)
        records = []
        for ticker, shares in holdings.items():
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
