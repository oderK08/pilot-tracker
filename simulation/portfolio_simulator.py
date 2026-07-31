"""
Reconstitue la valeur RÉELLE (pas simulée à échelle réduite) d'un
portefeuille qui suit les positions déclarées d'un "pilote" (politicien ou
gérant de fonds), à partir de vrais montants/quantités et de vrais prix
historiques.

Deux sources, deux niveaux de précision différents (limite légale, pas un
choix de modélisation) :
- 13F (hedge funds) : le dépôt donne le nombre RÉEL et EXACT d'actions
  détenues -- on les utilise telles quelles, aucune estimation.
- Congrès (STOCK Act) : la loi n'oblige à déclarer qu'une FOURCHETTE de
  montant, jamais un montant exact ni un nombre d'actions. Le montant réel
  investi est donc estimé au MILIEU de la fourchette déclarée.

⚠️ Point de conception CRITIQUE (bug corrigé) : la valeur du portefeuille à
une date donnée doit toujours être calculée à partir des positions RÉELLEMENT
DÉTENUES À CETTE DATE-LÀ, pas des positions finales après TOUTES les
transactions traitées. Une version précédente utilisait un dict `self.holdings`
muté en place au fil du traitement des transactions, puis interrogé après
coup pour n'importe quelle date -- ce qui donnait des valeurs FAUSSES pour
toute date antérieure à une vente (le dict reflétait déjà l'état final,
post-vente, même quand on demandait la valeur à une date où la position
était encore détenue). Toutes les valeurs sont maintenant dérivées d'une
timeline explicite de positions (une entrée par changement réel), qu'on
interroge à la bonne date à chaque fois.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
from data_sources.price_data import get_price_history, get_price_on_or_after


class PortfolioSimulator:
    def __init__(self):
        self.holdings_timeline = []  # liste de (date, {ticker: shares}) -- état CUMULATIF après chaque événement, triée chronologiquement
        self.price_cache = {}        # ticker -> DataFrame de prix (mis en cache)
        self.history = []            # historique de tous les mouvements (achats/ventes/positions/trimestres)

    def _get_price_series(self, ticker: str) -> pd.DataFrame:
        if ticker not in self.price_cache:
            self.price_cache[ticker] = get_price_history(ticker)
        return self.price_cache[ticker]

    def _holdings_at(self, as_of_date) -> dict:
        """
        Retourne les positions RÉELLEMENT détenues à une date donnée -- le
        dernier point de la timeline dont la date est <= as_of_date, pas
        l'état final après toutes les transactions.
        """
        if not self.holdings_timeline:
            return {}
        as_of_date = pd.Timestamp(as_of_date)
        applicable = [(d, h) for d, h in self.holdings_timeline if d <= as_of_date]
        if not applicable:
            return {}
        return applicable[-1][1]

    def total_value(self, as_of_date) -> float:
        """Valeur de marché réelle du portefeuille (positions détenues À CETTE DATE x prix à cette date)."""
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
        de la fourchette déclarée pour le Congrès), en enregistrant un point
        de la timeline après CHAQUE transaction exécutée -- pas seulement
        l'état final.

        Args:
            trades: DataFrame avec colonnes obligatoires:
                - date: date de divulgation (pas la date réelle du trade)
                - ticker: symbole boursier
                - action: "buy" ou "sell"
                - dollar_amount: montant réel estimé de la transaction (pour un achat)
        """
        trades = trades.sort_values("date").reset_index(drop=True)
        current_holdings = {}

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
                current_holdings[ticker] = current_holdings.get(ticker, 0) + shares_bought
                self.history.append({
                    "date": date, "ticker": ticker, "action": "buy",
                    "status": "exécuté", "price": price,
                    "amount_usd": dollar_amount, "shares": shares_bought,
                })
                self.holdings_timeline.append((date, dict(current_holdings)))

            elif action == "sell":
                shares_held = current_holdings.get(ticker, 0)
                if shares_held <= 0:
                    self.history.append({
                        "date": date, "ticker": ticker, "action": "sell",
                        "status": "ignoré (aucune position suivie à vendre)",
                        "price": price, "amount_usd": None, "shares": None,
                    })
                    continue
                proceeds = shares_held * price
                current_holdings[ticker] = 0
                self.history.append({
                    "date": date, "ticker": ticker, "action": "sell",
                    "status": "exécuté", "price": price,
                    "amount_usd": proceeds, "shares": shares_held,
                })
                self.holdings_timeline.append((date, dict(current_holdings)))
            else:
                self.history.append({
                    "date": date, "ticker": ticker, "action": action,
                    "status": f"ignoré (action inconnue: '{action}')",
                    "price": price, "amount_usd": None, "shares": None,
                })

        self.holdings_timeline.sort(key=lambda s: s[0])

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
        position totalement clôturée.

        Args:
            snapshots: liste de (date, {ticker: nombre_actions_reel}), pas
                besoin d'être pré-triée (fait automatiquement)
        """
        new_snapshots = sorted(snapshots, key=lambda s: s[0])
        self.holdings_timeline.extend(new_snapshots)
        self.holdings_timeline.sort(key=lambda s: s[0])

        previous_holdings = {}
        for date, holdings in new_snapshots:
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

    def value_over_time(self, start_date=None, end_date=None, freq: str = "D") -> pd.DataFrame:
        """
        Calcule la valeur réelle du portefeuille à intervalles réguliers
        (quotidien par défaut) entre le premier mouvement exécuté et
        aujourd'hui (ou end_date si fourni) -- en utilisant, pour CHAQUE
        date, les positions réellement détenues à CETTE date précise (voir
        _holdings_at), pas l'état final.
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
