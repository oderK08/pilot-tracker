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
        self.holdings_timeline = []   # Congrès : (date, {ticker: shares}) -- ÉVÉNEMENTS DISCRETS à date réelle connue
        self.quarterly_snapshots = [] # 13F : (date, {ticker: shares}) -- instantanés de FIN DE TRIMESTRE, à INTERPOLER entre eux (voir _interpolated_holdings_at)
        self.option_positions = []    # liste de dicts {ticker, option_type, strike, expiration, num_contracts, entry_date, exit_date}
        self.price_cache = {}         # ticker -> DataFrame de prix (mis en cache)
        self.history = []             # historique de tous les mouvements (achats/ventes/positions/trimestres/options)

    def _get_price_series(self, ticker: str) -> pd.DataFrame:
        """
        Récupère (et met en cache) l'historique de prix complet d'un ticker.

        ⚠️ Passe TOUJOURS un `start` explicite -- sans ça, yfinance se
        replie silencieusement sur seulement le DERNIER MOIS de données
        (comportement par défaut quand start/end sont tous les deux None),
        ce qui gelait la valorisation de toute date plus ancienne sur un
        unique prix figé (bug corrigé : ça donnait des paliers plats sur
        les 13F au lieu de vraies variations quotidiennes).
        """
        if ticker not in self.price_cache:
            start = (pd.Timestamp.today() - pd.DateOffset(years=6)).strftime("%Y-%m-%d")
            self.price_cache[ticker] = get_price_history(ticker, start=start)
        return self.price_cache[ticker]

    def _holdings_at(self, as_of_date) -> dict:
        """
        Retourne les positions ACTIONS réellement détenues à une date
        donnée (cas Congrès) -- le dernier point de la timeline dont la
        date est <= as_of_date, pas l'état final après toutes les
        transactions. Les dates ici sont RÉELLES (dates de divulgation
        connues), donc un saut net à cette date précise est correct.
        """
        if not self.holdings_timeline:
            return {}
        as_of_date = pd.Timestamp(as_of_date)
        applicable = [(d, h) for d, h in self.holdings_timeline if d <= as_of_date]
        if not applicable:
            return {}
        return applicable[-1][1]

    def _interpolated_holdings_at(self, as_of_date) -> dict:
        """
        Retourne les positions 13F INTERPOLÉES linéairement entre les deux
        trimestres connus qui encadrent as_of_date.

        ⚠️ Pourquoi interpoler plutôt qu'un saut net (comme pour le
        Congrès) : le 13F ne donne AUCUNE date exacte d'achat/vente à
        l'intérieur du trimestre -- seulement un instantané au dernier
        jour du trimestre. Attribuer tout le rééquilibrage du trimestre à
        ce seul jour créait un "mur" vertical artificiel dans le
        graphique de performance, qui n'a jamais vraiment eu lieu ainsi
        dans la réalité (le vrai rééquilibrage s'est étalé sur les ~90
        jours précédents, on ne sait juste pas exactement comment). Une
        interpolation linéaire est une approximation, mais bien plus
        honnête qu'un saut instantané.
        """
        if not self.quarterly_snapshots:
            return {}

        as_of_date = pd.Timestamp(as_of_date)
        snapshots = self.quarterly_snapshots  # déjà trié par set_holdings_timeline

        if as_of_date <= snapshots[0][0]:
            return snapshots[0][1]
        if as_of_date >= snapshots[-1][0]:
            return snapshots[-1][1]

        for i in range(len(snapshots) - 1):
            date_a, holdings_a = snapshots[i]
            date_b, holdings_b = snapshots[i + 1]
            if date_a <= as_of_date <= date_b:
                total_days = (date_b - date_a).days
                elapsed_days = (as_of_date - date_a).days
                frac = elapsed_days / total_days if total_days > 0 else 1.0

                all_tickers = set(holdings_a.keys()) | set(holdings_b.keys())
                interpolated = {}
                for ticker in all_tickers:
                    shares_a = holdings_a.get(ticker, 0)
                    shares_b = holdings_b.get(ticker, 0)
                    interpolated[ticker] = shares_a + (shares_b - shares_a) * frac
                return interpolated

        return snapshots[-1][1]  # ne devrait normalement pas être atteint

    def _options_value_at(self, as_of_date) -> float:
        """
        Valeur des positions d'OPTIONS ouvertes à une date donnée, par
        VALEUR INTRINSÈQUE uniquement : max(0, prix - strike) x 100 pour un
        call, max(0, strike - prix) x 100 pour un put, x nombre de
        contrats.

        ⚠️ Approximation assumée : la valeur intrinsèque ignore la "valeur
        temps" de l'option (la prime restante liée à la volatilité et au
        temps avant échéance) -- une vraie valorisation demanderait un
        modèle d'options (Black-Scholes) et des données de volatilité
        implicite, hors de portée d'un projet gratuit. Cette approximation
        est BEAUCOUP plus fidèle que de traiter l'option comme un achat
        d'action au comptant (l'erreur qu'on corrige ici), mais reste une
        approximation, pas une valorisation de marché exacte.
        """
        as_of_date = pd.Timestamp(as_of_date)
        total = 0.0
        for pos in self.option_positions:
            if pos["entry_date"] > as_of_date:
                continue
            if pos["exit_date"] is not None and pos["exit_date"] <= as_of_date:
                continue
            if as_of_date > pos["expiration_date"]:
                continue  # option expirée -> considérée à 0 (simplification, voir docstring de process_option_trades)

            try:
                price = get_price_on_or_after(self._get_price_series(pos["ticker"]), as_of_date)
            except Exception:
                continue

            if pos["option_type"] == "call":
                intrinsic = max(0.0, price - pos["strike_price"])
            else:
                intrinsic = max(0.0, pos["strike_price"] - price)

            total += intrinsic * 100 * pos["num_contracts"]
        return total

    def total_value(self, as_of_date) -> float:
        """
        Valeur de marché réelle du portefeuille (actions + options détenues
        À CETTE DATE, valorisées à cette date).

        Utilise les positions 13F INTERPOLÉES entre trimestres si elles
        existent (voir _interpolated_holdings_at), sinon les événements
        Congrès à date réelle (voir _holdings_at) -- un seul des deux
        mécanismes est utilisé selon comment le simulateur a été alimenté
        (process_trades pour le Congrès, set_holdings_timeline pour le 13F).
        """
        holdings = self._interpolated_holdings_at(as_of_date) if self.quarterly_snapshots else self._holdings_at(as_of_date)
        total = 0.0
        for ticker, shares in holdings.items():
            if shares <= 0:
                continue
            try:
                price = get_price_on_or_after(self._get_price_series(ticker), as_of_date)
                total += shares * price
            except Exception:
                continue  # prix indisponible à cette date -> ignoré
        total += self._options_value_at(as_of_date)
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

    def process_option_trades(self, option_trades: pd.DataFrame):
        """
        Traite une liste de transactions D'OPTIONS -- distinct de
        process_trades() car une option n'est pas une action (effet de
        levier, prix d'exercice, échéance), la traiter comme un achat
        d'action au comptant serait économiquement faux.

        Args:
            option_trades: DataFrame avec colonnes obligatoires:
                - date: date de divulgation
                - ticker: symbole boursier SOUS-JACENT (pas l'option elle-même)
                - action: "buy" ou "sell"
                - option_type: "call" ou "put"
                - strike_price: prix d'exercice
                - expiration_date: date d'échéance
                - num_contracts: nombre de contrats (1 contrat = 100 actions)
                - dollar_amount: prime payée/reçue (pour le journal, n'affecte pas la valorisation)

        À l'échéance (expiration_date dépassée), une position est
        considérée à 0 -- simplification : en réalité, une option "in the
        money" à l'échéance serait exercée (convertie en actions) ou son
        détenteur recevrait sa valeur intrinsèque en cash selon le
        contrat -- ce cas n'est pas modélisé ici, la position disparaît
        simplement de la valorisation après l'échéance.
        """
        option_trades = option_trades.sort_values("date").reset_index(drop=True)

        for _, trade in option_trades.iterrows():
            date = trade["date"]
            ticker = trade["ticker"]
            action = str(trade["action"]).lower()

            if action == "buy":
                self.option_positions.append({
                    "ticker": ticker,
                    "option_type": str(trade["option_type"]).lower(),
                    "strike_price": trade["strike_price"],
                    "expiration_date": pd.Timestamp(trade["expiration_date"]),
                    "num_contracts": trade["num_contracts"],
                    "entry_date": date,
                    "exit_date": None,
                })
                self.history.append({
                    "date": date, "ticker": ticker,
                    "action": f"achat option ({trade['option_type']}, strike {trade['strike_price']}, "
                              f"échéance {pd.Timestamp(trade['expiration_date']).date()})",
                    "status": "exécuté", "price": None,
                    "amount_usd": trade.get("dollar_amount"), "shares": trade["num_contracts"],
                })

            elif action == "sell":
                # Ferme la position OUVERTE la plus ancienne sur ce même ticker
                # (rapprochement approximatif -- les déclarations ne permettent
                # pas toujours de savoir EXACTEMENT quelle position est fermée
                # si plusieurs positions existent sur le même titre).
                open_positions = [p for p in self.option_positions
                                  if p["ticker"] == ticker and p["exit_date"] is None]
                if not open_positions:
                    self.history.append({
                        "date": date, "ticker": ticker, "action": "vente option",
                        "status": "ignoré (aucune position d'option ouverte à fermer)",
                        "price": None, "amount_usd": None, "shares": None,
                    })
                    continue
                open_positions.sort(key=lambda p: p["entry_date"])
                open_positions[0]["exit_date"] = date
                self.history.append({
                    "date": date, "ticker": ticker, "action": "vente option (position fermée)",
                    "status": "exécuté", "price": None,
                    "amount_usd": trade.get("dollar_amount"), "shares": open_positions[0]["num_contracts"],
                })
            else:
                self.history.append({
                    "date": date, "ticker": ticker, "action": action,
                    "status": f"ignoré (action inconnue: '{action}')",
                    "price": None, "amount_usd": None, "shares": None,
                })

    def set_holdings_timeline(self, snapshots: list):
        """
        Fixe une SUITE d'instantanés 13F dans le temps -- interpolés
        linéairement entre eux lors du calcul de la valeur (voir
        _interpolated_holdings_at), car le 13F ne donne aucune date exacte
        de transaction à l'intérieur du trimestre, seulement l'instantané
        de fin de trimestre.

        Calcule aussi la DIFFÉRENCE avec le trimestre précédent pour chaque
        titre, afin de journaliser explicitement s'il s'agit d'un
        renforcement, d'une réduction, d'une nouvelle position ou d'une
        position totalement clôturée -- ce journal reste un événement
        discret à la date du dépôt (utile pour l'historique affiché),
        seule la VALORISATION jour par jour est interpolée.

        Args:
            snapshots: liste de (date, {ticker: nombre_actions_reel}), pas
                besoin d'être pré-triée (fait automatiquement)
        """
        new_snapshots = sorted(snapshots, key=lambda s: s[0])
        self.quarterly_snapshots.extend(new_snapshots)
        self.quarterly_snapshots.sort(key=lambda s: s[0])

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

    def _value_with_holdings(self, holdings: dict, as_of_date) -> float:
        """Valeur de marché d'un jeu de positions DONNÉ (pas forcément celui actif à cette date), à une date donnée."""
        total = 0.0
        for ticker, shares in holdings.items():
            if shares <= 0:
                continue
            try:
                price = get_price_on_or_after(self._get_price_series(ticker), as_of_date)
                total += shares * price
            except Exception:
                continue
        return total

    def performance_index_over_time(self, start_date=None, end_date=None, freq: str = "D") -> pd.DataFrame:
        """
        Calcule un indice de PERFORMANCE (base 100 au départ) par
        RENDEMENT PONDÉRÉ DANS LE TEMPS ("time-weighted return") -- la
        méthode standard en reporting de performance financière pour
        neutraliser les rééquilibrages et changements de taille de
        portefeuille, qui ne doivent JAMAIS être comptés comme un gain ou
        une perte de performance.

        ⚠️ Pourquoi c'est nécessaire (et pas juste value_over_time() en %) :
        un simple changement de composition (vendre X pour acheter Y de
        valeur égale) ou de taille (déplacer du capital vers du cash non
        suivi par le 13F) fait changer la valeur ABSOLUE suivie, même si
        aucun "vrai" gain/perte n'a eu lieu. Normaliser cette valeur
        absolue en % ferait apparaître ce changement de taille comme une
        fausse performance. Ici, à chaque transition de trimestre 13F, on
        calcule le rendement RÉEL du jeu de positions PRÉCÉDENT (juste
        avant le rééquilibrage), on l'enchaîne à l'indice cumulé, puis on
        repart avec les nouvelles positions SANS compter le changement de
        taille lui-même.

        Pour le cas Congrès (pas de trimestres 13F), la valeur absolue
        correspond déjà aux vrais montants investis lors de vraies
        transactions -- pas de rééquilibrage "capital flow" au même sens,
        donc value_over_time() convertie en % est déjà correcte.
        """
        if not self.quarterly_snapshots:
            value_df = self.value_over_time(start_date, end_date, freq)
            if value_df.empty or not value_df["total_value"].iloc[0]:
                return pd.DataFrame(columns=["date", "performance_index"])
            first_value = value_df["total_value"].iloc[0]
            value_df["performance_index"] = value_df["total_value"] / first_value * 100
            return value_df[["date", "performance_index"]]

        snapshots = self.quarterly_snapshots
        if start_date is None:
            start_date = snapshots[0][0]
        if end_date is None:
            end_date = pd.Timestamp.today()

        dates = pd.date_range(start_date, end_date, freq=freq)
        records = []
        segment_start_index = 100.0
        current_segment = 0

        for d in dates:
            # Avancer au bon segment (trimestre actif), en enchaînant le
            # rendement RÉEL de chaque segment qui vient de se terminer
            # AVANT de passer au suivant.
            while current_segment < len(snapshots) - 1 and d >= snapshots[current_segment + 1][0]:
                date_i, holdings_i = snapshots[current_segment]
                date_next, _ = snapshots[current_segment + 1]
                value_start = self._value_with_holdings(holdings_i, date_i)
                value_end = self._value_with_holdings(holdings_i, date_next)
                if value_start > 0:
                    segment_start_index = segment_start_index * (value_end / value_start)
                current_segment += 1

            date_i, holdings_i = snapshots[current_segment]
            value_start = self._value_with_holdings(holdings_i, date_i)
            value_now = self._value_with_holdings(holdings_i, d)
            index_now = segment_start_index * (value_now / value_start) if value_start > 0 else segment_start_index
            records.append({"date": d, "performance_index": index_now})

        return pd.DataFrame(records)

    def get_transaction_log(self) -> pd.DataFrame:
        """Retourne l'historique complet des mouvements (exécutés ou non, avec la raison)."""
        return pd.DataFrame(self.history)

    def get_current_positions(self) -> pd.DataFrame:
        """Retourne les positions actuellement suivies (actions + options, hors positions à zéro/expirées), avec leur valeur réelle."""
        today = pd.Timestamp.today()
        records = []

        holdings = self._interpolated_holdings_at(today) if self.quarterly_snapshots else self._holdings_at(today)
        for ticker, shares in holdings.items():
            if shares <= 0:
                continue
            try:
                price = get_price_on_or_after(self._get_price_series(ticker), today)
                records.append({"ticker": ticker, "shares": shares, "current_price": price,
                                 "current_value": shares * price, "type": "action"})
            except Exception:
                records.append({"ticker": ticker, "shares": shares, "current_price": None,
                                 "current_value": None, "type": "action"})

        for pos in self.option_positions:
            if pos["exit_date"] is not None or today > pos["expiration_date"]:
                continue  # position fermée ou option expirée -> pas une position actuelle
            try:
                price = get_price_on_or_after(self._get_price_series(pos["ticker"]), today)
                if pos["option_type"] == "call":
                    intrinsic = max(0.0, price - pos["strike_price"])
                else:
                    intrinsic = max(0.0, pos["strike_price"] - price)
                value = intrinsic * 100 * pos["num_contracts"]
                label = f"{pos['ticker']} {pos['option_type']} {pos['strike_price']}$ (éch. {pos['expiration_date'].date()})"
                records.append({"ticker": label, "shares": pos["num_contracts"], "current_price": price,
                                 "current_value": value, "type": "option"})
            except Exception:
                continue

        return pd.DataFrame(records)
