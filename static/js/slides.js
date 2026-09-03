/*
 * Folien: Client-Spiegel von services/slides_service.py.
 *
 * - parseSlides(content)   → lenientes Parsen (Folientrenner `---` fence-aware,
 *                            Direktiven, Spaltentrenner `||`). Ungültige
 *                            Direktiven bleiben als Text stehen (der Server
 *                            meldet bei Speichern die genaue Fehlermeldung).
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

function _splitSlideBlocks(content) {
  const blocks = [];
  let current = [];
  let fenceChar = "";
  let fenceLen = 0;

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
      if (line.trim() === "---") {
        blocks.push(current.join("\n"));
        current = [];
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
  blocks.push(current.join("\n"));
  return blocks;
}

function _parseSlideBlock(block, index) {
  const slide = {
    layout: "topleft",
    transition: null,
    css_class: null,
    notes: null,
    background: null,
    columns: [""],
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
  return _splitSlideBlocks(content).map((b, i) => _parseSlideBlock(b, i + 1));
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
  //   .html-Applets bleiben per data-background-interactive klickbar
  //   (Reveal-Design: dann ist der Folieninhalt dieser Folie nicht
  //   klickbar).
  // - ?print-pdf: Element IN der Folie (pro PDF-Seite; Applet ist
  //   interaktiv, damit Einstellungen vor dem Druck angepasst werden
  //   können) — Reveal's Bg-System ist im Print-Modus ausgeblendet.
  //   NUR NACH renderSlideInto bauen — die setzt innerHTML und würde das
  //   Element sonst löschen.
  if (slide.background) {
    const isApplet = /\.html?$/i.test(slide.background.src);
    if (SLIDES_IS_PRINT_PDF) {
      const bg = document.createElement(isApplet ? "iframe" : "img");
      bg.className = "tutorai-slide-bg";
      bg.src = slide.background.src;
      if (isApplet) {
        bg.setAttribute("sandbox", "allow-scripts");
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
