/* ============================================================
   ENGINE — écrit une fois, jamais réécrit.
   Fournit : les helpers SVG, les primitives d'animation,
   et le système de scènes/navigation.
   ============================================================ */
const DECK = (function () {

  const NS = "http://www.w3.org/2000/svg";

  /* ---------- 1. HELPERS SVG ------------------------------------ */

  // Crée un élément SVG. C'est LE helper : tout le reste s'appuie dessus.
  // el("rect", {x:10, y:20, width:100, fill:"#730D1F"}, parent)
  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }

  // Idem pour du texte, avec le contenu en dernier argument.
  function text(attrs, parent, content) {
    const t = el("text", attrs, parent);
    t.textContent = content;
    return t;
  }


  /* ---------- 2. LES 4 PRIMITIVES DE MOUVEMENT ------------------ */

  // Courbe d'accélération : démarre vite, finit en douceur.
  // C'est ce qui fait qu'une animation paraît "naturelle" plutôt que mécanique.
  const ease = t => 1 - Math.pow(1 - t, 3);

  // (A) TWEEN — la primitive de base.
  // Appelle fn(p) ~60x/seconde avec p qui va de 0 à 1 sur `dur` millisecondes.
  // Tout le mouvement du deck sort de cette fonction.
  function animate(dur, fn, done) {
    const t0 = performance.now();
    (function step(now) {
      const p = Math.min(1, (now - t0) / dur);
      fn(ease(p));
      if (p < 1) requestAnimationFrame(step);
      else if (done) done();
    })(performance.now());
  }

  // (B) CASCADE — lance la même action sur N éléments, décalée de `step` ms.
  // C'est ce qui donne le rythme : les barres arrivent une par une.
  function stagger(items, step, fn, delay0) {
    items.forEach((item, i) => setTimeout(() => fn(item, i), (delay0 || 0) + i * step));
  }

  // (C) FONDU — fait apparaître un élément déjà dessiné.
  function fadeIn(node, delay, dur) {
    node.style.opacity = 0;
    node.style.transition = `opacity ${(dur || 700)}ms ease`;
    setTimeout(() => { node.style.opacity = 1; }, delay || 0);
  }

  // (D) TRACÉ — fait "s'écrire" une ligne de gauche à droite.
  // Astuce : on met des pointillés aussi longs que la ligne, puis on
  // fait glisser le décalage jusqu'à zéro.
  function draw(path, dur, delay) {
    const len = path.getTotalLength();
    path.setAttribute("stroke-dasharray", len);
    path.setAttribute("stroke-dashoffset", len);
    setTimeout(() => animate(dur, p =>
      path.setAttribute("stroke-dashoffset", len * (1 - p))), delay || 0);
  }

  // Compteur de chiffres qui monte. Sucre par-dessus animate().
  function countUp(node, from, to, dur, fmt, delay) {
    setTimeout(() => animate(dur, p =>
      node.textContent = fmt(from + (to - from) * p)), delay || 0);
  }


  /* ---------- 3. SYSTÈME DE SCÈNES ------------------------------ */

  const slides = [];
  let cur = 0;

  // Enregistre une slide. Appelé par chaque fichier de slides/.
  function slide(def) { slides.push(def); }

  // Construit le DOM d'une slide à partir de sa déclaration.
  // Le titre, le filet, le kicker, le punch et les sources sont
  // générés automatiquement — une slide n'a donc que ses données à fournir.
  function buildDOM(def, index) {
    const stage = document.getElementById("stage");
    const sc = document.createElement("div");
    sc.className = "scene";
    sc.id = "scene-" + index;
    sc.innerHTML =
      `<div class="s-head">
         <h1>${def.title}</h1>
         <div class="rule"></div>
         <div class="kicker">${def.kicker || ""}</div>
       </div>
       <div class="punch" id="punch-${index}">${def.punch || ""}</div>
       <div class="srcline">${def.source || ""}</div>`;
    const svg = el("svg", { viewBox: "0 0 1600 900", preserveAspectRatio: "xMidYMid meet" });
    sc.insertBefore(svg, sc.firstChild);
    stage.appendChild(sc);
    def._svg = svg;
    def._punch = sc.querySelector(".punch");
    return sc;
  }

  // (Re)joue une slide : on vide le SVG et on relance son rendu.
  // C'est ce qui permet de rejouer l'animation avec la touche R.
  function play(index) {
    const def = slides[index];
    def._svg.innerHTML = "";
    def._punch.classList.remove("show");
    // ctx = la boîte à outils passée au graphique
    def.chart(def._svg, def.data, {
      el, text, animate, stagger, fadeIn, draw, countUp,
      // appelé par le graphique quand son animation est finie
      done: () => def._punch.classList.add("show")
    });
  }

  function go(i) {
    cur = (i + slides.length) % slides.length;
    slides.forEach((s, j) =>
      document.getElementById("scene-" + j).classList.toggle("on", j === cur));
    document.querySelectorAll("#dots span")
      .forEach((d, j) => d.classList.toggle("on", j === cur));
    document.getElementById("counter").textContent = (cur + 1) + " / " + slides.length;
    play(cur);
  }

  function start() {
    slides.forEach(buildDOM);
    const dots = document.getElementById("dots");
    slides.forEach((s, i) => {
      const d = document.createElement("span");
      d.onclick = e => { e.stopPropagation(); go(i); };
      dots.appendChild(d);
    });

    addEventListener("keydown", e => {
      if (e.key === "ArrowRight" || e.key === " ") { e.preventDefault(); go(cur + 1); }
      if (e.key === "ArrowLeft") { e.preventDefault(); go(cur - 1); }
      if (e.key === "r" || e.key === "R") play(cur);   // rejouer l'animation
    });
    document.getElementById("nextB").onclick = e => { e.stopPropagation(); go(cur + 1); };
    document.getElementById("prevB").onclick = e => { e.stopPropagation(); go(cur - 1); };
    document.getElementById("stage").addEventListener("click", () => go(cur + 1));

    let sx = null;
    addEventListener("touchstart", e => sx = e.touches[0].clientX, { passive: true });
    addEventListener("touchend", e => {
      if (sx === null) return;
      const dx = e.changedTouches[0].clientX - sx; sx = null;
      if (Math.abs(dx) > 55) go(cur + (dx < 0 ? 1 : -1));
    }, { passive: true });

    go(0);
  }

  return { slide, start, el, text, animate, stagger, fadeIn, draw, countUp };
})();
