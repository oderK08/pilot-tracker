/* ============================================================
   SLIDE 02 — même structure, autre module graphique.
   Le titre, le filet, le kicker, le punch et les sources sont
   générés par le moteur : rien à remettre en page.
   ============================================================ */
DECK.slide({
  title:  "Trente ans pour 150 mds,<br>six ans pour mille",
  kicker: "Hyperscalers · capex par exercice · mds $",
  punch:  "La pente change de nature à partir de 2023 : ce n'est plus " +
          "une croissance, c'est <b>un changement de régime</b>.",
  source: "Sources : documents d'entreprise, consensus · données illustratives",

  chart: CHARTS.lines,

  data: {
    ymin: 0, ymax: 300,
    grid: [0, 100, 200, 300],
    gfmt: v => "$" + v,
    labels: ["2016","2017","2018","2019","2020","2021","2022","2023","2024","2025","2026e"],
    tick: i => i % 2 === 0,          // n'afficher qu'une étiquette sur deux
    series: [
      { n: "Microsoft", c: "#252525", w: 4,
        v: [ 9, 12, 14, 14, 18, 24, 31, 45, 76, 120, 175] },
      { n: "Amazon",    c: "#730D1F", w: 4,
        v: [11, 12, 13, 17, 40, 61, 64, 49, 78, 118, 200] },
      { n: "Alphabet",  c: "#778085", w: 3.5,
        v: [10, 13, 25, 23, 22, 25, 31, 32, 52,  85, 145] },
      { n: "Meta",      c: "#9CA9B0", w: 3.5,
        v: [ 5,  7, 14, 15, 15, 19, 32, 28, 37,  68, 115] }
    ]
  }
});
