// Kopiert ./overrides/ (falls vorhanden) 1:1 über node_modules/reveal.js/.
// Damit sind lokale Quell-Änderungen wiederaufbaubar, ohne node_modules/
// committen zu müssen. Siehe README.md.
import { cpSync, existsSync } from 'node:fs';

const overrides = 'overrides';
const target = 'node_modules/reveal.js';

if (!existsSync(overrides)) {
  console.log('keine overrides/ – unveränderte 4.6.0-Quelle wird gebaut');
  process.exit(0);
}
if (!existsSync(target)) {
  console.error('node_modules/reveal.js fehlt – erst `npm install` ausführen');
  process.exit(1);
}
cpSync(overrides, target, { recursive: true });
console.log('overrides/ über node_modules/reveal.js/ kopiert');
