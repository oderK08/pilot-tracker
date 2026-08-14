/* ============================================================
   SLIDE 01 — QUE DES DONNÉES.
   Zéro code de dessin : le graphique vient du module CHARTS.waterfall.
   C'est ce fichier que tu dupliques pour créer une nouvelle slide.
   ============================================================ */
DECK.slide({
  title:  "L'IA porte<br>le marché américain",
  kicker: "S&P 500 · contribution à la performance YTD · points de %",
  punch:  "Cinq segments expliquent <b>78 % de la performance</b> de l'indice. " +
          "Le reste de la cote — 454 valeurs — pèse 1,9 point.",
  source: "Sources : FMP, Sismo · données illustratives",

  chart: CHARTS.waterfall,

  data: {
    ymin: -0.2, ymax: 10.4,
    grid: [0, 2, 4, 6, 8, 10],
    gfmt: v => v + " pp",                                    // format de l'axe
    vfmt: v => (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(1),// format des valeurs
    bars: [
      { n: "SEMIS &|MÉMOIRE",    v:  5.9 },
      { n: "MATÉRIEL|& STOCKAGE", v:  1.4 },
      { n: "DATA-|CENTERS",       v:  0.8 },
      { n: "HYPER-|SCALERS",      v: -0.5 },
      { n: "ÉNERGIE|& LOGICIEL",  v: -0.3 },
      { n: "COMPLEXE IA",         v:  7.4, sub: true, note: "78 % de la performance" },
      { n: "TOUT|LE RESTE",       v:  1.9, rest: true },
      { n: "S&P 500",             v:  9.3, total: true }
    ]
  }
});
