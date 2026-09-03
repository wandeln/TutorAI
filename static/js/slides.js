/*
 * Folien: Client-Spiegel von services/slides_service.py.
 *
 * - parseSlides(content)   → lenientes Parsen (Folientrenner `---` und
 *                            Unterfolien-Trenner `--` fence-aware, Direktiven,
 *                            Spaltentrenner `||`). Ein Block mit ≥2 `--`-Seg-
 *                            menten wird zu einem Stack: leere Eltern-Folie mit
 *                            `children` (vertikale Unterfolien für Reveal).
 *                            Ungültige Direktiven bleiben als Text stehen (der
 *                            Server meldet bei Speichern die genaue Meldung).
 * - countSlides(slides)    → Anzahl anzeigbarer (Blatt-)Folien: jeder Stack
 *                            zählt seine Unterfolien (wie Reveal's Zähler).
 * - renderSlideInto(slide, el) → rendert eine Folie (Markdown via
 *                            renderMarkdown, bleibt in .markdown-preview).
 * - renderSlideThumb(slide, thumbEl, themeClass) → Kachel-Vorschau:
 *                            Folie in 960×(960/Ratio) rendern und per
 *                            transform auf die Kachelbreite skalieren.
 *                            (Ratio = --slides-aspect des Themes.)
 *                            — nicht mehr in Verwendung (Kacheln und
 *                            Design-Vorschau nutzen echte Reveal-Instanzen).
 * - buildSlideSection(slide, footerText) → <section> für Reveal
 *                            (Inhalt + Sprechernotiz als <aside class="notes");
 *                            Standard-Transition ist "autoanimate"
 *                            (data-auto-animate); "transition: fade|slide|
 *                            zoom|none" setzt eine klassische Transition.
 *                            Gezoomte Applets ({zoom=X}) überspringt das
 *                            Auto-Animate per autoAnimateMatcher-Config
 *                            (siehe tutoraiAutoAnimateMatcher) — der
 *                            transform-Zoom bleibt erhalten.
 */

const SLIDE_LAYOUTS = new Set(["center", "topleft", "twocol"]);
const SLIDE_TRANSITIONS = new Set(["fade", "slide", "zoom", "none", "autoanimate"]);
const SLIDE_DEFAULT_TRANSITION = "autoanimate";

const SLIDE_FENCE_OPEN = /^ {0,3}(`{3,}|~{3,})/;
const SLIDE_DIRECTIVE = /^(layout|transition|class|notes|background):\s*(\S.*)$/;
const SLIDE_CLASS = /^[A-Za-z0-9_-]+$/;
// background: Markdown-Bild-/Applet-Snippet ![Titel](/media/…) ohne Zusätze
// (kein Label/Attribute) — der Pfad darf kein Whitespace enthalten.
const SLIDE_BG_IMAGE = /^!\[([^\]]*)\]\(([^)\s]+)\)$/;
// ?print-pdf-Modus: dort liegt das Folien-Bg als Element in der Folie (pro
// PDF-Seite, interaktiv vor dem Druck) — Reveal's natives Bg-System
// (data-background-*) ist im Print-Modus ausgeblendet und funktioniert
// nicht pro Seite.
const SLIDES_IS_PRINT_PDF = /print-pdf/.test(window.location.search);

/**
 * Teilt den Inhalt an Zeilen mit exakt `---` (Folientrenner) und exakt `--`
 * (Unterfolien-Trenner, nur innerhalb einer Folie) — beides fence-aware.
 * Liefert eine Liste von Folien-Blöcken; jeder Block ist eine Liste von
 * Segmente-Strings (1 Segment = normale Folie, ≥2 = vertikal gestapelte
 * Unterfolien). Exakter Zeilenabgleich (trim === "--"/"---"), damit `--`
 * nie die ersten beiden Zeichen von `---` matcht.
 */
function _splitSlideBlocks(content) {
  const blocks = [];
  let segments = [];
  let current = [];
  let fenceChar = "";
  let fenceLen = 0;

  // Invariante: `segments` = abgeschlossene Segmente des aktuellen Blocks,
  // `current` = aktive (noch nicht in segments enthaltene) Zeilen.
  const newSegment = () => {
    segments.push(current);
    current = [];
  };
  const newBlock = () => {
    segments.push(current);
    blocks.push(segments);
    segments = [];
    current = [];
  };

  for (const line of (content || "").split(/\r?\n/)) {
    if (fenceChar) {
      current.push(line);
      const lead = line.length - line.trimStart().length;
      const rest = line.trim();
      if (
        lead <= 3 &&
        rest.length >= fenceLen &&
        rest.length > 0 &&
        new Set(rest).size === 1 &&
        rest[0] === fenceChar
      ) {
        fenceChar = "";
      }
    } else {
      const t = line.trim();
      if (t === "---") {
        newBlock();
        continue;
      }
      if (t === "--") {
        newSegment();
        continue;
      }
      const m = line.match(SLIDE_FENCE_OPEN);
      if (m) {
        fenceChar = m[1][0];
        fenceLen = m[1].length;
      }
      current.push(line);
    }
  }
  segments.push(current);
  blocks.push(segments);
  return blocks.map((segs) => segs.map((l) => l.join("\n")));
}

function _parseSlideBlock(block, index) {
  const slide = {
    layout: "topleft",
    transition: null,
    css_class: null,
    notes: null,
    background: null,
    columns: [""],
    children: [], // nur bei Stacks (`--`) befüllt
  };
  const lines = block.split("\n");
  const seen = new Set();
  let i = 0;
  while (i < lines.length && !lines[i].trim()) i++;

  while (i < lines.length) {
    const m = lines[i].match(SLIDE_DIRECTIVE);
    if (!m) break;
    const key = m[1];
    const value = m[2].trim();
    let consumed = false;
    if (!seen.has(key)) {
      if (key === "layout" && SLIDE_LAYOUTS.has(value)) {
        slide.layout = value;
        consumed = true;
      } else if (key === "transition" && SLIDE_TRANSITIONS.has(value)) {
        slide.transition = value;
        consumed = true;
      } else if (key === "class" && SLIDE_CLASS.test(value)) {
        slide.css_class = value;
        consumed = true;
      } else if (key === "background") {
        const bm = value.match(SLIDE_BG_IMAGE);
        if (bm) {
          slide.background = { alt: bm[1], src: bm[2] };
          consumed = true;
        }
      } else if (key === "notes") {
        slide.notes = value;
        consumed = true;
      }
    }
    if (!consumed) break; // ungültig/doppelt → als Text stehen lassen
    seen.add(key);
    i++;
  }

  const body = lines.slice(i);
  const firstSplit = body.findIndex((l) => l.trim() === "||");
  if (firstSplit !== -1 && slide.layout === "twocol") {
    const leftLines = body.slice(0, firstSplit);
    const right = body.slice(firstSplit + 1).join("\n").trim();
    // Überschrift auf der ersten nicht-leeren Zeile der linken Spalte →
    // eigener, vollbreiter Header über beiden Spalten (Spiegel von
    // _parse_block in slides_service.py; rendert renderSlideInto).
    let k = 0;
    while (k < leftLines.length && !leftLines[k].trim()) k++;
    if (k < leftLines.length && /^#{1,6}[ \t]\S/.test(leftLines[k].trim())) {
      slide.header = leftLines[k].trim();
      slide.columns = [leftLines.slice(k + 1).join("\n").trim(), right];
    } else {
      slide.columns = [leftLines.join("\n").trim(), right];
    }
  } else {
    slide.columns = [body.join("\n").trim()];
  }
  return slide;
}

function parseSlides(content) {
  if (!content || !content.trim()) return [];
  return _splitSlideBlocks(content).map((segs, i) =>
    segs.length === 1
      ? _parseSlideBlock(segs[0], i + 1)
      : _parseStackBlock(segs, i + 1)
  );
}

/** Block mit ≥2 `--`-Segmenten → Stack: reiner (leerer) Container-Eltern
 *  mit `children` — jede Unterfolie wird wie eine normale Folie geparsed
 *  (eigene Direktiven, Layout, Notiz, Hintergrund; Spiegel von parse_slides
 *  in slides_service.py). */
function _parseStackBlock(segments, index) {
  const stack = _parseSlideBlock("", index);
  stack.children = segments.map((seg) => _parseSlideBlock(seg, index));
  return stack;
}

/** Anzahl anzeigbarer (Blatt-)Folien: normale Folie zählt 1, Stack zählt
 *  seine Unterfolien (stimmt mit Reveal's Folienzähler überein). */
function countSlides(slides) {
  return slides.reduce((n, s) => n + (s.children.length > 0 ? s.children.length : 1), 0);
}

/** Rendert eine Folie in ein Element (<section> oder .slides-canvas).
 *  slideMode: true → slide-eigene Gleichungs-Labels bekommen (S1), (S2), …
 *  und Skript-Labels verlinken zur Gleichung im Skript. */
async function renderSlideInto(slide, container) {
  container.innerHTML = "";
  const isTwocol = slide.layout === "twocol" && slide.columns.length === 2;
  if (isTwocol) {
    if (slide.header) {
      // Überschrift spannt über beide Spalten (eigener Block vor dem Grid).
      const headerEl = document.createElement("div");
      headerEl.className = "slides-twocol-header";
      await renderMarkdown(slide.header, headerEl, { slideMode: true });
      container.appendChild(headerEl);
    }
    const wrap = document.createElement("div");
    wrap.className = "slides-twocol";
    for (const colMd of slide.columns) {
      const colEl = document.createElement("div");
      colEl.className = "slides-col";
      wrap.appendChild(colEl);
      if (colMd) await renderMarkdown(colMd, colEl, { slideMode: true });
    }
    container.appendChild(wrap);
  } else {
    const md = slide.columns[0] || "";
    if (md) await renderMarkdown(md, container, { slideMode: true });
  }
}

/* Seitenverhältnis (Breite/Höhe) aus dem Theme — kommt als CSS-Variable
 * --slides-aspect von :root (bzw. inline bei der Design-Vorschau). */
function slideAspectRatio(el) {
  try {
    const v = getComputedStyle(el).getPropertyValue("--slides-aspect").trim();
    const n = parseFloat(v);
    if (isFinite(n) && n > 0) return n;
  } catch (e) { /* fall through */ }
  return 16 / 9;
}

/**
 * Kachel-Vorschau: erste Folie in 960×(960/Ratio) rendern, auf die
 * Kachelbreite skalieren. thumbEl = .slides-thumb (aspect-ratio aus Theme).
 */
async function renderSlideThumb(slide, thumbEl, themeClass) {
  thumbEl.innerHTML = "";
  const ratio = slideAspectRatio(thumbEl);
  const W = 960;
  const H = Math.round(W / ratio);
  const s = (thumbEl.clientWidth / W) || 0.3;

  const scaler = document.createElement("div");
  scaler.className = "slides-thumb-scaler";
  scaler.style.width = W + "px";
  scaler.style.height = H + "px";
  scaler.style.transform = `scale(${s})`;

  const canvas = document.createElement("div");
  canvas.className = "slides-canvas layout-" + slide.layout + (themeClass ? " theme-" + themeClass : "");
  scaler.appendChild(canvas);
  thumbEl.appendChild(scaler);

  await renderSlideInto(slide, canvas);
}

/**
 * Baut ein <section> für Reveal: Layout-Klasse, Transition, optionaler
 * Fußzeilen-Text (data-footer), gerenderte Folie + Sprechernotiz.
 *
 * Die Notiz wird als <aside class="notes"> gerendert (Nicht das
 * data-notes-Attribut): das lokale Reveal-NOTES-Plugin liest in seiner
 * sendState()-Funktion ausschließlich die aside.notes-Elemente — das
 * Attribut würde dort durch den (leeren) aside.notes-Branch überschrieben
 * und in der Speaker-View nie angezeigt.
 */
async function buildSlideSection(slide, footerText) {
  // Vertikaler Stack (`--`-Unterfolien): Reveal-Nest <section>
  // <section>…</section>…</section>. Der Eltern-Section ist ein reiner
  // (inhaltloser) Container ohne Layout-Klasse — Reveal erkennt am
  // <section>-Kind automatisch den Stack; jede Unterfolie wird wie eine
  // normale Folie gebaut (eigene Transition/Notiz/Hintergrund).
  if (slide.children && slide.children.length > 0) {
    const stack = document.createElement("section");
    stack.className = "slides-stack";
    for (const child of slide.children) {
      stack.appendChild(await buildSlideSection(child, footerText));
    }
    return stack;
  }
  const section = document.createElement("section");
  section.className = "layout-" + slide.layout + (slide.css_class ? " " + slide.css_class : "");
  // Standard-Transition: Auto-Animate (Inhalte animieren zwischen den
  // Folien ineinander). "transition: fade|slide|zoom|none" setzt eine
  // klassische Transition (und damit kein data-auto-animate). Gezoomte
  // Applets ({zoom=X}) überspringt das Auto-Animate per
  // autoAnimateMatcher-Config (siehe tutoraiAutoAnimateMatcher) — sie
  // faden ein/aus, der Inline-Transform-Zoom bleibt erhalten.
  const transition = slide.transition || SLIDE_DEFAULT_TRANSITION;
  if (footerText) section.setAttribute("data-footer", footerText);
  await renderSlideInto(slide, section);
  // Folien-Hintergrund (background: ![Titel](…)):
  // - Präsentation/Vorschau: Reveal's natives Hintergrund-System
  //   (data-background-image / data-background-iframe) → deckt den ganzen
  //   Viewport ab, auch die Letterbox, in die Reveal .slides skaliert.
  //   .html-Applets UND externe Websites bleiben per
  //   data-background-interactive klickbar (Reveal-Design: dann ist der
  //   Folieninhalt dieser Folie nicht klickbar).
  // - ?print-pdf: Element IN der Folie (pro PDF-Seite; Applet ist
  //   interaktiv, damit Einstellungen vor dem Druck angepasst werden
  //   können) — Reveal's Bg-System ist im Print-Modus ausgeblendet.
  //   NUR NACH renderSlideInto bauen — die setzt innerHTML und würde das
  //   Element sonst löschen.
  if (slide.background) {
    // .html-Applet oder externe Website (http(s)-URL ohne Bild-Endung)
    // → Iframe; Erkennung/Sandbox-Regeln wie in markdown-renderer.js.
    const isApplet = isAppletSrc(slide.background.src);
    if (SLIDES_IS_PRINT_PDF) {
      const bg = document.createElement(isApplet ? "iframe" : "img");
      bg.className = "tutorai-slide-bg";
      bg.src = slide.background.src;
      if (isApplet) {
        bg.setAttribute("sandbox", appletSandboxAttr(slide.background.src));
        bg.title = slide.background.alt;
        bg.loading = "lazy";
      } else {
        bg.alt = slide.background.alt;
      }
      section.classList.add("slides-has-bg");
      section.appendChild(bg);
    } else if (isApplet) {
      section.setAttribute("data-background-iframe", slide.background.src);
      section.setAttribute("data-background-interactive", "");
    } else {
      section.setAttribute("data-background-image", slide.background.src);
    }
  }
  if (transition === "autoanimate") {
    section.setAttribute("data-auto-animate", "");
  } else {
    section.setAttribute("data-transition", transition);
  }
  if (slide.notes) {
    const aside = document.createElement("aside");
    aside.className = "notes";
    aside.textContent = slide.notes; // plain text → kein HTML-Injection-Risiko
    section.appendChild(aside);
  }
  return section;
}

/**
 * Reveal-Config `autoAnimateMatcher`: Auto-Animate-Paare filtern und
 * gezoomte Applets (data-zoom) überspringen — sonst würde AutoAnimate den
 * Inline-Transform (scale) des Iframes animieren/zerstören. Ausgefilterte
 * Elemente werden von Reveal als nicht passende behandelt (ein-/ausfaden),
 * ihr transform-Zoom bleibt unangetastet.
 *
 * Wichtig: `this` ist die AutoAnimate-Instanz (Reveal ruft den Matcher per
 * matcher.call(this, fromSlide, toSlide) auf) → getAutoAnimatePairs steht
 * hier als Instanz-Methode zur Verfügung.
 */
function tutoraiAutoAnimateMatcher(fromSlide, toSlide) {
  const pairs = this.getAutoAnimatePairs(fromSlide, toSlide);
  return pairs.filter(
    (pair) => !pair.from.hasAttribute("data-zoom") && !pair.to.hasAttribute("data-zoom")
  );
}

/**
 * Fragment-Schritte neu ableiten, sobald eine Folie gelayoutet ist (s.
 * tutoraiResortFragments in markdown-renderer.js). Beim Markdown-Render
 * sind die Sections noch display:none (Reveal-CSS), daher kann die
 * visuelle Fragment-Reihenfolge (z. B. KaTeX-Underbraces) erst richtig
 * sortiert werden, wenn die Folie sichtbar ist — bzw. im Print-Modus,
 * noch bevor Reveal's setupPDF die Fragments auf PDF-Seiten paginiert.
 *
 * MÜSSE NACH `await reveal.initialize()` aufgerufen werden: Der Aufruf
 * läuft dann exakt zu dem Zeitpunkt, an dem Reveal's "ready" gerade
 * gefeuert hat — ein `.on("ready")`-Listener wäre zu spät registriert
 * (Reveal feuert es via setTimeout(1ms) aus start(); das Rennen mit
 * setupPDF's erstem rAF geht je nach Umgebung unterschiedlich aus, s. unten).
 *
 * @param {object} reveal initialisierte Reveal-Instanz
 * @param {HTMLElement} slidesEl Element, das die Sections enthält (.slides)
 */
function wireKatexFragmentResort(reveal, slidesEl) {
  const resort = (slide, resync) => {
    if (!slide || !slide.querySelector(".markdown-preview")) return;
    tutoraiResortFragments(slide);
    // Sichtbarkeit an die (ggf. neuen) Indizes anpassen: Ohne angezeigte
    // Fragments ist data-fragment=-1 (Ausgangszustand); bei einem Deep-Link
    // (#/2/1) zeigt der alte Index auf den zugehörigen visuellen Schritt.
    // Im Print-Modus resync=false — Reveal's setupPDF verwaltet den
    // Fragment-Visibility-Zustand pro PDF-Seite selbst (ein vorheriges
    // .visible würde in die per Fragment-State geklonten Seiten wandern).
    if (resync) {
      try {
        reveal.fragments.update(
          parseInt(slide.getAttribute("data-fragment") || "-1", 10)
        );
      } catch (e) { /* ignore */ }
    }
  };
  if (/print-pdf/.test(window.location.search)) {
    // Die Ableitung muss VOR setupPDF's Fragment-Paginierung laufen (die
    // erfolgt 2 Animation-Frame nach dem Setzen von html.print-pdf).
    // Zwei Timing-Varianten: Unter Headless kann setupPDF's erster rAF
    // VOR Reveal's 1ms-Ready-Timer kommen → die Klasse ist dann beim
    // Aufruf bereits gesetzt (→ jetzt synchron ableiten; die Sections
    // sind gelayoutet und die Rects gültig). Andernfalls fängt der
    // MutationObserver das Class-Set synchron (Microtask) ab — ebenfalls
    // noch vor der Paginierung.
    const doPrintResort = () => {
      slidesEl.querySelectorAll("section").forEach((s) => resort(s, false));
    };
    if (document.documentElement.classList.contains("print-pdf")) {
      doPrintResort();
    } else {
      const mo = new MutationObserver(() => {
        if (!document.documentElement.classList.contains("print-pdf")) return;
        mo.disconnect();
        doPrintResort();
      });
      mo.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ["class"],
      });
    }
  } else {
    // Präsentation/Vorschau: "ready" hat beim Aufruf bereits gefeuert
    // (initialize() löst genau dort auf) → direkt für die aktuelle Folie
    // ableiten; ein .on("ready")-Listener wäre zu spät registriert. Das
    // deckt auch Deep-Links (#/2/1) ab — das initiale slidechanged feuert
    // während start(), also VOR der Listener-Registrierung.
    resort(reveal.getCurrentSlide(), true);
    reveal.on("slidechanged", (e) => resort(e.currentSlide, true));
  }
}
