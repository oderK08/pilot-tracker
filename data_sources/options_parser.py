"""
Extraction des détails d'une option (call/put, prix d'exercice, échéance,
nombre de contrats) depuis le champ `comment` en texte libre fourni par
congress-trading-monitor -- pas besoin de reparser le PDF nous-mêmes, le
texte est déjà extrait, il ne reste qu'à en tirer les chiffres avec une
regex.

Exemple de texte réel observé :
  "Purchased 200 call options with a strike price of $50 and an
  expiration date of 3/19/27."

⚠️ Cette regex a été construite et testée sur UN SEUL format de phrase
observé en conditions réelles -- la phrase exacte peut varier légèrement
selon les dépôts (autre verbe, autre ordre des informations), auquel cas
certains champs resteront à None plutôt que de planter. À surveiller et
ajuster si des transactions d'options ne sont pas correctement reconnues.
"""
import re
from datetime import datetime

CONTRACTS_PATTERN = re.compile(r"(\d+)\s+(call|put)\s+options?", re.IGNORECASE)
STRIKE_PATTERN = re.compile(r"strike price of \$?([\d,]+(?:\.\d+)?)", re.IGNORECASE)
EXPIRATION_PATTERN = re.compile(r"expiration date of (\d{1,2}/\d{1,2}/\d{2,4})", re.IGNORECASE)


def parse_option_details(comment: str):
    """
    Extrait les détails d'une option depuis un texte libre.

    Returns:
        dict avec num_contracts, option_type ("call"/"put"), strike_price,
        expiration_date (pd.Timestamp) -- ou None si le texte ne
        correspond pas au format attendu (champs manquants).
    """
    if not comment or not isinstance(comment, str):
        return None

    contracts_match = CONTRACTS_PATTERN.search(comment)
    strike_match = STRIKE_PATTERN.search(comment)
    expiration_match = EXPIRATION_PATTERN.search(comment)

    if not (contracts_match and strike_match and expiration_match):
        return None  # format inattendu -- on ne devine pas, on renvoie None explicitement

    num_contracts = int(contracts_match.group(1))
    option_type = contracts_match.group(2).lower()
    strike_price = float(strike_match.group(1).replace(",", ""))

    exp_str = expiration_match.group(1)
    try:
        # Format observé: M/D/YY (ex: "3/19/27")
        expiration_date = datetime.strptime(exp_str, "%m/%d/%y")
    except ValueError:
        try:
            expiration_date = datetime.strptime(exp_str, "%m/%d/%Y")
        except ValueError:
            return None

    return {
        "num_contracts": num_contracts,
        "option_type": option_type,
        "strike_price": strike_price,
        "expiration_date": expiration_date,
    }
