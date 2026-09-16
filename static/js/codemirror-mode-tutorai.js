// CodeMirror-Overlay-Mode für TutorAI-Annotationen (Markdown-Editoren)
//
// Definiert den Overlay-Mode „tutorai-annotations“ sowie den kombinierten
// Modus „tutorai-markdown“ (GFM/Markdown-Basis + Annotationen, via
// CodeMirror.overlayMode; Addon: addon/mode/overlay). Die Editoren
// initialisieren mit `mode: 'tutorai-markdown'`.
//
// Hervorgehoben:
//  - LaTeX-Formeln: $...$ und $$...$$ (auch mehrzeilig)
//  - Boxen: @startbox:<typ> … @endbox
//  - Spalten: @startcolumn[:gewicht] … @nextcolumn[:gewicht] … @endcolumn
//  - Labels/Metadaten: {#typ:label}, {#fragment}, {#aaid:…}, {#lines:…},
//    {.zoom=1.5}, {.height=300}, {zoom=0.8}
//  - Querverweise: @typ:label (@eq:, @fig:, @box:, @sec:, @code:, @tab:, …)
//
// Fenced-Codeblöcke und Inline-Code werden übersprungen, damit dort keine
// falschen Treffer entstehen. Labels auf der ÖFFNENDEN Fence-Zeile
// (z. B. ```python {#code:label}[Caption]) werden weiterhin hervorgehoben.

(function () {
  'use strict';
  if (typeof CodeMirror === 'undefined') return;

  const FENCE_OPEN = /^[ \t]*(?:`{3,}|~{3,})/;
  const FENCE_CLOSE = /^[ \t]*(?:`{3,}|~{3,})[ \t]*$/;
  const BACKTICKS = /`+/;

  // Achtung: Dieser CodeMirror-Build hat KEINE StringStream.skipLine()!
  // Zeilenende wird daher immer per `stream.pos = stream.string.length`
  // gesetzt (eine Exception im Token würde CMs Update-Operation
  // unterbrechen → Display-Desync + doppelte Eingaben).

  // stream.match(regex) ist eine Falle: Es führt pattern.exec() auf der
  // GANZEN Zeile aus und akzeptiert den Treffer nur, wenn er exakt an der
  // aktuellen Position beginnt (leftmost-match-Regel). Ab dem zweiten
  // Vorkommen eines Musters pro Zeile würde es daher immer fehlschlagen.
  // Deshalb explizit an der aktuellen Position matchen:
  function matchAt(stream, pattern) {
    if (typeof pattern === 'string') return stream.match(pattern);
    var m = pattern.exec(stream.string.slice(stream.pos));
    if (m && m.index === 0) {
      stream.pos += m[0].length;
      return m[0];
    }
    return null;
  }

  CodeMirror.defineMode('tutorai-annotations', function () {
    return {
      startState: function () {
        return { blockMath: false, fence: false, inlineCode: false };
      },
      // Pflicht: State wird in-place gemutet → pro Zeile kopieren,
      // sonst teilen sich Zeilen-Zustände das gleiche Objekt.
      copyState: function (state) {
        return { blockMath: state.blockMath, fence: state.fence, inlineCode: state.inlineCode };
      },
      token: function (stream, state) {
        // Fenced-Codeblock: Zeile für Zeile überspringen (das Ende wird
        // erkannt, die öffnende Zeile wird normal weiter tokenisiert,
        // damit dort ggf. stehende Labels hervorgehoben werden).
        if (state.fence && stream.sol()) {
          state.fence = !matchAt(stream, FENCE_CLOSE);
          stream.pos = stream.string.length;
          return null;
        }
        // Inline-Code-Span: bis zum schließenden Backtick überspringen.
        // (Markdown-Inline-Code spannt nie mehrere Zeilen: am Zeilenanfang
        // zurücksetzen, damit ein einzelnes Backtick in der Folge keine
        // ganzen weiteren Zeilen „verschlingt“.)
        if (state.inlineCode) {
          if (stream.sol()) state.inlineCode = false;
          else if (matchAt(stream, BACKTICKS)) {
            state.inlineCode = false;
            return null;
          } else {
            stream.pos = stream.string.length;
            return null;
          }
        }
        // Mehrzeilige Formel $$…$$: bis zum schließenden $$
        if (state.blockMath) {
          if (matchAt(stream, /^\s*[^$]*\$\$/)) state.blockMath = false;
          else stream.pos = stream.string.length;
          return 'ta-math';
        }
        // Block-/Display-Formel $$…$$ (schließt ggf. auf derselben Zeile)
        if (matchAt(stream, '$$')) {
          if (!matchAt(stream, /[^$]*\$\$/)) {
            state.blockMath = true;
            stream.pos = stream.string.length;
          }
          return 'ta-math';
        }
        // Inline-Formel $…$ (nur innerhalb einer Zeile)
        if (matchAt(stream, /\$[^$\n]*\$/)) return 'ta-math';
        // Fenced-Codeblock-Öffnung (Zeilenanfang, ggf. eingerückt)
        if (stream.sol() && matchAt(stream, FENCE_OPEN)) {
          state.fence = true;
          return null;
        }
        // Inline-Code-Öffnung
        if (matchAt(stream, BACKTICKS)) {
          state.inlineCode = true;
          return null;
        }
        // Box-Marker (Label/Caption auf der @startbox:-Zeile folgen als
        // eigene Tokens und werden von den Regeln darunter erfasst)
        if (matchAt(stream, /@startbox(?::[\w-]+)?/)) return 'ta-box';
        if (matchAt(stream, /@endbox\b/)) return 'ta-box';
        // Spalten-Marker (vor der generischen @typ:label-Regel, die
        // @startcolumn:2 andernfalls als ta-ref erfassen würde)
        if (matchAt(stream, /@startcolumn(?::[0-9]+(?:\.[0-9]+)?)?/)) return 'ta-box';
        if (matchAt(stream, /@nextcolumn(?::[0-9]+(?:\.[0-9]+)?)?/)) return 'ta-box';
        if (matchAt(stream, /@endcolumn\b/)) return 'ta-box';
        // Labels/Metadaten: {#typ:label}, {#fragment}, {.zoom=1.5}, …
        if (matchAt(stream, /\{[#.][^}\n]*\}/)) return 'ta-label';
        if (matchAt(stream, /\{zoom=\d+(?:\.\d+)?\}/)) return 'ta-label';
        // Querverweise @typ:label (erfasst auch @boxcolor:<farbe>)
        if (matchAt(stream, /@[A-Za-z_][\w-]*:[\w./()#-]+/)) return 'ta-ref';
        // Fließtext: ganze Abschnitte in einem Zug überspringen,
        // damit die Tokenisierung nicht zeichenweise läuft
        if (stream.eatWhile(/[^$`@{]/)) return null;
        stream.next();
        return null;
      },
    };
  });

  // Kombinierte Modus für die Markdown-Editoren: GFM/Markdown-Basis +
  // TutorAI-Annotationen-Overlay. (Der Mode-Spec-Key „overlay“ wird vom
  // CM5-Core NICHT unterstützt — der Wrapper wird per CodeMirror.overlayMode
  // gebildet; das Addon addon/mode/overlay muss geladen sein.)
  CodeMirror.defineMode('tutorai-markdown', function (config) {
    var baseName = CodeMirror.modes.gfm ? 'gfm' : 'markdown';
    try {
      return CodeMirror.overlayMode(
        CodeMirror.getMode(config, { name: baseName }),
        CodeMirror.getMode(config, { name: 'tutorai-annotations' })
      );
    } catch (e) {
      return CodeMirror.getMode(config, { name: baseName });
    }
  });
})();
