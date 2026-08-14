# deck-kit

Squelette pour construire des decks HTML animés — type slides de recherche, 16:9,
graphiques SVG faits main, un seul fichier à la livraison.

## Ouvrir

```
open index.html          # version modulaire (développement)
python3 build.py         # fusionne tout -> deck.html (livraison)
```

Navigation : `→` / `←` / espace / clic / swipe · **`R` rejoue l'animation** de la slide.

## Architecture

```
index.html          la charte (variables CSS) + le châssis 16:9 + la nav
engine.js           helpers SVG, primitives d'animation, système de scènes
charts/
  waterfall.js      module « cascade »  — réutilisable à l'infini
  lines.js          module « courbes »  — idem
slides/
  01-cascade.js     UNE SLIDE = QUE DES DONNÉES
  02-courbes.js
build.py            fusionne le tout en un deck.html autonome
```

Règle : **on n'écrit du code que dans `charts/`**. Une nouvelle slide ne contient
que son titre, ses données et le nom du module graphique à utiliser.

## Ajouter une slide

Copier un fichier de `slides/`, changer les données, l'ajouter dans `index.html` :

```html
<script src="slides/03-ma-slide.js"></script>
```

L'ordre des `<script>` = l'ordre des slides.

## Ajouter un type de graphique

Créer `charts/mon-graphique.js` avec ce contrat :

```js
window.CHARTS = window.CHARTS || {};
window.CHARTS.monGraphique = function (svg, data, ctx) {
  // svg  : élément <svg viewBox="0 0 1600 900"> vide, à remplir
  // data : l'objet `data` de la slide
  // ctx  : la boîte à outils du moteur (voir ci-dessous)
  // ...dessiner...
  ctx.done();   // à appeler à la fin -> déclenche l'apparition du punch
};
```

## La boîte à outils (`ctx`)

| Outil | Rôle |
|---|---|
| `el(tag, attrs, parent)` | crée un élément SVG |
| `text(attrs, parent, contenu)` | idem pour du texte |
| `animate(durée, fn, fin)` | **tween** : appelle `fn(p)` avec `p` de 0 à 1 |
| `stagger(items, pas, fn)` | **cascade** : la même action, décalée dans le temps |
| `fadeIn(node, délai, durée)` | **fondu** d'apparition |
| `draw(path, durée, délai)` | **tracé** : une ligne qui s'écrit |
| `countUp(node, de, à, durée, fmt)` | chiffre qui monte |
| `done()` | signale la fin de l'animation |

## Charte

Tout est dans le bloc `:root` de `index.html`. Changer les 8 couleurs et les
3 polices suffit à rebasculer le deck sur une autre identité.

L'unité `--u` vaut 1 % de la largeur de la scène : toutes les tailles en
dérivent, donc le deck s'adapte de l'écran de téléphone au vidéoprojecteur
sans une seule media query.
