/* ============================================================
   MODULE GRAPHIQUE : CASCADE (waterfall)
   Écrit une fois. Réutilisable sur autant de slides que voulu :
   seules les données changent.
   ============================================================ */
window.CHARTS = window.CHARTS || {};

window.CHARTS.waterfall = function (svg, d, ctx) {

  // --- géométrie : on travaille toujours dans un repère 1600 x 900 ---
  const L = 150, R = 1520, TOP = 285, BOT = 645;
  const gap = 22, n = d.bars.length;
  const bw = (R - L - gap * (n - 1)) / n;
  const Y = v => BOT - (v - d.ymin) / (d.ymax - d.ymin) * (BOT - TOP);

  // --- grille + axe zéro ---
  d.grid.forEach(v => {
    if (v !== 0) ctx.el("line", { x1: L - 18, y1: Y(v), x2: R, y2: Y(v),
      stroke: "#CFCCC2", "stroke-width": 1.5 }, svg);
    ctx.text({ x: L - 30, y: Y(v) + 7, "text-anchor": "end", "font-size": 19,
      fill: "#9CA9B0", "font-family": "IBM Plex Mono, monospace" }, svg, d.gfmt(v));
  });
  ctx.el("line", { x1: L - 18, y1: Y(0), x2: R, y2: Y(0),
    stroke: "#252525", "stroke-width": 2.5 }, svg);

  // --- les barres, en cascade ---
  let cum = 0;
  const plan = d.bars.map(b => {
    const start = (b.total || b.sub) ? 0 : cum;
    const end = (b.total || b.sub) ? b.v : cum + b.v;
    if (!b.total && !b.sub) cum = end;
    return { b, start, end };
  });

  ctx.stagger(plan, 380, (p, i) => {
    const { b, start, end } = p;
    const x = L + i * (bw + gap);
    const color = b.total ? "#252525"
                : b.sub   ? "#730D1F"
                : b.rest  ? "#BFC9CF"
                : (b.v < 0 ? "#A42035" : "#343E42");

    const g = ctx.el("g", {}, svg);
    const rect = ctx.el("rect", { x, width: bw, y: Y(Math.max(start, end)),
      height: 0, fill: color }, g);
    const label = ctx.text({ x: x + bw / 2, y: 0, "text-anchor": "middle",
      "font-size": 23, "font-weight": 600, fill: b.rest ? "#778085" : color,
      "font-family": "IBM Plex Mono, monospace", opacity: 0 }, g, "");

    // (A) la barre pousse + (B) le chiffre monte, sur le même tween
    ctx.animate(520, t => {
      const v = start + (end - start) * t;
      rect.setAttribute("y", Y(Math.max(start, v)));
      rect.setAttribute("height", Math.max(2, Math.abs(Y(start) - Y(v))));
      label.setAttribute("y", Y(Math.max(start, v)) - 14);
      label.setAttribute("opacity", t);
      label.textContent = d.vfmt(b.total ? v : (end - start) * t);
    }, () => {
      label.textContent = d.vfmt(b.total ? end : end - start);

      // (C) le nom sous la barre, en fondu
      b.n.split("|").forEach((line, k) => {
        const t = ctx.text({ x: x + bw / 2, y: BOT + 36 + k * 26,
          "text-anchor": "middle", "font-size": 20,
          "font-weight": (b.sub || b.total) ? 600 : 400, fill: "#252525" }, svg, line);
        ctx.fadeIn(t, 0, 400);
      });

      // pointillé de liaison vers la barre suivante
      if (i < plan.length - 1) {
        const yLink = Y(b.sub ? cum : end);
        ctx.el("line", { x1: x + bw, y1: yLink, x2: x + bw + gap, y2: yLink,
          stroke: "#9CA9B0", "stroke-width": 2, "stroke-dasharray": "5 5" }, svg);
      }

      // annotation optionnelle (rouge, au-dessus)
      if (b.note) {
        const t = ctx.text({ x: x + bw / 2, y: Y(Math.max(start, end)) - 52,
          "text-anchor": "middle", "font-size": 21, "font-weight": 600,
          fill: "#730D1F" }, svg, b.note);
        ctx.fadeIn(t, 0, 500);
      }
      if (i === plan.length - 1) setTimeout(ctx.done, 500);
    });
  });
};
