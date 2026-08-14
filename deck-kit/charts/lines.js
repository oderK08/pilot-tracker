/* ============================================================
   MODULE GRAPHIQUE : COURBES QUI SE TRACENT
   Même contrat que waterfall : (svg, données, boîte à outils).
   Démontre la primitive de mouvement (D) : le tracé progressif.
   ============================================================ */
window.CHARTS = window.CHARTS || {};

window.CHARTS.lines = function (svg, d, ctx) {

  const L = 150, R = 1430, TOP = 275, BOT = 640;
  const X = i => L + i * (R - L) / (d.labels.length - 1);
  const Y = v => BOT - (v - d.ymin) / (d.ymax - d.ymin) * (BOT - TOP);

  // --- grille horizontale ---
  d.grid.forEach(v => {
    ctx.el("line", { x1: L - 18, y1: Y(v), x2: R, y2: Y(v),
      stroke: v === 0 ? "#252525" : "#CFCCC2",
      "stroke-width": v === 0 ? 2.5 : 1.5 }, svg);
    ctx.text({ x: L - 30, y: Y(v) + 7, "text-anchor": "end", "font-size": 19,
      fill: "#9CA9B0", "font-family": "IBM Plex Mono, monospace" }, svg, d.gfmt(v));
  });

  // --- étiquettes d'axe X ---
  d.labels.forEach((lab, i) => {
    if (d.tick && !d.tick(i)) return;
    ctx.text({ x: X(i), y: BOT + 36, "text-anchor": "middle", "font-size": 19,
      fill: "#778085" }, svg, lab);
  });

  // --- une courbe par série, tracées en cascade ---
  ctx.stagger(d.series, 450, (s, k) => {
    const pts = s.v.map((v, i) => `${X(i)},${Y(v)}`).join(" ");
    const path = ctx.el("polyline", { points: pts, fill: "none",
      stroke: s.c, "stroke-width": s.w || 3.5,
      "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);

    ctx.draw(path, 1300, 0);   // (D) la ligne s'écrit

    // le nom de la série apparaît au bout de la courbe, une fois tracée
    const last = s.v.length - 1;
    const lab = ctx.text({ x: X(last) + 14, y: Y(s.v[last]) + 6, "font-size": 21,
      "font-weight": 600, fill: s.c }, svg, s.n);
    ctx.fadeIn(lab, 1300, 500);

    if (k === d.series.length - 1) setTimeout(ctx.done, 2000);
  });
};
