/**
 * Markdown + LaTeX + Mermaid Renderer für TutorAI.
 *
 * Verwendet:
 * - marked.js (MIT) für Markdown-Parser
 * - KaTeX (MIT) für LaTeX-Rendering
 * - Mermaid.js (MIT) für Diagramme (flowchart, sequence, class, state, etc.)
 * - highlight.js (BSD-3) für Python-Syntax-Highlighting
 *
 * Inline-Latex:  $...$                            → Inline
 * Display-Latex: $$...$$                          → Block
 * Mermaid:       ```mermaid                       → SVG-Diagramm
 * Escaped dollar: \$                              → literal $ (no LaTeX)
 * Nummerierte Figur:  ![caption](src){#fig:label} → "Abb. N: caption" (Anker fig:label)
 * Nummerierte Formel: $$...$$ {#eq:label}         → "(N)" neben der Formel (Anker eq:label)
 * Nummerierte Section: ## Titel {#sec:label}      → "K.N[.M]" vor der Überschrift (h2–h4,
 *                                                            kapitellokal; Kapitelnummer aus dem refmap);
 *                                                            Anker sec:label bzw. sec:{sectionId}-{num}
 * Querverweise:       @fig:label / @eq:label / @sec:label
 *                                                            → klickbares "Abb. N" / "Gl. N" / "Abs. N.M"
 *                                                            (In-Page- oder Kapitel-übergreifender Link, ❓ wenn unbekannt)
 *                    @kap:label = Legacy-Alias für @sec:label. Kapitel-Label = {#sec:label}
 *                    als EIGENE ZEILE (erste nicht-leere Zeile) am Kapitelanfang → "Kap. N"
 *                    (verlinkt auf #chapter-{id}); die Label-Zeile wird selbst nicht gerendert
 * Aufgaben-Box:       @task:{id}                  → Aufgaben-Box (Student: Punkte/Medaille analog
 *                                                            Aufgabenübersicht, PROF/TUTOR: kompakt; ❓ wenn unbekannt)
 * Hinweis-Boxen:      @box:{typ} … @endbox        → farbig markierte Box (merksatz/hinweis/bemerkung/
 *                                                            warnung/beispiel; unbekannte Typen = neutrale Box)
 *                                                            highlight: Box OHNE Kopf (transparent + Blur,
 *                                                            Primärfarbe), z.B. für Titel auf Deckslides;
 *                                                            @boxcolor:<farbe> als ERSTE Zeile übersteuert
 *                                                            die Boxfarbe (#hex/rgb()/CSS-Farbname)
 *                                                            code: dunkle Code-Box mit 💻-Kopf
 *                                                            (Inhalt = fenced Code-Block)
 * Code-Blöcke:        ```<sprache> … ```           → Syntax-Highlighting (hljs, alle Sprachen;
 *                                                            ohne Sprache = Auto-Detection)
 *                                                            Öffnende Zeile: Sprache + optionale Tokens in
 *                                                            BELIEBIGER Reihenfolge (parseFenceHead):
 *                                                            {#lines:1,3-5} → (Slides) Zeilennummern +
 *                                                            Zeilen-Highlight, "|" = weiterer Schritt
 *                                                            (Per-Line-Reveal, Reveal-Highlight-Plugin)
 *                                                            {#aaid:label} → (Slides) Auto-Animate-ID auf dem
 *                                                            <pre> (data-id): derselbe Label auf der nächsten
 *                                                            Folie → native Zeilen-Animation
 *                                                            {.zoom=X} → Schriftgröße ×X, {.height=Y} →
 *                                                            Max-Höhe Y px + internes Scrollen (beides:
 *                                                            Slides UND Skript; führender Punkt optional,
 *                                                            wie bei Applets; Inline-Styles am <pre>)
 *                                                            (unbekanntes Token → 1. Zeile bleibt Code-Text)
 * AutoAnimate-IDs:    {#aaid:label}                → (nur Slides) explizites Auto-Animate-Element-
 *                                                            Matching: Elemente mit demselben Label auf
 *                                                            zwei Folien animieren per ID ineinander
 *                                                            (Reveal 4.6: data-id; in Reveal 5 heißt es
 *                                                            data-auto-animate-id). Am Zeilenende (nach
 *                                                            Bullet/Absatz/Formel/Figur) oder als Token
 *                                                            nach $$…$$ {#eq:…} bzw. Bild-Snippets.
 * LaTeX-Fragmente:    \fragment{…}                 → (Slides) der eingewickelte Teil der Formel
 *                                                            (Kurzform von \htmlClass{fragment}{…})
 *                                                            erscheint mit einem extra Klick;
 *                                                            \fragment{id}{…} = ID-Gruppe
 *                                                            (gleichzeitig, = \htmlClass{fragment:id}{…}).
 *                                                            Andere Trust-Kommandos
 *                                                            (\href, \includegraphics, …) bleiben
 *                                                            abgelehnt (Security).
 * Applet-Abbildung:   ![caption](src.html)         → interaktives (sandboxed) Iframe;
 *                    ![caption](https://…)          → dito für externe Websites
 *                                                            (http(s)-URL ohne Bild-Endung);
 *                                                            mit {#fig:label} nummeriert wie Bilder
 * Labels dürfen (Unicode-)Buchstaben enthalten, z.B. Umlaute: {#fig:verteilung_überblick}
 *
 * Code blocks (```...``` and `...`) are protected from LaTeX extraction.
 *
 * usage:
 *   await renderMarkdown(text, element)
 *   await renderMarkdown(text, element, { preview: true })  // Editor-Preview
 */

// Initialize Mermaid on first load
if (typeof mermaid !== 'undefined') {
  mermaid.initialize({
    startOnLoad: false,
    theme: 'default',
    securityLevel: 'loose',
  });
}

// ─── Kapitel-übergreifende Referenz-Map ─────────────────────────────────────
// GET /api/courses/{courseId}/script-refmap: live-berechnete globale
// Nummerierung aller fig:/eq:-Labels des Skripts (ohne DB). Wird einmal
// pro Seite geholt und gecacht. Das globale `courseId` wird auf Kurs-Seiten
// definiert (course/base.html bzw. direkt in task_detail/task_solve);
// fehlt es → null.
let _refMapPromise = null;
function _getCourseId() {
  try {
    const cid = (typeof courseId !== 'undefined') ? courseId : null;
    return (cid === null || cid === undefined) ? null : cid;
  } catch (e) {
    return null; // TDZ: courseId wird auf der Seite deklariert, aber noch nicht initialisiert
  }
}

function getCourseRefMap() {
  const cid = _getCourseId();
  if (cid === null) {
    return Promise.resolve(null); // bewusst ohne Caching → nächster Render versucht es erneut
  }
  if (!_refMapPromise) {
    _refMapPromise = fetch(`/api/courses/${cid}/script-refmap`, { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null);
  }
  return _refMapPromise;
}

// GeCachten Refmap verwerfen (nach Kapitel-Änderungen: speichern/löschen/
// verschieben) und neu laden — Nummern/TOC ändern sich sonst erst beim Reload.
function refreshCourseRefMap() {
  _refMapPromise = null;
  return getCourseRefMap();
}

function _chapterRef(refMap, sectionId) {
  if (!refMap || !refMap.chapters || sectionId === null || sectionId === undefined) return null;
  return refMap.chapters[String(sectionId)] || null;
}

// ─── Hinweis-/Merksatz-Boxen: @box:{typ} … @endbox ─────────────────────────
// Bekannte Typen mit Icon & Überschrift. Unbekannte Typen werden als neutrale
// Box mit dem rohen Typen als Titel gerendert (Inhalt geht nicht verloren).
const CALLOUT_TYPES = {
  merksatz: { icon: '📌', title: 'Merksatz' },
  hinweis: { icon: '💡', title: 'Hinweis' },
  bemerkung: { icon: 'ℹ️', title: 'Nebenbemerkung' },
  warnung: { icon: '⚠️', title: 'Warnung' },
  beispiel: { icon: '📎', title: 'Beispiel' },
  code: { icon: '💻', title: 'Code' },
};

// ─── Highlight-Box: @boxcolor:<farbe> ───────────────────────────────────
// @box:highlight ist die headless Variante der Hinweis-Boxen (kein
// Icon/Überschrift-Kopf, Styling in slides.css). Als ERSTE Zeile des
// Boxinhalts darf @boxcolor:<farbe> die Boxfarbe übersteuern
// (#rgb/#rrggbb, rgb()/rgba(), klassische CSS-Farbnamen). Der Renderer
// emittiert eine normalisierte rgba()-Zeile als Inline-Style (überlebt
// DOMPurify, s. Applet-Styling); ungültige Werte → CSS-Default
// (Primärfarbe mit HIGHLIGHT_BOX_ALPHA).
const HIGHLIGHT_BOX_ALPHA = 0.3;
const BOX_COLOR_NAMES = {
  white: [255, 255, 255], black: [0, 0, 0], red: [220, 38, 38], green: [22, 163, 74],
  blue: [37, 99, 235], yellow: [234, 179, 8], orange: [249, 115, 22], purple: [147, 51, 234],
  pink: [236, 72, 153], brown: [146, 64, 14], gray: [107, 114, 128], grey: [107, 114, 128],
  cyan: [6, 182, 212], magenta: [233, 30, 99], lime: [132, 204, 22], teal: [13, 148, 136],
  navy: [30, 58, 138], maroon: [127, 29, 29], olive: [113, 99, 41],
};

function _boxColorToRgba(value) {
  // → normalisierte "rgba(r, g, b, a)"-Zeile oder null (→ CSS-Default).
  // Nur regulargültige Werte werden akzeptiert → kein Weg, beliebigen
  // CSS-Text in den Inline-Style zu schmuggeln.
  value = String(value).trim().replace(/^["']+|["']+$/g, '');
  if (!value) return null;

  let m = /^#([\da-f]{3}|[\da-f]{6})$/i.exec(value);
  if (m) {
    let h = value.slice(1);
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    return 'rgba(' + parseInt(h.slice(0, 2), 16) + ', ' + parseInt(h.slice(2, 4), 16) +
      ', ' + parseInt(h.slice(4, 6), 16) + ', ' + HIGHLIGHT_BOX_ALPHA + ')';
  }

  m = /^rgba?\(\s*(0|[1-9]\d{0,2})\s*,\s*(0|[1-9]\d{0,2})\s*,\s*(0|[1-9]\d{0,2})\s*(?:,\s*([0-9]*\.?[0-9]+))?\s*\)$/i.exec(value);
  if (m) {
    let alpha = HIGHLIGHT_BOX_ALPHA;
    if (m[4] !== undefined) {
      alpha = parseFloat(m[4]);
      if (alpha < 0 || alpha > 1) return null;
    }
    return 'rgba(' + m[1] + ', ' + m[2] + ', ' + m[3] + ', ' + alpha + ')';
  }

  const rgb = BOX_COLOR_NAMES[value.toLowerCase()];
  if (rgb) return 'rgba(' + rgb[0] + ', ' + rgb[1] + ', ' + rgb[2] + ', ' + HIGHLIGHT_BOX_ALPHA + ')';
  return null;
}

// ─── Applet-/Website-Erkennung ──────────────────────────────────────────
// Ein „Applet“ ist ein .html-Medium (eigene /media-Applets) ODER eine
// externe Website (http(s)-URL ohne Bild-Endung) — beide werden als
// Iframe gerendert. Die Sandbox unterscheidet sich:
// - eigene Applets: allow-scripts OHNE allow-same-origin — sie laufen
//   unter eigener (App-)Origin, mit same-origin könnten kompromittierte
//   Applets via window.parent auf das Parent-DOM zugreifen;
// - externe Websites: + allow-same-origin — ohne Storage/Subresource-
//   Zugriffe läuft kaum eine reale Site; sie haben eine fremde Origin
//   und erreichen das Parent-DOM daher nicht.
const APPLET_IMAGE_EXT = /\.(?:png|jpe?g|gif|webp|svg|bmp|avif|ico)(?:[?#].*)?$/i;
function isAppletSrc(src) {
  return /\.html?$/i.test(src) ||
    (/^https?:\/\//i.test(src) && !APPLET_IMAGE_EXT.test(src));
}
function appletSandboxAttr(src) {
  if (!/^https?:\/\//i.test(src)) return "allow-scripts";
  // YouTube-Player: der „Zu YouTube“-Link braucht allow-popups. Vollbild ist
  // per Embed-Param fs=0 deaktiviert (Button versteckt), weil fullscreen aus
  // dem cross-origin-Frame trotz Permissions-Policy-Delegation in Chrome
  // blockiert wird → kein allow-fullscreen mehr nötig.
  if (/youtube(?:-nocookie)?\.com/i.test(src)) {
    return "allow-scripts allow-same-origin allow-popups";
  }
  return "allow-scripts allow-same-origin";
}

// YouTube-URL (watch?v=…, youtu.be/…, shorts/…, embed/…; youtube.com und
// youtube-nocookie.com) → Embed-URL des Players. Damit werden YouTube-
// Links wie jedes andere Medium eingebunden (Iframe-Player statt der
// ganzen Watch-Seite). Nicht-YouTube-URLs → null.
function youtubeEmbedSrc(src) {
  let m;
  if ((m = /^https?:\/\/(?:www\.|m\.)?youtube(?:-nocookie)?\.com\/(?:watch\?(?:[^#]*&)?v=|embed\/|shorts\/)([A-Za-z0-9_-]+)(?:[?&#]|$)/i.exec(src)) ||
      (m = /^https?:\/\/(?:www\.)?youtu\.be\/([A-Za-z0-9_-]+)(?:[?&#]|$)/i.exec(src))) {
    // fs=0: Vollbild-Button im Player verstecken — fullscreen aus dem
    // cross-origin-Frame ist ohnehin nicht nutzbar (s. appletSandboxAttr).
    return `https://www.youtube.com/embed/${m[1]}?fs=0`;
  }
  return null;
}

// ─── Applet-Auto-Sizing ───────────────────────────────────────────────
// Eigene Applets (.html-Medien) melden ihre Inhaltshöhe per postMessage — das
// Boilerplate wird serverseitig in jeden Applet-Versand injiziert (siehe
// serve_media in main.py). Das Iframe passt sich an (150–600 px);
// darüber hinaus scrollt das Applet im Iframe intern.
// Externe Websites senden keine solche Nachricht → Default-Höhe bleibt.
const APPLET_MIN_H = 150;
const APPLET_MAX_H = 600;
window.addEventListener('message', (event) => {
  const d = event.data;
  if (!d || d.source !== 'tutorai-applet' || typeof d.height !== 'number' || !isFinite(d.height)) return;
  document.querySelectorAll('iframe.tutorai-applet').forEach((f) => {
    if (f.contentWindow === event.source) {
      // Gezoomte Applets ({.zoom=X}): Clamp zoom-korrigiert, damit die
      // VISIBLE Höhe (gemeldete Höhe × zoom) im selben Bereich bleibt.
      const zoom = parseFloat(f.getAttribute('data-zoom')) || 1;
      let h = Math.round(
        Math.min(Math.max(d.height + 8, APPLET_MIN_H / zoom), APPLET_MAX_H / zoom)
      );
      // Explizite Max-Höhe ({.height=X} → data-max-h, sichtbare px):
      // darüber hinaus scrollt das Applet im Iframe intern.
      const maxH = parseFloat(f.getAttribute('data-max-h'));
      if (maxH > 0) {
        const v = Math.min(h * zoom, maxH);
        h = Math.max(1, Math.round(v / zoom));
      }
      f.style.height = h + 'px';
      // Gezoomte Applets stehen in einem .tutorai-applet-zoom-Wrapper, dessen
      // Höhe der sichtbaren (geskalten) Höhe folgen muss (transform ändert
      // nicht die Iframe-Layout-Box). offsetHeight statt h: die Iframe-Box
      // kann per CSS max-height kleiner sein (Default-Höhe ohne explizites
      // {.height} / {.height}=X), der Wrapper muss dem sichtbaren Ergebnis
      // folgen, nicht der unbeachteten Zielhöhe.
      const wrap = f.parentElement && f.parentElement.classList.contains('tutorai-applet-zoom')
        ? f.parentElement
        : null;
      if (wrap) wrap.style.height = Math.round((f.offsetHeight || h) * zoom) + 'px';
    }
  });
});

// ─── Fenced-Code-Block-Head ──────────────────────────────────────────
// Öffnende Zeile eines ```-Blocks: optionale Sprache (bares Wort an
// erster Position) + optionale Tokens in beliebiger Reihenfolge —
// durch Leerraum getrennt ODER nahtlos direkt aneinander:
//   {#lines:1,3-5}   Zeilennummern + Highlight-Schritte (Slides)
//   {#aaid:label}    Auto-Animate-ID (Slides)
//   {#code:label}    Code-Label (nummeriert "Code N", Codes-Tabelle im
//                    Inhaltsverzeichnis, Verweis ins Skript)
//   [caption]        Code-Caption (NUR in Kombination mit {#code:label};
//                    ohne Label bleibt die Zeile Code-Text, damit z. B.
//                    ```python [1,2,3] weiter ein Code-Array ist)
//   {.zoom=X}        Schriftgröße ×X
//   {.height=Y}      Max-Höhe Y px (Suffix ∅ oder "px", wie Applets)
// (Zoom/Height: führender Punkt optional, wie bei Applets; wirken in
// Slides UND Skript — Inline-Styles am <pre>, kein Kontext-CSS nötig.)
// Unbekanntes Token oder Duplikat → null (Fallback: 1. Zeile = Code-Text,
// Tippfehler fallen sichtbar auf — wie beim Applet-Attribut-Parser).
function parseFenceHead(head) {
  const out = { language: '', lines: null, aaid: null, zoom: null, height: null, codeLabel: null, caption: null };
  let matchedAny = false;
  let i = 0;
  const n = head.length;
  const isWS = (ch) => ch === ' ' || ch === '\t';
  while (i < n) {
    if (isWS(head[i])) { i += 1; continue; }
    // Token = {…} (bis zum nächsten }), […] (bis zum nächsten ]) oder
    // bares Wort (bis zum nächsten Leerraum/{/[) → nahtlose Tokens sind
    // damit automatisch möglich.
    let tok, isBare = false;
    if (head[i] === '{') {
      const end = head.indexOf('}', i);
      if (end === -1) return null;
      tok = head.slice(i, end + 1);
      i = end + 1;
    } else if (head[i] === '[') {
      const end = head.indexOf(']', i);
      if (end === -1) return null;
      tok = head.slice(i, end + 1);
      i = end + 1;
    } else {
      let j = i;
      while (j < n && !isWS(head[j]) && head[j] !== '{' && head[j] !== '[') j += 1;
      tok = head.slice(i, j);
      i = j;
      isBare = true;
    }
    let m;
    if (isBare) {
      // Sprache: bares Wort nur an erster Position (vor jedem Token)
      if (matchedAny) return null;
      if (!/^[a-zA-Z][a-zA-Z0-9+-]*$/.test(tok)) return null;
      out.language = tok;
      matchedAny = true;
      continue;
    }
    if ((m = tok.match(/^\{#lines:([\d,| -]+)\}$/u))) {
      if (out.lines !== null) return null; // Duplikat
      out.lines = m[1];
      matchedAny = true;
    } else if ((m = tok.match(/^\{#aaid:([\p{L}0-9_-]+)\}$/u))) {
      if (out.aaid !== null) return null; // Duplikat
      out.aaid = m[1];
      matchedAny = true;
    } else if ((m = tok.match(/^\{#code:([\p{L}0-9_-]+)\}$/u))) {
      if (out.codeLabel !== null) return null; // Duplikat
      out.codeLabel = m[1];
      matchedAny = true;
    } else if (tok[0] === '[') {
      // Caption nur nach {#code:label} (und nur einmal); leer → ungültig.
      if (out.codeLabel === null) return null;
      if (out.caption !== null) return null; // Duplikat
      const cap = tok.slice(1, -1).trim();
      if (!cap) return null;
      out.caption = cap;
      matchedAny = true;
    } else if ((m = tok.match(/^\{\.?zoom=([\d.]+)\}$/))) {
      if (out.zoom !== null) return null; // Duplikat
      out.zoom = parseFloat(m[1]);
      matchedAny = true;
    } else if ((m = tok.match(/^\{\.?height=([\d.]+)([a-z]*)\}$/))) {
      if (m[2] !== '' && m[2] !== 'px') return null; // Suffix: ∅ oder "px"
      if (out.height !== null) return null; // Duplikat
      out.height = parseFloat(m[1]); // px
      matchedAny = true;
    } else {
      return null; // unbekanntes Token → Fallback
    }
  }
  return matchedAny ? out : null;
}

async function renderMarkdown(text, targetElement, options = {}) {
  if (!text || typeof text !== 'string') {
    targetElement.innerHTML = '';
    return;
  }

  const { preview = false, sectionId = null, slideMode = false } = options;

  // Globale Label-Map für Querverweise (gecacht; null auf Nicht-Kurs-Seiten).
  const refMap = await getCourseRefMap();
  const globalLabels = (refMap && refMap.labels) || {};
  const chapterRef = _chapterRef(refMap, sectionId);

  // 0. Kapitel-Label ({#sec:label} als erste nicht-leere Zeile) → kein Content, entfernen
  text = text.replace(/^(?:[ \t]*\n)*[ \t]*\{#sec:[\p{L}0-9_-]+\}[ \t]*(?:\r?\n|$)/u, '');

  // ── Pre-extraction phase ──────────────────────────────────────────
  // Order matters: extract code blocks FIRST so LaTeX extraction never
  // sees $ signs inside them.

  // 1a. Extract ```mermaid ... ``` blocks
  const mermaidBlocks = [];
  let processed = text.replace(/```mermaid\n([\s\S]*?)```/g, (match, diagram) => {
    mermaidBlocks.push(diagram.trim());
    return `%%MERmaid_BLOCK_${mermaidBlocks.length - 1}%%`;
  });

  // 1b. Extract remaining fenced code blocks (``` ... ```)
  const fencedCodeBlocks = [];
  processed = processed.replace(/```([\s\S]*?)```/g, (match, content) => {
      fencedCodeBlocks.push(content);
    return `%%FC${fencedCodeBlocks.length - 1}%%`;
  });

  // 1c. Extract inline code spans (`...`)
  const inlineCodeSpans = [];
  processed = processed.replace(/`([^`]+?)`/g, (match, content) => {
    inlineCodeSpans.push(content);
    return `%%IC${inlineCodeSpans.length - 1}%%`;
  });

  // 1d. Convert callout boxes: @box:{typ} … @endbox
  //     → statischer HTML-Wrapper (marked lässt HTML-Blöcke unverändert
  //     durch, DOMPurify behält die divs). Der INHALT bleibt im Fließtext →
  //     $...$/{#fig:…}/@fig:/@task:… darin werden wie gewohnt extrahiert.
  //     Code-Blöcke sind zu diesem Zeitpunkt bereits extrahiert →
  //     in Code bleibt @box:… literal.
  processed = processed.replace(
    /@box:([\p{L}0-9_-]+)\r?\n([\s\S]*?)\r?\n@endbox/gu,
    (match, type, content) => {
      if (type === 'highlight') {
        // Headless-Box (kein Kopf). Optional: ERSTE Zeile @boxcolor:<farbe>
        // → normalisierter Inline-Style (ungültig → CSS-Default, Primärfarbe);
        // die @boxcolor-Zeile wird im Match-Fall immer entfernt.
        let body = content;
        const m = /^@boxcolor:\s*(.+?)\s*\r?\n([\s\S]*)$/u.exec(body);
        if (m) {
          const rgba = _boxColorToRgba(m[1]);
          body = m[2];
          if (rgba) {
            return (
              '\n\n<div class="tutorai-callbox tutorai-callbox-highlight"' +
              ' style="background-color:' + rgba + '">\n\n' + body.trim() + '\n\n</div>\n\n'
            );
          }
        }
        return (
          '\n\n<div class="tutorai-callbox tutorai-callbox-highlight">\n\n' +
          body.trim() + '\n\n</div>\n\n'
        );
      }
      const info = CALLOUT_TYPES[type] || { icon: '📄', title: type.charAt(0).toUpperCase() + type.slice(1) };
      const body = content.trim();
      return (
        '\n\n<div class="tutorai-callbox tutorai-callbox-' + type + '">\n' +
        '<div class="tutorai-callbox-head">' +
        '<span class="tutorai-callbox-icon" aria-hidden="true">' + info.icon + '</span> ' +
        escapeHtml(info.title) + '</div>\n' +
        '<div class="tutorai-callbox-body">\n\n' + body + '\n\n</div>\n</div>\n\n'
      );
    }
  );

  // 1e. Extract figures (labeled + unlabeled) mit Attribut-Tokens.
  //     → labeliert {#fig:label} = nummerierte Abbildung ("Abb. N") mit Anker,
  //       latex-artig verlinkbar; unlabelt .html = Applet (Iframe); unlabeltes
  //       Bild + {.height=X} = Bild mit Max-Höhe.
  //     Nummerierung: global (kursweit) via refmap; neue (noch ungespeicherte)
  //     Labels bekommen in Slides S-Nummern (S1, S2, …) bzw. im Skript
  //     Fallback-Nummern nach der letzten bekannten des Kapitels.
  //     Attribut-Tokens nach dem Bild (in BELIEBIGER Reihenfolge, gleiche Zeile
  //     oder Zeilenumbbruch ohne Leerzeile):
  //       {#fig:label}, {#fragment}, {#fragment:id}, {#Fragment},
  //       {.height=X} (Max-Höhe in px, optionaler Suffix "px"),
  //       {.zoom=X} (Iframes: Zoom-Faktor des Applet-Inhalts).
  //     height/zoom wirken in Slides UND Skript. Unlabelte Medien nehmen
  //     optional ihre Attribute an (Applet: {.zoom=X} und/oder {.height=X},
  //     Bild: nur {.height=X}); ohne Attribut bleibt ein Applet ein normales
  //     Iframe. Medien mit Alt-Text bekommen diesen als Caption unter sich
  //     angezeigt (unlabelt: ohne Nummer; labelt: „Abb. N: Caption").
  //     Alles andere (auch unbekannte Tokens) → Match wird abgebrochen, der
  //     Text bleibt literal (Tippfehler fallen so auf).
  const figures = [];
  const appletFigures = [];
  const plainFigures = [];
  const figLabelNumbers = {};
  const figFallbackBase = chapterRef ? (chapterRef.maxFig || 0) : 0;
  let figFallbackCount = 0;
  // Slide-Decks: eigene Labels (nicht im Skript enthalten) bekommen eigene
  // Nummerierung (S1), (S2), … — abgesetzt von der Skript-Nummerierung
  // (wie bei den Formeln, s. 1g).
  let slideFigCount = 0;
  // Zwei einfache Regexes statt einem verschachtelten Monster:
  const IMG_REF = /!\[([^\]]*)\]\(([^)\s]+)\)/g;
  // Ein Attribut-Block: {…} direkt hinter der Referenz, nur Leerraum oder
  // ein Zeilenumbruch (ohne Leerzeile) dazwischen.
  const ATTR_BLOCK = /^[ \t]*(?:\r?\n[ \t]*)?\{([^{}]*)\}/;
  // Literale Tails (unbekannte/ungültige Tokens, schlichte <img>): werden
  // vor den Folgeschritten (1f–1k) aus dem Text genommen, damit z. B.
  // {#fragment} im Tail nicht als echtes Fragment-Sentinel interpretiert
  // wird, und direkt vor marked.parse (Step 2) wiederhergestellt.
  const imgLiteralTails = [];
  {
    let out = '';
    let last = 0;
    let m;
    while ((m = IMG_REF.exec(processed)) !== null) {
      const alt = m[1];
      const src = m[2];
      // Aufeinanderfolgende Attribut-Blöcke hinter der Referenz konsumieren.
      const tokens = [];
      let pos = m.index + m[0].length;
      for (;;) {
        const bm = ATTR_BLOCK.exec(processed.slice(pos));
        if (!bm) break;
        tokens.push(bm[1].trim());
        pos += bm[0].length;
      }
      // Tokens validieren: nur bekannte Typen, je Attribut höchstens einmal.
      const a = { label: null, frag: null, height: null, zoom: null, aaid: null };
      let valid = true;
      for (const inner of tokens) {
        let t;
        if ((t = inner.match(/^#fig:([\p{L}0-9_-]+)$/u))) {
          if (a.label !== null) { valid = false; break; } // Duplikat-Label
          a.label = t[1];
        } else if ((t = inner.match(/^#([Ff])ragment(?::([\p{L}0-9_-]+))?$/u))) {
          if (a.frag) { valid = false; break; } // Duplikat-Fragment
          a.frag = { type: _fragType(t[1], t[2]), id: t[2] || null };
        } else if ((t = inner.match(/^#aaid:([\p{L}0-9_-]+)$/u))) {
          // Auto-Animate-Element-ID (nur Slides; Reveal 4.6: data-id)
          if (a.aaid !== null) { valid = false; break; } // Duplikat
          a.aaid = t[1];
        } else if ((t = inner.match(/^\.?height=([\d.]+)([a-z]*)$/))) {
          // Führender Punkt optional (beide Formen valid): {height=X} /
          // {.height=X}. Suffix-Check separat (einfacher als `px?` im Regex
          // selbst — robust gegenüber V8/Chrome-Regel-3-Veränderungen):
          // erlaubt: kein Suffix oder genau "px".
          if (t[2] !== '' && t[2] !== 'px') { valid = false; break; }
          if (a.height !== null) { valid = false; break; } // Duplikat
          a.height = parseFloat(t[1]); // px
        } else if ((t = inner.match(/^\.?zoom=([\d.]+)$/))) {
          if (a.zoom !== null) { valid = false; break; } // Duplikat
          a.zoom = parseFloat(t[1]);
        } else {
          valid = false; break; // unbekanntes Attribut → literal
        }
      }
      // Unlabelt: Applet nimmt (k)ein {.zoom=X} und/oder (k)ein {.height=X},
      // Bild (k)ein {.height=X} (Zoom nur für Applets). Medien mit Alt-Text
      // bekommen ihre Caption unter sich (unlabelt ohne Nummer).
      // Ungültige Kombinationen bleiben literal (Tippfehler fallen auf).
      let repl = null;
      if (valid) {
        if (a.label !== null) {
          let num;
          if (a.label in figLabelNumbers) {
            num = figLabelNumbers[a.label]; // Duplikat → erstes Vorkommen gewinnt
          } else {
            const g = globalLabels[a.label];
            if (g && g.kind === 'fig') {
              num = g.num; // gespeichertes Label → exakte globale Nummer
            } else if (slideMode) {
              slideFigCount += 1;
              num = 'S' + slideFigCount; // slide-eigenes Label → S1, S2, …
            } else {
              figFallbackCount += 1;
              num = figFallbackBase + figFallbackCount; // neues (ungespeichertes) Label
            }
            figLabelNumbers[a.label] = num;
          }
          figures.push({ alt, src, label: a.label, num, frag: a.frag, height: a.height, zoom: a.zoom, aaid: a.aaid });
          repl = `%%FIG_${figures.length - 1}%%`;
        } else if (isAppletSrc(src) && !a.frag) {
          appletFigures.push({ alt, src, zoom: a.zoom, height: a.height });
          repl = `%%APPLETFIG_${appletFigures.length - 1}%%`;
        } else if (!isAppletSrc(src) && !a.frag && a.zoom === null &&
                   (a.height !== null || alt.trim() !== '')) {
          // Bild mit Max-Höhe und/oder Caption (= Alt-Text, ohne Nummer).
          // Ohne Attribut UND ohne Alt-Text bleibt das Bild literal
          // (normales marked-<img>), wie bisher.
          plainFigures.push({ alt, src, height: a.height });
          repl = `%%PLAINFIG_${plainFigures.length - 1}%%`;
        }
      }
      // repl = null (inkl. Bild ohne Attribute) → Referenz + Tail bleiben
      // literal (per Platzhalter, s. imgLiteralTails).
      out += processed.slice(last, m.index) + (repl !== null
        ? repl
        : `%%IMGLIT_${imgLiteralTails.push(processed.slice(m.index, pos)) - 1}%%`);
      last = pos;
      IMG_REF.lastIndex = pos;
    }
    processed = out + processed.slice(last);
  }

  // 1f. Handle escaped dollar signs: \$ → placeholder
  const escapedDollar = '%%ED%%';
  processed = processed.replace(/\\\$/g, escapedDollar);

  // 1g. Extract $$...$$ display blocks (optional trailing {#eq:label}
  //     → nummerierte Gleichung "(N)" mit Anker, latex-artig verlinkbar)
  const latexBlocks = [];
  const latexLabels = [];
  const latexAaids = [];
  const eqLabelNumbers = {};
  const eqFallbackBase = chapterRef ? (chapterRef.maxEq || 0) : 0;
  let eqFallbackCount = 0;
  // Slide-Decks: eigene Labels (nicht im Skript enthalten) bekommen eigene
  // Nummerierung (S1), (S2), … — abgesetzt von der Skript-Nummerierung.
  let slideEqCount = 0;
  const latexFragments = [];
  processed = processed.replace(
    /\$\$([\s\S]*?)\$\$(?:\s*\{#eq:([\p{L}0-9_-]+)\})?(?:\s*\{#([Ff])ragment(?::([\p{L}0-9_-]+))?\})?(?:\s*\{#aaid:([\p{L}0-9_-]+)\})?/gu,
    (match, latex, label, fchar, id, aaid) => {
      latexBlocks.push(latex.trim());
      if (label && !(label in eqLabelNumbers)) {
        const g = globalLabels[label];
        if (g && g.kind === 'eq') {
          eqLabelNumbers[label] = g.num; // gespeichertes Label → exakte globale Nummer
        } else if (slideMode) {
          slideEqCount += 1;
          eqLabelNumbers[label] = 'S' + slideEqCount; // slide-eigenes Label → (S1), (S2), …
        } else {
          eqFallbackCount += 1;
          eqLabelNumbers[label] = eqFallbackBase + eqFallbackCount; // neues (ungespeichertes) Label
        }
      }
      latexLabels.push(label || null);
      latexFragments.push(fchar !== undefined ? { type: _fragType(fchar, id), id: id || null } : null);
      latexAaids.push(aaid || null);
      return `%%LATEX_BLOCK_${latexBlocks.length - 1}%%`;
    }
  );

  // 1h. Extract $...$ inline math (no newlines allowed)
  const latexInlines = [];
  processed = processed.replace(/\$([^$\n]+?)\$/g, (match, latex) => {
    latexInlines.push(latex.trim());
    return `%%LATEX_INLINE_${latexInlines.length - 1}%%`;
  });

  // 1i. Extract cross-references: @fig:label / @eq:label / @sec:label / @kap:label
  const xrefs = [];
  processed = processed.replace(/@(fig|eq|sec|kap):([\p{L}0-9_-]+)/gu, (match, kind, label) => {
    xrefs.push({ kind, label });
    return `%%XREF_${xrefs.length - 1}%%`;
  });

  // 1j. Extract task references: @task:{id}
  //     → Aufgaben-Box (Daten via refmap.tasks; Code-Blöcke sind zu
  //     diesem Zeitpunkt bereits extrahiert → in Code bleibt es literal).
  const taskRefs = [];
  processed = processed.replace(/@task:(\d+)/g, (match, id) => {
    taskRefs.push(id);
    return `%%TASKREF_${taskRefs.length - 1}%%`;
  });

  // 1k. Slide-Fragments → unsichtbarer Sentinel (Typ/ID als data-Attribute,
  //     überleben die DOMPurify-Sanitize). Formen (Details:
  //     _applyFragmentMarkers am Ende von renderMarkdown):
  //       {#fragment}      → normales Fragment (eigener Einblend-Schritt)
  //       {#fragment:id}   → ID-Gruppe (gleiche ID → gleicher Schritt)
  //       {#Fragment}      → Gate (Groß, ohne ID): Element + alle FOLGENDEN
  //                           Inhalte der Folie erscheinen in einem Schritt
  //     eq/fig haben ihren Marker oben bereits selbst extrahiert.
  if (slideMode) {
    processed = processed.replace(
      /\{#([Ff])ragment(?::([\p{L}0-9_-]+))?\}/gu,
      (m, fchar, id) => {
        const idAttr = id ? ` data-frag-id="${id}"` : '';
        return `<span class="tutorai-frag-marker" data-frag="${_fragType(fchar, id)}"${idAttr}></span>`;
      }
    );
    // {#aaid:label} → Sentinel: _applyFragmentMarkers setzt data-id auf das
    // umgebende Block-Element (Reveal 4.6: explizites Auto-Animate-Element-
    // Matching per data-id; in Reveal 5 heißt das Attribut
    // data-auto-animate-id). eq/fig nehmen {#aaid:…} oben bereits direkt.
    processed = processed.replace(
      /\{#aaid:([\p{L}0-9_-]+)\}/gu,
      (m, label) => `<span class="tutorai-aaid-marker" data-aaid="${label}"></span>`
    );
  }

  // 1l. Literal-Image-Tails wiederherstellen (nach 1k, damit enthaltene
  //     {#fragment}-Token als sichtbarer Text bleiben und KEIN Sentinel werden)
  imgLiteralTails.forEach((tail, idx) => {
    processed = processed.split(`%%IMGLIT_${idx}%%`).join(tail);
  });

  // 2. Render Markdown (marked)
  let html = marked.parse(processed, {
    breaks: true,
    gfm: true,
    headerIds: false,
    mangle: false,
  });

  // 3. Restore fenced code blocks
  //    Öffnende Zeile (parseFenceHead): optionale Sprache + optionale
  //    Tokens in beliebiger Reihenfolge, nahtlos oder mit Leerzeichen:
  //    {#lines:…} (Slides: Zeilennummern + Per-Line-Reveal, Reveal-
  //    Highlight-Plugin; "|" = weiterer Schritt),
  //    {#aaid:label} (Slides: Auto-Animate-ID → <pre data-id=…>; Blöcke
  //    mit demselben Label auf aufeinanderfolgenden Folien animiert Reveal
  //    nativ per Zeile ineinander, s. Reveal-Code-Beispiel),
  //    {#code:label} (+ optionales [caption] direkt danach, ohne
  //    Leerzeichen): nummerierter Code-Block ("Code N") mit Anker —
  //    Nummer wie bei Abbildungen/Formeln (im Skript gespeichertes Label →
  //    Skript-Nummer, in Slides: eigene Labels → S1, S2, …); in Slides ist
  //    die Nummer klickbar (→ Skript). Beschriftete Blöcke stehen in einer
  //    „Codes-Tabelle“ im Inhaltsverzeichnis (via refmap).
  //    {.zoom=X} / {.height=Y} (Schriftgröße / Max-Höhe + internes
  //    Scrollen; Slides UND Skript — Inline-Styles am <pre>, die auch von
  //    den hljs-Clones geerbt werden).
  //    Unbekanntes Token/Duplikat → Fallback: die erste Zeile ist Sprache
  //    NUR wenn rein (sonst Code-Teil).
  const codeLabelNumbers = {};
  const codeFallbackBase = chapterRef ? (chapterRef.maxCode || 0) : 0;
  let codeFallbackCount = 0;
  // Slide-Decks: slide-eigene Code-Labels → S1, S2, … (wie eq/fig).
  let slideCodeCount = 0;
  fencedCodeBlocks.forEach((content, idx) => {
    const safe = content.replace(escapedDollar, '$');
    const lines = safe.split('\n');
    let language = '';
    let lineNumbers = null;
    let blockAaid = null;
    let codeZoom = null;
    let codeHeight = null;
    let codeLabel = null;
    let codeCaption = null;
    let codeNum = null;
    let codeBody;
    if (lines.length > 1) {
      const parsed = parseFenceHead(lines[0].trim());
      if (parsed) {
        language = parsed.language;
        lineNumbers = parsed.lines;
        blockAaid = parsed.aaid;
        codeZoom = parsed.zoom;
        codeHeight = parsed.height;
        codeLabel = parsed.codeLabel;
        codeCaption = parsed.caption;
        codeBody = lines.slice(1).join('\n');
      }
    }
    if (codeBody === undefined) codeBody = safe;
    const escaped = codeBody.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\$\$/g, '$$$$$$$$');
    const langAttr = language ? ` class="language-${language}"` : '';
    // {#aaid} ohne {#lines}: bares data-line-numbers → das Reveal-Highlight-
    // Plugin baut die hljs-ln-Tabelle OHNE Highlight-Schritte — die
    // .hljs-ln-code-Zellen braucht das native AutoAnimate zum Zeilen-Matching.
    const linesAttr = lineNumbers
      ? ` data-line-numbers="${escapeHtml(lineNumbers.trim())}"`
      : (blockAaid ? ' data-line-numbers' : '');
    const aaidAttr = blockAaid ? ` data-id="${blockAaid}"` : '';
    // {.zoom=X} → font-size in em (Text bleibt scharf; das Theme-Padding
    // 1em am code skaliert proportional mit), {.height=Y} → max-height +
    // internes Scrollen (die hljs-Clones sind absolute Kinder → sie
    // scrollen MIT dem Container, das Zeilen-Alignement bleibt erhalten).
    // !important: muss auch die !important-Regeln von main.css (Skript-
    // Ansicht/Thumbnails: pre font-size 0.825rem) schlagen — Inline-
    // !important gewinnt gegen Stylesheet-!important.
    let preStyle = '';
    if (codeZoom !== null) {
      preStyle += `font-size:${codeZoom}em !important;`;
      // --tz: Default-Max-Höhe (9em, slides.css) wird durch den Zoom-Faktor
      // geteilt → sichtbare Default-Höhe identisch zum zoomlosen Default
      // (exakt wie bei gezoomten Applets, s. dort).
      preStyle += `--tz:${codeZoom};`;
    }
    if (codeHeight !== null) preStyle += `max-height:${codeHeight}px !important;overflow:auto !important;`;
    const preStyleAttr = preStyle ? ` style="${preStyle}"` : '';
    let blockHtml = `<pre${aaidAttr}${preStyleAttr}><code${langAttr}${linesAttr}>${escaped}</code></pre>`;
    // {#code:label} und/oder [caption] → Code-Figur mit Caption
    // ("Code N: caption"), wie bei Abbildungen.
    if (codeLabel !== null || codeCaption !== null) {
      if (codeLabel !== null) {
        if (codeLabel in codeLabelNumbers) {
          codeNum = codeLabelNumbers[codeLabel]; // Duplikat → erstes Vorkommen
        } else {
          const g = globalLabels[codeLabel];
          if (g && g.kind === 'code') {
            codeNum = g.num; // gespeichertes Label → exakte globale Nummer
          } else if (slideMode) {
            slideCodeCount += 1;
            codeNum = 'S' + slideCodeCount; // slide-eigenes Label
          } else {
            codeFallbackCount += 1;
            codeNum = codeFallbackBase + codeFallbackCount;
          }
          codeLabelNumbers[codeLabel] = codeNum;
        }
      }
      let numHtml = '';
      if (codeNum !== null) {
        const g = globalLabels[codeLabel];
        if (slideMode && g && g.kind === 'code') {
          const cid = (refMap && refMap.courseId) || _getCourseId() || '';
          numHtml =
            `<a class="tutorai-code-num-link" href="/courses/${cid}/script#code:${codeLabel}"` +
            ` title="Zum Code im Skript">Code ${codeNum}</a>`;
        } else {
          numHtml = `Code ${codeNum}`;
        }
      }
      const capParts = [];
      if (numHtml) capParts.push(numHtml);
      if (codeCaption) capParts.push(escapeHtml(codeCaption));
      const idAttr = codeLabel !== null ? ` id="code:${codeLabel}"` : '';
      blockHtml = `<figure class="tutorai-code-figure"${idAttr}>${blockHtml}` +
        `<figcaption>${capParts.join(': ')}</figcaption></figure>`;
    }
    html = html.replace(`%%FC${idx}%%`, blockHtml.replace(/\$/g, '$$$$'));
  });

  // 4. Restore inline code spans
  inlineCodeSpans.forEach((content, idx) => {
    const safe = content.replace(escapedDollar, '$');
    const escaped = safe.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\$\$/g, '$$$$$$$$');
    html = html.replace(`%%IC${idx}%%`, `<code>${escaped}</code>`);
  });

  // 5. Restore LaTeX blocks (labeled ones as numbered equation "(N)")
  //    Slide-Mode: Nummer eines im Skript vorhandenen Labels ist klickbar und
  //    verlinkt zur Gleichung im Skript; slide-eigene Labels zeigen (S1), …
  latexBlocks.forEach((latex, idx) => {
    const rendered = renderLatexBlock(latex);
    const label = latexLabels[idx];
    const frag = latexFragments[idx];
    const aaid = latexAaids[idx];
    const fragAttrs = frag
      ? ` data-frag="${frag.type}"${frag.id ? ` data-frag-id="${frag.id}"` : ''}`
      : '';
    // Auto-Animate-Element-ID (Reveal 4.6: data-id, s. {#aaid:…})
    const aaidAttr = aaid ? ` data-id="${aaid}"` : '';
    if (label) {
      const num = eqLabelNumbers[label];
      const g = globalLabels[label];
      let numHtml;
      if (slideMode && g && g.kind === 'eq') {
        const cid = (refMap && refMap.courseId) || _getCourseId() || '';
        numHtml =
          `<a class="tutorai-eq-num tutorai-eq-num-link" href="/courses/${cid}/script#eq:${label}"` +
          ` title="Zur Gleichung im Skript">(${num})</a>`;
      } else {
        numHtml = `<span class="tutorai-eq-num">(${num})</span>`;
      }
      const wrapped =
        `<div id="eq:${label}" class="tutorai-equation"${fragAttrs}${aaidAttr}>${rendered}${numHtml}</div>`;
      html = html.replace(`%%LATEX_BLOCK_${idx}%%`, wrapped.replace(/\$/g, '$$$$'));
    } else if (frag || aaid) {
      // Unlabeled + Fragment: Katex' <span class="katex-display"> in ein
      // fragmentierbares Block-Element verpacken (wird beim innerHTML-Parse
      // aus dem umgebenden <p> gehoistet, wie beim labeled Fall). Die
      // Reveal-Klasse + Index vergibt _applyFragmentMarkers (slideMode).
      html = html.replace(`%%LATEX_BLOCK_${idx}%%`, `<div${fragAttrs}${aaidAttr}>${rendered}</div>`.replace(/\$/g, '$$$$'));
    } else {
      html = html.replace(`%%LATEX_BLOCK_${idx}%%`, rendered.replace(/\$/g, '$$$$'));
    }
  });

  // 6. Restore inline LaTeX
  latexInlines.forEach((latex, idx) => {
    html = html.replace(`%%LATEX_INLINE_${idx}%%`, renderLatexInline(latex));
  });

  // 6a. Restore numbered figures (.html-Medien als interaktives Iframe)
  //     {.height=X} (px, Max-Höhe) / {.zoom=X} wirken in Slides UND Skript.
  //     !important: schlägt die generischen img-/applet-Regeln aus
  //     slides.css/main.css (height:auto, max-height, width:100% — alle mit
  //     !important; Inline-!important gewinnt).
  //     Höhe = max-height (nicht exakt): Medien nutzen die verfügbare Breite
  //     ratio-erhaltend aus, bis die Max-Höhe erreicht ist ("contain"-Effekt
  //     über die Basis-CSS max-width:100% + height:auto). Applets: volle
  //     Breite, interne Scrollbar, wenn die Max-Höhe überschritten wird.
  figures.forEach((f, idx) => {
    const safeAlt = escapeHtml(f.alt);
    const isApplet = isAppletSrc(f.src);
    // Externe Website/YouTube (http(s)): keine Auto-Sizing-Nachrichten →
    // {.height=Y} setzt die ECHTE Höhe (height, nicht max-height) —
    // sonst bliebe das Iframe bei der Browser-Default-Höhe (150 px) und
    // die Angabe wirkte nicht. YouTube-Watch-Links → Embed-Player.
    const isExternalApplet = isApplet && /^https?:\/\//i.test(f.src);
    const isVideo = youtubeEmbedSrc(f.src) !== null;
    const effSrc = youtubeEmbedSrc(f.src) || f.src;
    const parts = [];
    let dataZoom = '';
    if (isVideo && f.height != null) {
      // YouTube: fixe Höhe + proportionale Breite (16:9), zentriert in
      // der Figure (text-align:center). Inline-!important schlägt die
      // width:100%-Regel (slides.css) und die 9em-Default-Max-Höhe.
      const hExpr = f.zoom != null ? `calc(${f.height}px / ${f.zoom})` : `${f.height}px`;
      const wExpr = f.zoom != null
        ? `calc(${f.height}px * 16 / 9 / ${f.zoom})`
        : `${Math.round((f.height * 16) / 9)}px`;
      parts.push(`height: ${hExpr} !important`);
      parts.push(`max-height: ${hExpr} !important`);
      parts.push(`width: ${wExpr} !important`);
    } else if (f.height != null) {
      // Höhe in sichtbaren px. Gezoomtes Applet: geteilt durch den
      // Zoom-Faktor, da die transform die Iframe-Layout-Box skaliert.
      parts.push(f.zoom != null
        ? `${isExternalApplet ? 'height' : 'max-height'}: calc(${f.height}px / ${f.zoom}) !important`
        : `${isExternalApplet ? 'height' : 'max-height'}: ${f.height}px !important`);
      // Externe Website: die 9em-Default-Max-Höhe (slides.css, Stylesheet-
      // !important) dürfte die explizite height nicht deckeln → gleiche
      // Max-Höhe inline (Inline-!important gewinnt).
      if (isExternalApplet) {
        parts.push(f.zoom != null
          ? `max-height: calc(${f.height}px / ${f.zoom}) !important`
          : `max-height: ${f.height}px !important`);
      }
    }
    if (f.zoom != null) {
      // Zoom via transform (universell unterstützt, im Gegensatz zur
      // zoom-Property): Width-Kompensation + Skalierung füllen exakt die
      // Spaltenbreite; der Wrapper übernimmt die sichtbare (geskalte) Höhe.
      // (Video mit expliziter {.height} trägt oben bereits die 16:9-Breite.)
      if (!(isVideo && f.height != null)) parts.push(`width: calc(100% / ${f.zoom}) !important`);
      parts.push(`transform: scale(${f.zoom})`);
      parts.push('transform-origin: 0 0');
      // Ohne explizite {.height} gilt die Default-Max-Höhe (9em in Slides)
      // auch für gezoomte Applets — die slides.css-Regel teilt sie durch
      // --tz (transform vergrößert die Layout-Box), damit die sichtbare
      // Höhe der zoomlosen Default-Höhe entspricht. --tz setzen, damit sie
      // ohne data-max-h greift. (Bilder mit Zoom behalten none: Zoom ist
      // Applet-Semantik, die 7em-Bild-Cap soll dort nicht zuschlagen.)
      if (isApplet) parts.push(`--tz: ${f.zoom}`);
      else if (f.height == null) parts.push('max-height: none !important');
      dataZoom = ` data-zoom="${f.zoom}"`; // Auto-Sizing: Clamp zoom-korrigiert
    }
    const mediaStyle = parts.length ? ` style="${parts.join('; ')}"` : '';
    const dataMaxH = isApplet && !isExternalApplet && f.height != null ? ` data-max-h="${f.height}"` : '';
    const mediaTag = isApplet
      ? `<iframe src="${escapeHtml(effSrc)}" class="tutorai-applet${isVideo ? ' tutorai-video' : ''}" sandbox="${appletSandboxAttr(effSrc)}" loading="lazy" title="${safeAlt}"${dataZoom}${dataMaxH}${mediaStyle}></iframe>`
      : `<img src="${escapeHtml(f.src)}" alt="${safeAlt}"${mediaStyle}>`;
    // Gezoomte Applets in einen overflow:hidden-Wrapper (sichtbare =
    // geskalte Höhe; transform ändert die Iframe-Layout-Box nicht). Initiale
    // Höhe: Applets = Auto-Sizing-Default (150 px × zoom), ggf. auf die
    // Max-Höhe begrenzt (der Auto-Sizing-Listener clamped danach ebenfalls);
    // externe Websites = exakt die vorgegebene Höhe (Iframe-Höhe ist fix);
    // Videos ohne {.height} = Spaltenbreite × 9/16 (16:9, CSS aspect-ratio).
    // WICHTIG: <span>, kein <div> — ein div würde das umgebende <p>
    // (HTML-Parsing, unabhängig vom CSS display) schließen und das Iframe
    // in einen anderen font-size-Kontext rücken (p = 0.95em); die
    // em-basierte Default-Max-Höhe müsste dann nicht mehr der zoomlosen
    // Default-Höhe am selben Ort entsprechen. Ein span mit
    // display:inline-block + width:100% bleibt im <p> (s. CSS).
    const zoomInitH = f.height != null
      ? (isExternalApplet
        ? `${f.height}px`
        : `min(calc(150px * ${f.zoom}), ${f.height}px)`)
      : (isVideo
        ? 'calc(100% * 0.5625)'
        : `calc(150px * ${f.zoom})`);
    const innerMedia = (isApplet && f.zoom != null)
      ? `<span class="tutorai-applet-zoom" style="height: ${zoomInitH}">${mediaTag}</span>`
      : mediaTag;
    const figFragAttrs = f.frag
      ? ` data-frag="${f.frag.type}"${f.frag.id ? ` data-frag-id="${f.frag.id}"` : ''}`
      : '';
    const figAaidAttr = f.aaid ? ` data-id="${f.aaid}"` : '';
    // Nummer: im Skript gespeichertes Label → in Slides klickbar (→ Skript),
    // wie bei den Formeln; sonst (auch slide-eigene S-Nummern) plain.
    const g = globalLabels[f.label];
    const numHtml = (slideMode && g && g.kind === 'fig')
      ? (() => {
        const cid = (refMap && refMap.courseId) || _getCourseId() || '';
        return `<a class="tutorai-fig-num-link" href="/courses/${cid}/script#fig:${f.label}" title="Zur Abbildung im Skript">Abb. ${f.num}</a>`;
      })()
      : `Abb. ${f.num}`;
    const figHtml =
      `<figure id="fig:${f.label}" class="tutorai-figure"${figFragAttrs}${figAaidAttr}>` +
      innerMedia +
      `<figcaption>${numHtml}${f.alt ? `: ${safeAlt}` : ''}</figcaption></figure>`;
    html = html.replace(`%%FIG_${idx}%%`, figHtml.replace(/\$/g, '$$$$'));
  });

  // 6a2. Restore unlabeled HTML figures (interaktives Applet/YouTube, ohne
  //      Nummer) — Medien mit Alt-Text bekommen die Caption unter sich.
  //      {.height=X} / {.zoom=X}: Semantik wie bei labeled Figures (s. 6a).
  appletFigures.forEach((f, idx) => {
    const isExternal = /^https?:\/\//i.test(f.src);
    const isVideo = youtubeEmbedSrc(f.src) !== null;
    const effSrc = youtubeEmbedSrc(f.src) || f.src;
    const parts = [];
    let dataZoom = '';
    if (isVideo && f.height != null) {
      // YouTube: fixe Höhe + proportionale Breite (16:9) — s. labeled-
      // Figures oben.
      const hExpr = f.zoom != null ? `calc(${f.height}px / ${f.zoom})` : `${f.height}px`;
      const wExpr = f.zoom != null
        ? `calc(${f.height}px * 16 / 9 / ${f.zoom})`
        : `${Math.round((f.height * 16) / 9)}px`;
      parts.push(`height: ${hExpr} !important`);
      parts.push(`max-height: ${hExpr} !important`);
      parts.push(`width: ${wExpr} !important`);
    } else if (f.height != null) {
      parts.push(f.zoom != null
        ? `${isExternal ? 'height' : 'max-height'}: calc(${f.height}px / ${f.zoom}) !important`
        : `${isExternal ? 'height' : 'max-height'}: ${f.height}px !important`);
      // Externe Website: 9em-Default-Max-Höhe nicht zulassen (s. 6a).
      if (isExternal) {
        parts.push(f.zoom != null
          ? `max-height: calc(${f.height}px / ${f.zoom}) !important`
          : `max-height: ${f.height}px !important`);
      }
    }
    if (f.zoom != null) {
      // (Video mit expliziter {.height} trägt bereits die 16:9-Breite.)
      if (!(isVideo && f.height != null)) parts.push(`width: calc(100% / ${f.zoom}) !important`);
      parts.push(`transform: scale(${f.zoom})`);
      parts.push('transform-origin: 0 0');
      // Ohne {.height} gilt die Default-Max-Höhe (9em in Slides) — teilt die
      // slides.css-Regel durch --tz (s. labeled-Figures oben).
      parts.push(`--tz: ${f.zoom}`);
      dataZoom = ` data-zoom="${f.zoom}"`;
    }
    const mediaStyle = parts.length ? ` style="${parts.join('; ')}"` : '';
    const dataMaxH = !isExternal && f.height != null ? ` data-max-h="${f.height}"` : '';
    const iframeTag =
      `<iframe src="${escapeHtml(effSrc)}" class="tutorai-applet${isVideo ? ' tutorai-video' : ''}" sandbox="${appletSandboxAttr(effSrc)}" loading="lazy" title="${escapeHtml(f.alt)}"${dataZoom}${dataMaxH}${mediaStyle}></iframe>`;
    const zoomInitH = f.height != null
      ? (isExternal ? `${f.height}px` : `min(calc(150px * ${f.zoom}), ${f.height}px)`)
      : (isVideo ? 'calc(100% * 0.5625)' : `calc(150px * ${f.zoom})`);
    // <span>-Wrapper (kein <div>): muss im umgebenden <p> bleiben, damit
    // das Iframe dieselbe font-size erbt wie im zoomlosen Fall (s. 6a).
    const iframeHtml = (f.zoom != null)
      ? `<span class="tutorai-applet-zoom" style="height: ${zoomInitH}">${iframeTag}</span>`
      : iframeTag;
    // Caption unter dem Medium: Alt-Text (unlabelt → ohne Nummer).
    const figHtml = f.alt.trim()
      ? `<figure class="tutorai-figure">${iframeHtml}<figcaption>${escapeHtml(f.alt.trim())}</figcaption></figure>`
      : iframeHtml;
    html = html.replace(`%%APPLETFIG_${idx}%%`, figHtml.replace(/\$/g, '$$$$'));
  });

  // 6a3. Restore unlabeled plain images — mit Max-Höhe ({.height=X}, s. 6a)
  //      und/oder Caption (= Alt-Text, ohne Nummer) → <figure>;
  //      sonst simples <img> (wie ein normales marked-Bild).
  plainFigures.forEach((f, idx) => {
    const imgStyle = f.height != null ? ` style="max-height: ${f.height}px !important"` : '';
    const imgTag = `<img src="${escapeHtml(f.src)}" alt="${escapeHtml(f.alt)}"${imgStyle}>`;
    const imgHtml = (f.height != null || f.alt.trim())
      ? `<figure class="tutorai-figure">${imgTag}` +
        (f.alt.trim() ? `<figcaption>${escapeHtml(f.alt.trim())}</figcaption>` : '') +
        `</figure>`
      : imgTag;
    html = html.replace(`%%PLAINFIG_${idx}%%`, imgHtml.replace(/\$/g, '$$$$'));
  });

  // 6a3. Heading-Nummerierung (h2–h4) + {#sec:label}-Anker
  //      Kapitelnummer aus dem refmap (rollenabhängig: Student = veröffentlichte
  //      Nummerierung). Ohne Kapitel-Kontext (Aufgaben-Seiten, Antwort-Previews)
  //      → keine Nummer; labelte Sections bekommen trotzdem ihren Anker.
  const chapterNum = chapterRef && chapterRef.num != null ? chapterRef.num : null;
  const secLabelNumbers = {};
  {
    const frag = document.createElement('div');
    frag.innerHTML = html;
    let n2 = 0;
    let n3 = 0;
    let n4 = 0;
    frag.querySelectorAll('h2, h3, h4').forEach((h) => {
      let num = null;
      if (h.tagName === 'H2') {
        n2 += 1; n3 = 0; n4 = 0;
        num = chapterNum != null ? `${chapterNum}.${n2}` : null;
      } else if (h.tagName === 'H3') {
        n3 += 1; n4 = 0;
        num = chapterNum != null ? `${chapterNum}.${n2}.${n3}` : null;
      } else {
        n4 += 1;
        num = chapterNum != null ? `${chapterNum}.${n2}.${n3}.${n4}` : null;
      }
      let label = null;
      const lm = h.innerHTML.match(/\s*\{#sec:([\p{L}0-9_-]+)\}\s*$/u);
      if (lm) {
        label = lm[1];
        h.innerHTML = h.innerHTML.slice(0, lm.index).replace(/\s+$/, '');
        if (num != null) secLabelNumbers[label] = num;
      }
      if (label) {
        h.id = `sec:${label}`;
      } else if (num != null && sectionId != null) {
        h.id = `sec:${sectionId}-${num}`;
      }
      if (num != null) {
        const span = document.createElement('span');
        span.className = 'tutorai-sec-num';
        span.textContent = num;
        const space = document.createTextNode('\u00a0');
        h.insertBefore(span, h.firstChild);
        h.insertBefore(space, span.nextSibling);
      }
    });
    html = frag.innerHTML;
  }

  // 6b. Restore cross-references (@fig:label / @eq:label / @sec:label;
  //     @kap:label = Legacy-Alias für @sec:label)
  //     Auflösung: 1) in diesem Dokument → In-Page-Anker,
  //               2) refmap → direkter Link zum Objekt (#fig:label / #eq:label / #sec:label)
  //                  auf der Skript-Seite (alle Rollen; das Kapitel wird dort aufgeklappt);
  //                  Kapitel-Labels (chapter: true) verlinken auf #chapter-{id} und
  //                  werden als „Kap. N“ angezeigt,
  //               3) unbekannt → ❓
  xrefs.forEach((x, idx) => {
    const kind = x.kind === 'kap' ? 'sec' : x.kind;
    const local =
      kind === 'fig' ? figLabelNumbers[x.label]
      : kind === 'eq' ? eqLabelNumbers[x.label]
      : secLabelNumbers[x.label];
    const g = globalLabels[x.label];
    let refHtml;
    if (local) {
      const text = kind === 'fig' ? 'Abb.' : kind === 'eq' ? 'Gl.' : 'Abs.';
      refHtml = `<a href="#${kind}:${x.label}" class="tutorai-xref">${text} ${local}</a>`;
    } else if (g && g.kind === kind) {
      const cid = (refMap && refMap.courseId) || '';
      const text = g.chapter ? 'Kap.' : kind === 'fig' ? 'Abb.' : kind === 'eq' ? 'Gl.' : 'Abs.';
      const anchor = g.chapter ? `chapter-${g.sectionId}` : `${kind}:${x.label}`;
      refHtml = `<a href="/courses/${cid}/script#${anchor}" class="tutorai-xref">${text} ${g.num}</a>`;
    } else {
      refHtml = `<span class="tutorai-xref-broken" title="Label unbekannt — zugehöriges Objekt fehlt">❓ ${x.kind}:${x.label}</span>`;
    }
    html = html.replace(`%%XREF_${idx}%%`, refHtml);
  });

  // 6c. Restore task references (@task:{id})
  //     Daten via refmap.tasks: Student (mode "reading") → nur freigeschaltete
  //     Aufgaben inkl. eigener Punkte/Medaille (analog Aufgabenübersicht);
  //     PROF/TUTOR/Admin (mode "edit") → alle Aufgaben, kompakte Box mit Link.
  const refTasks = (refMap && refMap.tasks) || {};
  const taskCid = (refMap && refMap.courseId) || '';
  taskRefs.forEach((id, idx) => {
    const t = refTasks[id];
    let boxHtml;
    if (!t) {
      boxHtml = `<span class="tutorai-xref-broken" title="Aufgabe unbekannt — existiert nicht (mehr) oder ist nicht freigeschaltet">❓ Aufgabe ${escapeHtml(id)}</span>`;
    } else if (refMap && refMap.mode === 'edit') {
      // PROF/TUTOR/Admin: kompakte Box mit Link zur Aufgabenseite
      boxHtml =
        `<div class="tutorai-taskbox">` +
        `<span class="tutorai-taskbox-icon" aria-hidden="true">📝</span>` +
        `<div class="flex-1 min-w-0">` +
        `<a href="/courses/${taskCid}/tasks/${t.id}" class="tutorai-xref font-semibold">${escapeHtml(t.title)}</a>` +
        `<div class="text-sm text-gray-500 mt-0.5">${t.maxPoints} Punkte · ${t.taskType === 'code' ? '💻 Code-Aufgabe' : '📄 Text-Aufgabe'}</div>` +
        `</div></div>`;
    } else {
      // Student: Karte analog zur Aufgabenübersicht (Punkte, Medaille, Versuche, Deadline)
      const pct = t.maxPoints > 0 ? t.myPoints / t.maxPoints : 0;
      let pointColor = 'text-gray-400';
      if (pct >= 0.8) pointColor = 'text-green-600';
      else if (pct >= 0.5) pointColor = 'text-yellow-600';
      else if (t.myPoints > 0) pointColor = 'text-orange-600';
      let medalBadge = '';
      if (pct >= 1.0) medalBadge = '<span class="badge-tier badge-platinum">💎</span>';
      else if (pct >= 0.9) medalBadge = '<span class="badge-tier badge-gold">🥇</span>';
      else if (pct >= 0.8) medalBadge = '<span class="badge-tier badge-silver">🥈</span>';
      else if (pct >= 0.7) medalBadge = '<span class="badge-tier badge-bronze">🥉</span>';
      let deadlineHtml = '';
      if (t.deadline) {
        const dl = new Date(t.deadline).toLocaleDateString('de-DE');
        deadlineHtml = ` · ⏰ Deadline ${dl}`;
      }
      const typeBadge = t.taskType === 'code'
        ? '<span class="px-2 py-0.5 rounded text-xs font-medium bg-orange-100 text-orange-700">💻 Code</span>'
        : '<span class="px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-600">📄 Text</span>';
      boxHtml =
        `<div class="tutorai-taskbox">` +
        `<span class="tutorai-taskbox-icon" aria-hidden="true">📝</span>` +
        `<div class="flex-1 min-w-0">` +
        `<a href="/courses/${taskCid}/tasks/${t.id}" class="tutorai-xref text-base font-semibold">${escapeHtml(t.title)}</a>` +
        `<div class="text-sm text-gray-500 mt-1">` +
        `${t.attemptsUsed}${t.maxAttempts != null ? '/' + t.maxAttempts : ''} Versuche${deadlineHtml}&nbsp;${typeBadge}${medalBadge ? '&nbsp;&nbsp;&nbsp;&nbsp;' + medalBadge : ''}` +
        `</div></div>` +
        `<div class="text-right flex-shrink-0">` +
        `<div class="text-lg font-bold ${pointColor}">${t.myPoints}/${t.maxPoints}</div>` +
        `<div class="text-xs text-gray-400">Punkte</div>` +
        `</div></div>`;
    }
    // $ in der Ersatz-String escapen (String.replace-Backrefs), wie bei Figuren/Latex
    html = html.replace(`%%TASKREF_${idx}%%`, boxHtml.replace(/\$/g, '$$$$$$$$'));
  });

  // 7. Decode escaped dollar signs back to literal $
  html = html.replace(new RegExp(escapedDollar, 'g'), '$');

  // 8. Apply syntax highlighting
  html = highlightCodeBlocks(html);

  // 9. Decode HTML entities in non-code text
  html = decodeTextEntities(html);

  // 10. Sanitize with DOMPurify
  //     (ADD_TAGS/ADD_ATTR: Applet-Iframes werden sonst komplett entfernt —
  //     iframe steht nicht in der Default-Allow-List von DOMPurify)
  if (typeof DOMPurify !== 'undefined') {
    html = DOMPurify.sanitize(html, { ADD_TAGS: ['iframe'], ADD_ATTR: ['sandbox'] });
    // Sicherheitsnetz: Applet-Iframes dürfen IMMER nur sandboxed laufen
    // (allow-scripts, ohne allow-same-origin). Falls DOMPurify das sandbox-
    // Attribut entfernt, wird es neu eingefügt.
    html = html.replace(/<iframe(?![^>]*\bsandbox=)([^>]*)>/g, (_m, attrs) => {
      return '<iframe sandbox="allow-scripts"' + attrs.replace(/\/\s*$/, '') + '>';
    });
  }

  // 11. Render Mermaid diagrams
  if (mermaidBlocks.length > 0 && typeof mermaid !== 'undefined') {
    const renderedDiagrams = await Promise.all(mermaidBlocks.map((diagram) => {
      return renderMermaid(diagram);
    }));
    renderedDiagrams.forEach((svg, idx) => {
      html = html.replace(`%%MERmaid_BLOCK_${idx}%%`, svg);
    });
  }

  targetElement.innerHTML = `<div class="markdown-preview">${html}</div>`;
  _cleanupBlockArtifacts(targetElement);
  if (slideMode) _applyFragmentMarkers(targetElement);
}

// Byproducts aufräumen: Figure-/Applet-/Code-/Taskbox-Placeholders sind
// Inline-Tokens — marked (breaks:true) erzeugt aus „Token-Zeile + Textzeile
// (ohne Leerzeile dazwischen)“ ein <p>…<br>TOKEN<br>…</p>. Beim
// innerHTML-Parsen schließen <figure>/<pre>/<div> das umgebende <p>
// IMPLIZIT (HTML-Parser-Regel), sodass (a) ein <p>-Rest mit trailing <br>
// vor dem Block bleibt und (b) das <br> + der Folgetext (inkl. Inline-
// Elemente wie <code>/Katex-Spans) als Direktkinder der .markdown-preview
// landen. Aufräumen:
//   (1) Top-Level-<br> entfernen (erzeugen je eine Zeile Abstand),
//   (2) laufende Inline-Läufe (Text + inline gerenderte Elemente) in
//       EINEM <p> hüllen — würde jeder Textknoten sein eigenes <p>
//       bekommen, bricht der Satz an den Inline-Elementen um (falsche
//       Zeilenumbrüche um Formeln/`<code>`),
//   (3) leere Top-Level-<p> entfernen,
//   (4) trailing <br> am Ende eines <p> entfernen, wenn danach (über
//       Whitespace hinweg) ein Block-Element folgt (Byproduct-Lücke vor
//       dem Figure-/Code-Block).
// Legitime <br> (z. B. in Listen oder zwischen zwei Zeilen desselben
// Absatzes) liegen nie als Direktkind der Preview und bleiben erhalten.
function _cleanupBlockArtifacts(root) {
  const clean = (mp) => {
    // (1) Top-Level-<br> entfernen
    for (const br of Array.from(mp.querySelectorAll(':scope > br'))) br.remove();

    // (2) Top-Level-Inline-Läufe in <p> gruppieren.
    // Tag-basiert statt getComputedStyle: beim Render sind die
    // Reveal-Sections noch detached (bzw. display:none) — für nicht
    // eingebundene Elemente liefert getComputedStyle keine aufgelösten
    // Werte (display="" → kein "inline") → Inline-Elemente blieben
    // ungruppiert und Absätze zerfielen (nur im Skript, wo der Container
    // im DOM hängt, griff die computed-style-Variante).
    const isBlockTag = /^(?:P|DIV|PRE|FIGURE|TABLE|UL|OL|BLOCKQUOTE|H[1-6]|HR|SECTION|ARTICLE|ASIDE|DETAILS|FORM|FIELDSET|BR|VIDEO|AUDIO|IFRAME)$/;
    const isInlineEl = (node) =>
      node.nodeType === Node.ELEMENT_NODE && !isBlockTag.test(node.tagName);
    let run = [];
    const flush = () => {
      if (!run.length) return;
      const p = document.createElement('p');
      mp.insertBefore(p, run[0]);
      for (const n of run) p.appendChild(n);
      run = [];
    };
    for (const node of Array.from(mp.childNodes)) {
      if (node.nodeType === Node.TEXT_NODE) {
        // Whitespace-Text ohne aktiven Lauf: unsichtbar → stehen lassen.
        if (node.textContent.trim() || run.length) run.push(node);
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        if (isInlineEl(node)) run.push(node);
        else flush();
      }
    }
    flush();

    // (3) Leere Top-Level-<p> entfernen
    for (const p of Array.from(mp.querySelectorAll(':scope > p'))) {
      if (!p.childElementCount && !p.textContent.trim()) p.remove();
    }

    // (4) Trailing <br> am <p>-Ende entfernen, wenn danach ein Block-Element
    // folgt (das <br> ist das Byproduct der Token-Zeile, nicht ein
    // absichtlicher Zeilenumbruch).
    const isBlockEl = (el) => !!(el && isBlockTag.test(el.tagName));
    for (const p of Array.from(mp.querySelectorAll(':scope > p'))) {
      let sib = p.nextSibling;
      while (sib && sib.nodeType === Node.TEXT_NODE && !sib.textContent.trim()) {
        sib = sib.nextSibling;
      }
      if (!sib || sib.nodeType !== Node.ELEMENT_NODE || !isBlockEl(sib)) continue;
      while (p.lastChild) {
        const n = p.lastChild;
        if (n.nodeType === Node.ELEMENT_NODE && n.tagName === 'BR') {
          n.remove();
        } else if (n.nodeType === Node.TEXT_NODE && !n.textContent.trim()) {
          n.remove();
        } else {
          break;
        }
      }
    }
  };
  root.querySelectorAll('.markdown-preview').forEach(clean);
}

// Fragment-Marker (Reveal) auflösen: {#fragment}, {#fragment:id} (ID-Gruppe),
// {#Fragment} (Gate), {#aaid:label} (Auto-Animate-Element-ID → data-id) und
// \htmlClass{fragment…} in Formeln (s. _katexFragRewrite). Der Sentinel ist
// ein Inline-<span>, das marked in das nächste Block-Element einbettet;
// eq/fig tragen data-frag/data-id bereits aus der Restore-Phase. Phasen:
//   1: Sentinel → umgebendes Block-Element (p/li/h*/pre/…) als Host
//      (data-frag/data-frag-id bzw. data-id vom Span auf den Host kopiert;
//      Sonderfälle: leeres <p> nach Codeblock, Marker direkt im Wrapper).
//   1b: \htmlClass{fragment…}-Spans in gerenderten Formeln als Fragment-
//      Hosts markieren (group/normal aus der ID-Klasse) — die inerten
//      tutorai-katex-frag*-Klassen werden entfernt, „enclosing“ bleibt.
//   2: Alle [data-frag]-Elemente + Top-Level-Elemente der Folie (direkte
//      Kinder der .markdown-preview-Wrapper, bei twocol je ein Wrapper pro
//      Spalte) in Dokumentreihenfolge sammeln.
//   3: Reveal data-fragment-index vergeben (gleicher Index = gleichzeitig
//      eingeblendet): ID-Gruppen teilen sich den Schritt des ersten
//      Vorkommens; ein Gate ("reveal") bekommt einen neuen Schritt, und
//      ALLE nachfolgenden Inhalte der Folie (Dokumentreihenfolge) ohne
//      eigenes Fragment davor bekommen denselben Schritt — explizite
//      Fragments nach dem Gate werden also vom Gate "verschlungen"
//      (ein späteres Gate übertrumpft ein früheres).
//   4: data-frag*-Attribute entfernen ("fragment"-Klasse + Index bleiben;
//      data-id bleibt — es ist Reveal's Auto-Animate-Matching-Key).
function _applyFragmentMarkers(container) {
  const blockRe = /^(P|LI|H[1-6]|PRE|BLOCKQUOTE|FIGURE|TABLE|DIV)$/;
  const isPreviewWrap = (el) => el && el.classList && el.classList.contains('markdown-preview');

  // Phase 1: Sentinel auflösen (Fragmente + Auto-Animate-IDs)
  container.querySelectorAll('span.tutorai-frag-marker, span.tutorai-aaid-marker').forEach((span) => {
    const isAaid = span.classList.contains('tutorai-aaid-marker');
    const fragType = span.getAttribute('data-frag') || 'normal';
    const fragId = span.getAttribute('data-frag-id');
    const aaid = span.getAttribute('data-aaid');
    let el = span.parentElement;
    while (
      el &&
      el !== container &&
      !isPreviewWrap(el) &&
      !blockRe.test(el.tagName) &&
      !el.classList.contains('tutorai-equation')
    ) {
      el = el.parentElement;
    }
    let host = el && el !== container && !isPreviewWrap(el) ? el : null;
    // Leeres <p> direkt nach einem Block (z. B. Codeblock): Marker → Block davor
    if (host && host.tagName === 'P' && !host.textContent.trim() && host.previousElementSibling) {
      host = host.previousElementSibling;
    }
    if (!host) {
      // Marker steht direkt im Wrapper (Codeblock ohne Leerzeile): vorheriges
      // Block-Element nehmen, <br> von marked (breaks:true) überspringen.
      let sib = span.previousElementSibling;
      while (sib && sib.tagName === 'BR') sib = sib.previousElementSibling;
      host = sib && blockRe.test(sib.tagName) ? sib : null;
    }
    if (host) {
      if (isAaid) {
        // Auto-Animate-Element-ID (bleibt dauerhaft, s. Phase-4-Kommentar)
        host.setAttribute('data-id', aaid);
      } else {
        host.setAttribute('data-frag', fragType);
        if (fragId) host.setAttribute('data-frag-id', fragId);
      }
    }
    // Vom Sentinel getrenntes <br> (marked, breaks:true) wieder entfernen
    if (span.previousElementSibling && span.previousElementSibling.tagName === 'BR') {
      span.previousElementSibling.remove();
    }
    span.remove();
  });

  // Phase 1b: \htmlClass{fragment…} in Formeln (via _katexFragRewrite nach
  // tutorai-katex-frag(-id-<label>) umgeschrieben, s. renderLatex*): die
  // Spans als Fragment-Hosts markieren (ID → Gruppe), inerte Klassen
  // entfernen — ab Phase 2 laufen sie wie jedes [data-frag]-Element.
  container.querySelectorAll('span.tutorai-katex-frag').forEach((span) => {
    let fragId = null;
    for (const c of span.classList) {
      if (c.startsWith('tutorai-katex-fragid-')) fragId = c.slice('tutorai-katex-fragid-'.length);
    }
    span.classList.remove('tutorai-katex-frag');
    if (fragId) span.classList.remove('tutorai-katex-fragid-' + fragId);
    span.setAttribute('data-frag', fragId ? 'group' : 'normal');
    if (fragId) span.setAttribute('data-frag-id', fragId);
    // Marker für Phase 2b (visuelle Lesereihenfolge innerhalb der
    // Gleichung), in Phase 4 wieder entfernt.
    span.setAttribute('data-tutorai-katexfrag', '');
  });

  // Phase 2: Fragment-Elemente + Top-Level-Elemente, Dokumentreihenfolge
  const fragEls = Array.from(container.querySelectorAll('[data-frag]'));
  if (fragEls.length === 0) return;
  const allEls = _collectFragmentEls(container, fragEls);

  // Phase 2b: Katex-Fragmente in visueller Lesereihenfolge (s. Hilf).
  // Beim ersten Render sind die Reveal-Sections noch display:none (Rects=0)
  // → no-op; tutoraiResortFragments() macht es später noch einmal.
  _visualKatexRunSort(allEls);

  // Phase 3: Schritt-Indizes
  _assignFragmentSteps(allEls);

  // Phase 4: Die Marker data-frag/data-frag-id/data-tutorai-katexfrag
  // bleiben bewusst stehen: tutoraiResortFragments() (slides.js ruft sie
  // auf ready/slidechanged bzw. bei ?print-pdf auf) läuft dieselbe
  // Ableitung erneut, sobald die Folie gelayoutet ist, und braucht sie.
}

// Fragment-Elemente + Top-Level-Elemente der .markdown-preview-Blöcke in
// Dokumentreihenfolge sammeln (die Gate-Logik in _assignFragmentSteps
// braucht die Top-Level-Elemente).
function _collectFragmentEls(container, fragEls) {
  const topLevel = [];
  container.querySelectorAll('.markdown-preview').forEach((mp) => {
    Array.from(mp.children).forEach((c) => topLevel.push(c));
  });
  const allEls = [...new Set([...fragEls, ...topLevel])];
  allEls.sort((a, b) => {
    const pos = a.compareDocumentPosition(b);
    if (pos & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
    if (pos & Node.DOCUMENT_POSITION_PRECEDING) return 1;
    return 0;
  });
  return allEls;
}

// Katex-Fragment-Elemente in visueller Lesereihenfolge sortieren.
// KaTeX rendert z. B. \underbrace{a+b}_{c} als munder-Vlist, in dem die
// Subscript-Zelle DOM-mäßig VOR der Haupt-Zelle steht — die Schrittnummern
// würden sonst c→a→b statt a→b→c vergeben. Deshalb NUR aufeinanderfolgende
// katex-Frag-Elemente derselben Gleichung (gleiche .katex-Root) per
// Bounding-Box (top, dann left; Toleranz 2px) neu sortieren. Stabiler Sort
// hält bei Ties die DOM-Reihenfolge. Nicht global visuell: würde z. B.
// Twocol-Spaltenreihenfolge brechen. Ungelayoutete Slides (Rects=0) → no-op.
function _visualKatexRunSort(allEls) {
  for (let i = 0; i < allEls.length; ) {
    const first = allEls[i];
    if (!first.hasAttribute('data-tutorai-katexfrag')) { i++; continue; }
    const root = first.closest ? first.closest('.katex') : null;
    let j = i + 1;
    while (
      j < allEls.length &&
      allEls[j].hasAttribute('data-tutorai-katexfrag') &&
      root && allEls[j].closest && allEls[j].closest('.katex') === root
    ) j++;
    if (j - i > 1) {
      const run = allEls.slice(i, j).map((el) => ({ el, r: el.getBoundingClientRect() }));
      run.sort((x, y) => {
        const dTop = x.r.top - y.r.top;
        if (Math.abs(dTop) > 2) return dTop; // erst Zeile
        const dLeft = x.r.left - y.r.left;
        if (Math.abs(dLeft) > 2) return dLeft; // dann Spalte
        return 0; // Tie → DOM-Order (stabil)
      });
      run.forEach((item, k) => { allEls[i + k] = item.el; });
    }
    i = j;
  }
  return allEls;
}

// Fragment-Schritt-Indizes vergeben (Gate-Logik + ID-Gruppen).
function _assignFragmentSteps(allEls) {
  let nextStep = 0;
  let activeGateStep = null;
  const idToStep = {};
  for (const el of allEls) {
    const fragType = el.getAttribute('data-frag');
    const fragId = el.getAttribute('data-frag-id');
    // Ein Top-Level-Element OHNE Marker wird nur fragmentiert, wenn das
    // Gate es verschlingt und KEIN Fragment-Element darin einen eigenen
    // (früheren) Schritt bestimmt.
    const becomesFrag = fragType !== null ||
      (activeGateStep !== null && !el.querySelector('[data-frag]'));
    if (!becomesFrag) continue;

    let step;
    if (fragType === 'reveal') {
      step = nextStep++; // Gate: eigener neuer Schritt
      activeGateStep = step;
    } else if (activeGateStep !== null) {
      step = activeGateStep; // vom Gate verschlungen (auch mit eigenem Marker)
    } else if (fragId && fragId in idToStep) {
      step = idToStep[fragId]; // ID-Gruppe: Schritt des ersten Vorkommens
    } else {
      step = nextStep++;
    }
    if (fragId && !(fragId in idToStep)) idToStep[fragId] = step;
    el.setAttribute('data-fragment-index', String(step));
    el.classList.add('fragment');
  }
}

// Fragment-Schritte neu ableiten, sobald der Container (Folie) gelayoutet
// ist. Beim ersten Render sind die Reveal-Sections noch display:none
// (Reveal-CSS), daher kann die visuelle Run-Sortierung (KaTeX-Underbraces)
// dort nur ein no-op sein. slides.js (wireKatexFragmentResort) ruft das auf
// Reveal's ready/slidechanged (Präsentation/Vorschau) und — im ?print-pdf-
// Modus — synchron auf, sobald html.print-pdf gesetzt ist (noch vor
// Reveal's setupPDF-Fragment-Paginierung). Setzt dieselben Attribute/Klassen
// wie der erste Durchlauf (idempotent).
function tutoraiResortFragments(container) {
  if (!container || !container.querySelectorAll) return;
  const fragEls = Array.from(container.querySelectorAll('[data-frag]'));
  if (fragEls.length === 0) return;
  _assignFragmentSteps(_visualKatexRunSort(_collectFragmentEls(container, fragEls)));
}

// Fragment-Typ aus der Markersyntax: {#Fragment} (Groß, ohne ID) = Gate,
// {#fragment:id}/{#Fragment:id} = ID-Gruppe, {#fragment} = normal.
function _fragType(fchar, id) {
  if (fchar === 'F' && !id) return 'reveal';
  return id ? 'group' : 'normal';
}

// Escape HTML special characters to prevent XSS in error messages
function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Sanitize LaTeX input to prevent HTML/JS injection.
function sanitizeLatex(latex) {
  let prev = '';
  while (prev !== latex) {
    prev = latex;
    latex = latex.replace(/\[HTML\][\s\S]*?\[TeX\]/gi, '');
  }
  latex = latex.replace(/\[HTML\]/gi, '').replace(/\[TeX\]/gi, '');
  latex = latex.replace(/<\/?script[\s>][^>]*>/gi, '');
  latex = latex.replace(/<\s*[a-zA-Z][^>=]*=[^>]*>/gi, '');
  return latex;
}

function renderLatexBlock(latex) {
  try {
    return katex.renderToString(_katexFragRewrite(sanitizeLatex(latex)), {
      displayMode: true,
      throwOnError: false,
      trust: _katexTrust,
    });
  } catch (e) {
    return `<pre class="text-red-500 bg-red-50 p-2 rounded">KaTeX Error: ${escapeHtml(e.message)}</pre>`;
  }
}

function renderLatexInline(latex) {
  try {
    return katex.renderToString(_katexFragRewrite(sanitizeLatex(latex)), {
      displayMode: false,
      throwOnError: false,
      trust: _katexTrust,
    });
  } catch (e) {
    return `<span class="text-red-500">\(${escapeHtml(latex)}\)</span>`;
  }
}

// ─── LaTeX-Fragmente (\fragment{…} bzw. \htmlClass{fragment…}) ───────────
// Formelteile schrittweise einblenden (Slides): \fragment{…} (Kurzform)
// bzw. \htmlClass{fragment}{…}, mit ID: \fragment{id}{…} bzw.
// \htmlClass{fragment:id}{…} (ID-Gruppe = gleichzeitig). KaTeX's
// htmlClass setzt die Klassen ROH auf den Wrapper-Span — aber „fragment"
// wäre Reveal's Fragment-Klasse (falsche Semantik), und „fragment:label"
// ist keine gültige CSS-Klasse → deshalb hier umschreiben auf eigene,
// inerte Klassen. _applyFragmentMarkers (Phase 1b) übernimmt die Spans
// dann als normale Fragment-Hosts (group/normal) und entfernt die
// Klassen; „enclosing“ (KaTeX-Styling) bleibt. Im Skript (ohne slideMode)
// bleiben die Spans inaktive Elemente — kein visueller Unterschied.
const KATEX_FRAG_CLASS = 'tutorai-katex-frag';
const KATEX_FRAG_ID_PREFIX = 'tutorai-katex-fragid-';

function _katexFragRewrite(latex) {
  return latex
    // Kurzform \fragment{…}: zuerst die ID-Variante (zwei Argumente:
    // {id} + {content}, erkennbar am { direkt nach der ID-Gruppe), dann
    // die plain-Variante als reiner Prefix-Tausch (Content bleibt
    // unangetastet, auch mit geschachtelten Klammern).
    .replace(/\\fragment\{([\p{L}0-9_-]+)\}(?=\{)/gu, '\\htmlClass{fragment:$1}')
    .replace(/\\fragment\{/g, '\\htmlClass{fragment}{')
    .replace(/\\htmlClass\{fragment:([\p{L}0-9_-]+)\}/gu, '\\htmlClass{' + KATEX_FRAG_CLASS + ' ' + KATEX_FRAG_ID_PREFIX + '$1}')
    .replace(/\\htmlClass\{fragment\}/g, '\\htmlClass{' + KATEX_FRAG_CLASS + '}');
}

// KaTeX-Trust-Funktion (ersetzt trust:false): erlaubt NUR \htmlClass mit
// genau unseren Fragment-Klassen — alle anderen Trust-Kommandos
// (\htmlClass mit fremden Klassen, \href, \url, \includegraphics,
// \htmlId/\htmlStyle/\htmlData) bleiben abgelehnt (roter
// Unsupported-Command-Text, Rest der Formel rendert normal).
const KATEX_FRAG_TRUST_RE = new RegExp('^' + KATEX_FRAG_CLASS + '( ' + KATEX_FRAG_ID_PREFIX + '[\\p{L}0-9_-]+)?$', 'u');
function _katexTrust(ctx) {
  return !!ctx && ctx.command === '\\htmlClass' && KATEX_FRAG_TRUST_RE.test(String(ctx.class || ''));
}

// Syntax-Highlighting (hljs, global aus base.html): alle Sprachen mit
// language-<lang>-Klasse; ohne/unknown Sprache = Auto-Detection. Die
// übrigen Attribute (data-line-numbers für Slides) überleben den Rebuild;
// das class-Attribut wird neu gesetzt (Sprache + hljs-Markierung).
// Im Slide-Modus überstreicht das Reveal-Highlight-Plugin das Ergebnis
// idempotent (gleiche hljs-Version) und baut zusätzlich die
// data-line-numbers-Tabelle/Fragmente auf.
function highlightCodeBlocks(html) {
  // <pre> darf Attribute tragen (data-id für {#aaid:…}-Code-Blöcke) — die
  // überleben den Rebuild unverändert (preAttrs).
  return html.replace(/<pre([^>]*)><code([^>]*)>([\s\S]*?)<\/code><\/pre>/g, (match, preAttrs, attrs, code) => {
    const decoded = code
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&amp;/g, '&')
      .replace(/&#39;/g, "'")
      .replace(/&quot;/g, '"');

    const otherAttrs = attrs.replace(/\s*class="[^"]*"/, '').trim();
    const langMatch = /class="language-([\w+-]+)"/.exec(attrs);
    const language = langMatch ? langMatch[1] : null;

    if (typeof hljs !== 'undefined') {
      let highlighted = null;
      try {
        const src = decoded.trim();
        if (src) {
          highlighted = (language && hljs.getLanguage(language))
            ? hljs.highlight(src, { language }).value
            : hljs.highlightAuto(src).value;
        }
      } catch (e) {
        highlighted = null; // Fallback unten: escaped, unverändert
      }
      if (highlighted !== null) {
        const cls = (language ? `language-${language} ` : '') + 'hljs';
        return `<pre${preAttrs}><code${otherAttrs ? ` ${otherAttrs}` : ''} class="${cls}">${highlighted}</code></pre>`;
      }
    }

    const escaped = decoded
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    return `<pre${preAttrs}><code${attrs}>${escaped}</code></pre>`;
  });
}

function decodeTextEntities(html) {
  const parts = html.split(/(<pre[^>]*>[\s\S]*?<\/pre>|<span class="katex(?:-display)?">[\s\S]*?<\/span>|<div class="mermaid-diagram">[\s\S]*?<\/div>|<[^>]*>)/g);
  return parts.map((part, i) => {
    if (i % 2 === 0) {
      return part.replace(/&lt;/g, '<').replace(/&gt;/g, '>');
    }
    return part;
  }).join('');
}

// ─── Mermaid Diagram Rendering ──────────────────────────────────────────

/**
 * Mermaid-Flowcharts: Knotentext mit Sonderzeichen (z.B. `C[H(X) = log2(n)]`)
 * ist ohne Anführungszeichen nicht parsebar. Für Flowcharts werden
 * unquoted [..]/{..}-Labels automatisch angeführt: C["H(X) = log2(n)"].
 * Bereits angeführte Labels sowie andere Diagrammtypen
 * (Sequence, Class, ER, …) bleiben unverändert.
 */
function sanitizeFlowchartLabels(text) {
  const firstLine = text.split('\n').map(l => l.trim()).find(l => l && !l.startsWith('%%')) || '';
  if (!/^(graph|flowchart)\b/i.test(firstLine)) return text;
  return text
    .replace(/\b([A-Za-z0-9_][A-Za-z0-9_-]*)\[([^\[\]"]*)\]/g, (m, id, label) => {
      return /[(){}<>]/.test(label) ? `${id}["${label}"]` : m;
    })
    .replace(/\b([A-Za-z0-9_][A-Za-z0-9_-]*)\{([^\{\}"]*)\}/g, (m, id, label) => {
      return /[(){}<>]/.test(label) ? `${id}{"${label}"}` : m;
    });
}

async function renderMermaid(diagramText) {
  if (typeof mermaid === 'undefined') {
    return `<pre class="text-orange-500 bg-orange-50 p-2 rounded">Mermaid not loaded</pre>`;
  }

  diagramText = sanitizeFlowchartLabels(diagramText);

  try {
    const { svg } = await mermaid.render(`mermaid-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, diagramText);
    const temp = document.createElement('div');
    temp.innerHTML = svg;
    const svgEl = temp.querySelector('svg');
    const viewBox = svgEl?.getAttribute('viewBox');
    const parts = viewBox?.split(/\s+/);
    const svgWidth = parts && parts.length >= 3 ? parseFloat(parts[2]) : 800;
    svgEl?.setAttribute('width', `${Math.round(svgWidth)}px`);
    svgEl?.removeAttribute('height');
    svgEl?.setAttribute('style', 'max-width:100%;height:auto;display:block');
    return `<div class="mermaid-diagram flex justify-center">${temp.innerHTML}</div>`;
  } catch (e) {
    return `<pre class="text-red-500 bg-red-50 p-2 rounded">Mermaid Error: ${escapeHtml(e.message)}\n\n${escapeHtml(diagramText)}</pre>`;
  }
}

/**
 * Create a Markdown editor with a toggle between edit and preview mode.
 */
function createMarkdownEditor(containerId, options = {}) {
  const container = document.getElementById(containerId);
  if (!container) {
    console.warn(`createMarkdownEditor: Container #${containerId} not found`);
    return null;
  }

  const textarea = (options.textareaId
    ? (document.getElementById(options.textareaId) || null)
    : container.querySelector('textarea'));
  if (!textarea) {
    console.warn(`createMarkdownEditor: No textarea found in #${containerId}`);
    return null;
  }

  const previewDiv = document.createElement('div');
  previewDiv.className = 'markdown-preview-area hidden min-h-[200px] border border-gray-300 rounded-lg p-4 bg-white overflow-y-auto';

  const toggleBtn = document.createElement('button');
  toggleBtn.type = 'button';
  toggleBtn.className = 'text-gray-500 hover:text-gray-700 text-sm px-3 py-1.5 rounded-lg border border-gray-300 hover:bg-gray-50 transition inline-flex items-center gap-1.5';
  toggleBtn.innerHTML = '<span>👁️</span> <span>Preview</span>';

  textarea.parentNode.insertBefore(previewDiv, textarea.nextSibling);

  const anchorId = options.buttonAnchor;
  if (anchorId) {
    const anchorEl = document.getElementById(anchorId);
    if (anchorEl) {
      anchorEl.appendChild(toggleBtn);
    } else {
      toggleBtn.style.marginTop = '0.5rem';
      textarea.parentNode.insertBefore(toggleBtn, textarea.nextSibling);
    }
  } else {
    toggleBtn.style.marginTop = '0.5rem';
    textarea.parentNode.insertBefore(toggleBtn, textarea.nextSibling);
  }

  let isPreview = false;

  // Markdown-Optionen für Preview + Auto-Update (sectionId → globale Nummerierung)
  const mdRenderOptions = { preview: true, sectionId: options.sectionId || null };

  // Preview rendern; options.onPreviewRender wird danach aufgerufen
  // (z. B. um Skript-Fragen-Markierungen im Edit-Modus neu anzuwenden)
  const renderPreview = async () => {
    await renderMarkdown(textarea.value, previewDiv, mdRenderOptions).catch(() => {});
    if (options.onPreviewRender) options.onPreviewRender();
  };

  toggleBtn.addEventListener('click', () => {
    isPreview = !isPreview;
    if (isPreview) {
      textarea.classList.add('hidden');
      previewDiv.classList.remove('hidden');
      toggleBtn.innerHTML = '<span>✏️</span> <span>Edit</span>';
      toggleBtn.classList.add('bg-blue-50', 'border-blue-300', 'text-blue-700');
      renderPreview();
    } else {
      previewDiv.classList.add('hidden');
      textarea.classList.remove('hidden');
      toggleBtn.innerHTML = '<span>👁️</span> <span>Preview</span>';
      toggleBtn.classList.remove('bg-blue-50', 'border-blue-300', 'text-blue-700');
    }
  });

  let updateTimeout = null;
  textarea.addEventListener('input', () => {
    if (isPreview) {
      clearTimeout(updateTimeout);
      updateTimeout = setTimeout(renderPreview, 300);
    }
    if (options.onValueChange) {
      options.onValueChange(textarea.value);
    }
  });

  // Optional: direkt im Preview-Modus starten (z.B. für LLM-generierte Inhalte)
  if (options.startInPreview) {
    toggleBtn.click();
  }

  return { textarea, previewDiv, toggleBtn, isPreview: () => isPreview };
}
