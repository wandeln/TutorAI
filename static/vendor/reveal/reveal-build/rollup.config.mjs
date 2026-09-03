import { nodeResolve } from '@rollup/plugin-node-resolve';

// Baut reveal.js 4.6.0 (Quelle: node_modules/reveal.js/js) als
// LEBBARES UMD-Bundle nach ../reveal.js – ohne Minifizierung.
//
// Das Ergebnis ist inhaltlich das offizielle 4.6.0-Bundle (gleiche
// Module, gleiche Reihenfolge, UMD-Wrapper), nur nicht minifiziert.
//
// Lokale Quell-Änderungen (falls je nötig): geänderte Dateien aus
// node_modules/reveal.js/js in ./overrides spiegeln und VOR dem Build
// per `npm run sync` über die Paket-Quelle kopieren. Siehe README.md.

export default {
  input: 'node_modules/reveal.js/js/index.js',
  output: {
    file: '../reveal.js',
    format: 'umd',
    name: 'Reveal',
    exports: 'default',
    banner: [
      '/*!',
      '* reveal.js 4.6.0 (readable UMD build)',
      '* https://revealjs.com',
      '* MIT licensed',
      '*',
      '* Copyright (C) 2011-2023 Hakim El Hattab, https://hakim.se',
      '*',
      '* Hinweis: Lesbare (nicht minifizierte) Version des offiziellen',
      '* 4.6.0-Bundles, gebaut über reveal-build/rollup.config.mjs.',
      '* Inhaltlich unverändert – lokale Änderungen hier dokumentieren.',
      '*/'
    ].join('\n')
  },
  plugins: [nodeResolve({ browser: true, preferBuiltins: false })]
};
