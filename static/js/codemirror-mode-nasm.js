// CodeMirror-Mode für NASM x86-64 Assemblee
//
// Definiert den Modus „text/x-nasm“ (verwendet vom Preset „asm“,
// Dateiendung .asm). Bewusst klein: Kommentare, Direktiven, Labels,
// Zahlen (dez/hex/binary + Suffixe), häufige Instruktionen, Register.
// Unbekannte Wörter bleiben neutral (keine Editor-Breakage).
//
// Klassischer defineMode mit matchAt-Pattern (wie codemirror-mode-tutorai.js),
// da SimpleMode-Regex-Matching je Build unterschiedlich verhält.

(function () {
  'use strict';
  if (typeof CodeMirror === 'undefined') return;

  // Stream-Matching immer explizit an der aktuellen Position (der
  // leftmost-Match von exec() auf der Ganzen Zeile würde ansonsten
  // ab dem 2. Vorkommen pro Zeile fehlschlagen).
  function matchAt(stream, pattern) {
    var m = pattern.exec(stream.string.slice(stream.pos));
    if (m && m.index === 0) {
      stream.pos += m[0].length;
      return m[0];
    }
    return null;
  }

  // ── Wortschatz ─────────────────────────────────────────────────
  var DIRECTIVES = new Set([
    'db', 'dw', 'dd', 'dq', 'dt', 'resb', 'resw', 'resd', 'resq', 'times',
    'section', 'segment', 'global', 'extern', 'align', 'alignb', 'alignw',
    'alignq', 'bits', 'use16', 'use32', 'use64', 'string',
    'byte', 'word', 'dword', 'qword', 'tbyte', 'float', 'double',
  ]);
  var INSTRUCTIONS = new Set([
    'nop', 'ret', 'iret', 'iretd', 'iretq', 'syscall', 'sysret', 'int', 'hlt',
    'mov', 'movabs', 'movsx', 'movsxd', 'movzx', 'lea', 'xchg', 'xadd',
    'push', 'pop', 'pushf', 'popf', 'pushfq', 'popfq',
    'inc', 'dec', 'neg', 'not',
    'add', 'sub', 'adc', 'sbb', 'and', 'or', 'xor', 'test', 'cmp',
    'mul', 'imul', 'div', 'idiv',
    'shl', 'shr', 'sal', 'sar', 'rol', 'ror', 'rcl', 'rcr',
    'cdq', 'cqo', 'cbw', 'cwde', 'cdqe',
    'cmc', 'clc', 'stc', 'cld', 'std',
    'enter', 'leave', 'call', 'jmp',
    'jz', 'jnz', 'je', 'jne', 'jl', 'jle', 'jg', 'jge',
    'jb', 'jbe', 'ja', 'jae', 'js', 'jns', 'jo', 'jno', 'jp', 'jnp',
    'loop', 'loope', 'loopne', 'loopeq', 'loopnz', 'loopz',
    'rep', 'repe', 'repne', 'repz', 'repnz',
    'stosb', 'stosw', 'stosd', 'stosq',
    'movsb', 'movsw', 'movsd', 'movsq',
    'lodsb', 'lodsw', 'lodsd', 'lodsq',
    'scasb', 'scasw', 'scasd', 'scasq',
    'in', 'out', 'insb', 'insw', 'insd', 'outsb', 'outsw', 'outsd',
    'wait', 'ud2', 'ud1', 'cpuid', 'rdrand', 'rdtsc', 'rdtscp',
    'sfence', 'lfence', 'mfence', 'pause',
  ]);
  var REGISTERS = (function () {
    var s = new Set();
    ['rax', 'rbx', 'rcx', 'rdx', 'rsi', 'rdi', 'rbp', 'rsp'].forEach(function (n) { s.add(n); });
    for (var i = 8; i <= 15; i++) {
      s.add('r' + i); s.add('r' + i + 'b'); s.add('r' + i + 'w');
      s.add('r' + i + 'd'); s.add('r' + i + 'l');
    }
    ['eax', 'ebx', 'ecx', 'edx', 'esi', 'edi', 'ebp', 'esp'].forEach(function (n) { s.add(n); });
    ['ax', 'bx', 'cx', 'dx', 'si', 'di', 'bp', 'sp', 'ip'].forEach(function (n) { s.add(n); });
    ['al', 'bl', 'cl', 'dl', 'ah', 'bh', 'ch', 'dh',
     'sil', 'dil', 'bpl', 'spl'].forEach(function (n) { s.add(n); });
    ['cs', 'ds', 'es', 'fs', 'gs', 'ss'].forEach(function (n) { s.add(n); });
    for (var j = 0; j < 8; j++) s.add('st' + j);
    for (var k = 0; k < 16; k++) s.add('xmm' + k);
    return s;
  })();

  CodeMirror.defineMode('text/x-nasm', function () {
    return {
      startState: function () { return {}; },
      token: function (stream) {
        // Kommentar bis Zeilenende
        if (matchAt(stream, /;.*/)) return 'comment';
        // Präprozessor: %define, %if, %rep, %times, …
        if (matchAt(stream, /%[a-zA-Z_][\w.]*/)) return 'keyword';
        // Zahlen: 0x…, %1010, 10, 10h, 10d, 10w, 10b
        if (matchAt(stream, /0[xX][0-9a-fA-F]+/)) return 'number';
        if (matchAt(stream, /%[01]+/)) return 'number';
        if (matchAt(stream, /\d+[dhwbt]?/)) return 'number';
        // Bezeichner (Labels, Instruktionen, Register, extern, .text, …)
        var t = matchAt(stream, /[a-zA-Z_.$][\w.$-]*/);
        if (t) {
          // Label: direkt nach dem Bezeichner ein ":" (ggf. nach Leerzeichen)
          var labelMatch = /^\s*:/.exec(stream.string.slice(stream.pos));
          if (labelMatch && labelMatch.index === 0) return 'variable-2';
          var low = t.toLowerCase();
          if (DIRECTIVES.has(low)) return 'keyword';
          if (INSTRUCTIONS.has(low)) return 'builtin';
          if (REGISTERS.has(low)) return 'atom';
          return null; // extern, Abschnitte, Symbole — neutral
        }
        if (stream.eatWhile(/[ \t]/)) return null;
        stream.next();
        return null;
      },
      lineComment: ';',
    };
  });
})();
