# reveal.js Build (TutorAI)

Erzeugt die **lesbare (nicht minifizierte)** UMD-Version von
**reveal.js 4.6.0** in `../reveal.js` (das von den Templates geladene
Vendor-File).

## Warum?

- Die npm/CDN-Dists von reveal.js sind minifiziert – schlecht zum
  Debuggen (Console-Stacktraces) und Bearbeiten.
- Dieses Setup baut exakt das offizielle 4.6.0-Bundle (gleiche
  Quelle `js/`, gleiche Modul-Reihenfolge, UMD-Wrapper) einfach ohne
  Minifizierung.
- Die Applet-Zoom-Filterung für AutoAnimate ist **kein** Vendor-Patch,
  sondern läuft über die offizielle Config-Option `autoAnimateMatcher`
  (siehe `static/js/slides.js`) – `../reveal.js` bleibt unveränderte
  offizielle 4.6.0.

## Voraussetzungen

Node.js ≥ 18 (z. B. in `~/tools/node-v20.18.1-linux-x64/bin`,
offizielles Binary-Tarball, kein Root nötig).

## Build

```sh
cd static/vendor/reveal/reveal-build
export PATH=~/tools/node-v20.18.1-linux-x64/bin:$PATH   # falls Node nicht im PATH
npm install        # einmalig (pinnt reveal.js@4.6.0, rollup, node-resolve)
npm run build      # schreibt ../reveal.js
```

`npm run build` kopiert vorher die Dateien aus `./overrides/` (falls
vorhanden) über die Paket-Quelle und baut dann.

## Lokale Quell-Änderungen (falls je nötig)

1. `node_modules/reveal.js/js/...` editieren oder direkt nach
   `./overrides/js/...` kopieren (gleiche Struktur wie im Paket).
   `overrides/` wird committet, `node_modules/` nicht – die Änderung
   bleibt dadurch wiederaufbaubar.
2. `npm run build` erneut ausführen.
3. Die Änderung in diesem README und im Banner von `../reveal.js`
   dokumentieren.

## Verifikation

```sh
node -p "require('../reveal.js').VERSION"   # -> 4.6.0
```

Die CSS-Dateien `../reveal.css` und `../reset.css` sind reformatierte
(lesbare) Kopien der offiziellen 4.6.0-Dist-CSS – der Regelsatz ist
inhaltlich unverändert (per Whitespace-Kompakt-Vergleich verifiziert).
