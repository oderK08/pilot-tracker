"""
Liste curatée des pilotes affichés sur la plateforme -- volontairement
FERMÉE : les visiteurs de l'appli Streamlit ne peuvent choisir que parmi
ces noms, pas taper n'importe quel nom au clavier.

Pourquoi une liste fermée plutôt qu'une recherche libre :
- Contrôle éditorial : on décide nous-mêmes qui est montré (voir
  find_most_active_congress.py -- certains noms trouvés dans les données
  brutes, comme Donald J Trump ou Alan Armstrong, ont été volontairement
  exclus après vérification, voir update_archive.py historique).
- Performance : seuls les pilotes de cette liste sont pré-archivés par
  update_archive.py (voir ce script) -- un nom en dehors de cette liste
  n'aurait de toute façon aucune archive à lire, donc une recherche libre
  serait lente à chaque fois pour n'importe quel nom tapé au hasard.

Ce module est importé à la fois par update_archive.py (qui alimente
l'archive pour ces noms) et par streamlit_app.py (qui limite le menu
déroulant à ces mêmes noms) -- une seule source de vérité pour la liste.
"""

# ⚠️ Les noms doivent correspondre à l'orthographe EXACTE utilisée dans la
# source de données du Congrès (voir find_most_active_congress.py pour
# vérifier) -- une correspondance partielle insensible à la casse est
# utilisée en interne, mais "Ro Khanna" ne matche PAS "Rohit Khanna" (le
# vrai nom dans les données), par exemple.
TRACKED_CONGRESS = [
    "Nancy Pelosi",
    "Michael T. McCaul",
    "Rohit Khanna",
    "Markwayne Mullin",
    "Josh Gottheimer",
    "Gilbert Cisneros",
    "John Phelan",
    "David H McCormick",
    "Scott H. Peters",
    "April McClain Delaney",
    "John Boozman",
]

TRACKED_HEDGE_FUNDS = [
    "Berkshire Hathaway",
    "Bridgewater Associates",
    "Pershing Square Capital Management",
    "Scion Asset Management",
    "Duquesne Family Office",
    "Situational Awareness",  # Leopold Aschenbrenner, CIK 0002045724
]
