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
 * Applet-Abbildung:   ![caption](src.html)         → interaktives (sandboxed) Iframe;
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
};

// ─── Applet-Auto-Sizing ───────────────────────────────────────────────
// Applets (.html-Medien) melden ihre Inhaltshöhe per postMessage — das
// Boilerplate wird serverseitig in jeden Applet-Versand injiziert (siehe
// serve_media in main.py). Das Iframe passt sich an (150–600 px);
// darüber hinaus scrollt das Applet im Iframe intern.
const APPLET_MIN_H = 150;
const APPLET_MAX_H = 600;
window.addEventListener('message', (event) => {
  const d = event.data;
  if (!d || d.source !== 'tutorai-applet' || typeof d.height !== 'number' || !isFinite(d.height)) return;
  const h = Math.round(Math.min(Math.max(d.height + 8, APPLET_MIN_H), APPLET_MAX_H));
  document.querySelectorAll('iframe.tutorai-applet').forEach((f) => {
    if (f.contentWindow === event.source) {
      f.style.height = h + 'px';
    }
  });
});

async function renderMarkdown(text, targetElement, options = {}) {
  if (!text || typeof text !== 'string') {
    targetElement.innerHTML = '';
    return;
  }

  const { preview = false, sectionId = null } = options;

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

  // 1e. Extract labeled figures: ![caption](src){#fig:label}
  //     → nummerierte Abbildung ("Abb. N") mit Anker, latex-artig verlinkbar.
  //     Bilder ohne {#fig:…} bleiben unverändert (abwärtskompatibel).
  //     Nummerierung: global (kursweit) via refmap; neue (noch ungespeicherte)
  //     Labels bekommen Fallback-Nummern nach der letzten bekannten des Kapitels.
  const figures = [];
  const figLabelNumbers = {};
  const figFallbackBase = chapterRef ? (chapterRef.maxFig || 0) : 0;
  let figFallbackCount = 0;
  processed = processed.replace(
    /!\[([^\]]*)\]\(([^)\s]+)\)\s*\{#fig:([\p{L}0-9_-]+)\}/gu,
    (match, alt, src, label) => {
      let num;
      if (label in figLabelNumbers) {
        num = figLabelNumbers[label]; // Duplikat → erstes Vorkommen gewinnt
      } else {
        const g = globalLabels[label];
        if (g && g.kind === 'fig') {
          num = g.num; // gespeichertes Label → exakte globale Nummer
        } else {
          figFallbackCount += 1;
          num = figFallbackBase + figFallbackCount; // neues (ungespeichertes) Label
        }
        figLabelNumbers[label] = num;
      }
      figures.push({ alt, src, label, num });
      return `%%FIG_${figures.length - 1}%%`;
    }
  );

  // 1e2. Extract unlabeled HTML figures: ![caption](src.html)
  //      → interaktive Vorschau (sandboxed Iframe) OHNE Nummerierung.
  //      Labelierte .html-Medien ({#fig:…}) wurden oben bereits extrahiert.
  const appletFigures = [];
  processed = processed.replace(
    /!\[([^\]]*)\]\(([^)\s]+\.html?)\)/giu,
    (match, alt, src) => {
      appletFigures.push({ alt, src });
      return `%%APPLETFIG_${appletFigures.length - 1}%%`;
    }
  );

  // 1f. Handle escaped dollar signs: \$ → placeholder
  const escapedDollar = '%%ED%%';
  processed = processed.replace(/\\\$/g, escapedDollar);

  // 1g. Extract $$...$$ display blocks (optional trailing {#eq:label}
  //     → nummerierte Gleichung "(N)" mit Anker, latex-artig verlinkbar)
  const latexBlocks = [];
  const latexLabels = [];
  const eqLabelNumbers = {};
  const eqFallbackBase = chapterRef ? (chapterRef.maxEq || 0) : 0;
  let eqFallbackCount = 0;
  processed = processed.replace(
    /\$\$([\s\S]*?)\$\$(?:\s*\{#eq:([\p{L}0-9_-]+)\})?/gu,
    (match, latex, label) => {
      latexBlocks.push(latex.trim());
      if (label && !(label in eqLabelNumbers)) {
        const g = globalLabels[label];
        if (g && g.kind === 'eq') {
          eqLabelNumbers[label] = g.num; // gespeichertes Label → exakte globale Nummer
        } else {
          eqFallbackCount += 1;
          eqLabelNumbers[label] = eqFallbackBase + eqFallbackCount; // neues (ungespeichertes) Label
        }
      }
      latexLabels.push(label || null);
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

  // 2. Render Markdown (marked)
  let html = marked.parse(processed, {
    breaks: true,
    gfm: true,
    headerIds: false,
    mangle: false,
  });

  // 3. Restore fenced code blocks
  fencedCodeBlocks.forEach((content, idx) => {
    const safe = content.replace(escapedDollar, '$');
    const lines = safe.split('\n');
    let language = '';
    let codeBody;
    if (lines.length > 1 && lines[0].trim().match(/^[a-zA-Z][a-zA-Z0-9+-]*$/)) {
      language = lines[0].trim();
      codeBody = lines.slice(1).join('\n');
    } else {
      codeBody = safe;
    }
    const escaped = codeBody.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\$\$/g, '$$$$$$$$');
    const langAttr = language ? ` class="language-${language}"` : '';
    html = html.replace(`%%FC${idx}%%`, `<pre><code${langAttr}>${escaped}</code></pre>`);
  });

  // 4. Restore inline code spans
  inlineCodeSpans.forEach((content, idx) => {
    const safe = content.replace(escapedDollar, '$');
    const escaped = safe.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\$\$/g, '$$$$$$$$');
    html = html.replace(`%%IC${idx}%%`, `<code>${escaped}</code>`);
  });

  // 5. Restore LaTeX blocks (labeled ones as numbered equation "(N)")
  latexBlocks.forEach((latex, idx) => {
    const rendered = renderLatexBlock(latex);
    const label = latexLabels[idx];
    if (label) {
      const wrapped =
        `<div id="eq:${label}" class="tutorai-equation">${rendered}` +
        `<span class="tutorai-eq-num">(${eqLabelNumbers[label]})</span></div>`;
      html = html.replace(`%%LATEX_BLOCK_${idx}%%`, wrapped.replace(/\$/g, '$$$$'));
    } else {
      html = html.replace(`%%LATEX_BLOCK_${idx}%%`, rendered.replace(/\$/g, '$$$$'));
    }
  });

  // 6. Restore inline LaTeX
  latexInlines.forEach((latex, idx) => {
    html = html.replace(`%%LATEX_INLINE_${idx}%%`, renderLatexInline(latex));
  });

  // 6a. Restore numbered figures (.html-Medien als interaktives Iframe)
  figures.forEach((f, idx) => {
    const safeAlt = escapeHtml(f.alt);
    const isApplet = /\.html?$/i.test(f.src);
    const mediaTag = isApplet
      ? `<iframe src="${escapeHtml(f.src)}" class="tutorai-applet" sandbox="allow-scripts" loading="lazy" title="${safeAlt}"></iframe>`
      : `<img src="${escapeHtml(f.src)}" alt="${safeAlt}">`;
    const figHtml =
      `<figure id="fig:${f.label}" class="tutorai-figure">` +
      mediaTag +
      `<figcaption>Abb. ${f.num}${f.alt ? `: ${safeAlt}` : ''}</figcaption></figure>`;
    html = html.replace(`%%FIG_${idx}%%`, figHtml.replace(/\$/g, '$$$$'));
  });

  // 6a2. Restore unlabeled HTML figures (interaktive Vorschau, ohne Nummer)
  appletFigures.forEach((f, idx) => {
    const iframeHtml =
      `<iframe src="${escapeHtml(f.src)}" class="tutorai-applet" sandbox="allow-scripts" loading="lazy" title="${escapeHtml(f.alt)}"></iframe>`;
    html = html.replace(`%%APPLETFIG_${idx}%%`, iframeHtml.replace(/\$/g, '$$$$'));
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

  if (preview) {
    targetElement.innerHTML = `<div class="markdown-preview">${html}</div>`;
  } else {
    targetElement.innerHTML = `<div class="markdown-preview">${html}</div>`;
  }
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
    return katex.renderToString(sanitizeLatex(latex), {
      displayMode: true,
      throwOnError: false,
      trust: false,
    });
  } catch (e) {
    return `<pre class="text-red-500 bg-red-50 p-2 rounded">KaTeX Error: ${escapeHtml(e.message)}</pre>`;
  }
}

function renderLatexInline(latex) {
  try {
    return katex.renderToString(sanitizeLatex(latex), {
      displayMode: false,
      throwOnError: false,
      trust: false,
    });
  } catch (e) {
    return `<span class="text-red-500">\(${escapeHtml(latex)}\)</span>`;
  }
}

function highlightCodeBlocks(html) {
  return html.replace(/<pre><code([^>]*)>([\s\S]*?)<\/code><\/pre>/g, (match, attrs, code) => {
    const decoded = code
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&amp;/g, '&')
      .replace(/&#39;/g, "'")
      .replace(/&quot;/g, '"');

    const isPython = attrs.includes('python') || !attrs.trim();

    if (isPython && typeof hljs !== 'undefined') {
      try {
        const highlighted = hljs.highlight(decoded.trim(), { language: 'python' }).value;
        return `<pre><code class="language-python">${highlighted}</code></pre>`;
      } catch (e) {
        // Fallback: re-escape and return as-is
      }
    }

    const escaped = decoded
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    return `<pre><code${attrs}>${escaped}</code></pre>`;
  });
}

function decodeTextEntities(html) {
  const parts = html.split(/(<pre>[\s\S]*?<\/pre>|<span class="katex(?:-display)?">[\s\S]*?<\/span>|<div class="mermaid-diagram">[\s\S]*?<\/div>|<[^>]*>)/g);
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
