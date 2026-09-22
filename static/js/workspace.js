/* ═══════════════════════════════════════════════════════════════
   WorkspaceUI — geteilte Datei-Baum + Editor-Komponente für
   Workspace-Aufgaben.

   Verwendet von:
   - templates/tutor/task_detail_workspace.html  (Tutor/Prof: volle Rechte)
   - templates/student/task_solve_workspace.html (Student: eingeschränkt)

   Initialisierung im Template:
     const wsUI = WorkspaceUI.init({
       treeEl, editorEl, apiBase, ...   // s. init() unten
     });

   Konzepte:
   - Zugriffsklassen (explizit pro Datei UND Ordner, im Baum markiert):
       ✏️ edit      — Tutor + Student schreiben (Standard)
       🔒 readonly  — Student nur lesen (geteilt: 1 Kopie für alle)
       👤 hidden    — Student sieht es nie (nur Tutor + Korrektur)
     Effektive Klasse = restriktivste von (eigene explizite, alle
     Ordner-Vorfahren). Das Backend liefert je Datei die effektive Klasse
     (`access`) + die explizite (`file_access`); Ordner-Klassen kommen als
     `folders`-Liste {path, access (effektiv), own (explizit)}.
   - Init-Artefakte (init.sh/.init_hidden.sh-Ergebnisse, mit `init: true`
     vom Backend): am realen Pfad im Baum, read-only (📦-Marker),
     entstehen/ändern sich nur per neuem Init-Build.
   - "Neuer Ordner" ist client-seitig (leere Ordner werden nicht
     persistiert, außer sie bekommen eine Zugriffs-Klasse) und existiert,
     bis die erste Datei darin angelegt wird.
   - Move/Rename = Backend-Endpoint /files/move (Dateien) bzw.
     /folders/move (Tutor: Ordner inkl. Zugriffsklassen in einem Call).
   - Reihenfolge: Dateien, Ordner und [init]-Artefakte sind je Ordner per
     Drag & Drop sortierbar (rein Anzeige — Struktur unverändert). Tutor:
     global persistiert (sort_order / task_workspace_orders via /reorder);
     Student: nur eigene Ansicht (localStorage pro User+Task, localOrderKey).
     Drop im selben Ordner = neu ordnen; in anderen Ordner = Move (Gates).
   ═══════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  // ─── Zugriffsklassen (explizit, entspricht dem Backend-Modell) ──
  const ACCESS = {
    edit:     { icon: "✏️", label: "Editierbar — Tutor + Student können schreiben" },
    readonly: { icon: "🔒", label: "Read-only — Student darf nur lesen (geteilt für alle)" },
    hidden:   { icon: "👤", label: "Versteckt — Student sieht es nie (nur Tutor + Korrektur)" },
  };
  const ACCESS_RANK = { edit: 0, readonly: 1, hidden: 2 };
  const ACCESS_BY_RANK = ["edit", "readonly", "hidden"];

  function accessLabel(a) {
    if (a === "readonly") return "🔒 read-only";
    if (a === "hidden") return "👤 versteckt";
    return "✏️ editierbar";
  }

  // System-Skripte mit fester Zugriffs-Klasse (spiegelt system_file_access
  // im Backend): diese können im Kontextmenü nicht umgestellt werden.
  // Alle sechs stehen immer in der Wurzel (weiche Punkt-Konvention: 👤
  // beginnt mit ".").
  const SYSTEM_FILE_ACCESS = {
    "run.sh": "readonly",
    "init.sh": "readonly",
    "test.sh": "readonly",
    ".init_hidden.sh": "hidden",
    ".test_private.sh": "hidden",
    ".run_solution.sh": "hidden",
  };
  function systemAccessOf(path) {
    const p = String(path).replace(/\\/g, "/").replace(/^\/+/, "");
    return SYSTEM_FILE_ACCESS[p] || null;
  }

  // ─── kleine Helper (mit Fallback, da Page-Globals erst im Body) ──
  function esc(s) {
    if (typeof escapeHtml === "function") return escapeHtml(s);
    return String(s).replace(/[&<>"']/g, c => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }
  function toast(msg, type) {
    if (typeof showToast === "function") showToast(msg, type);
  }
  function fmtBytes(n) {
    if (n == null) return "";
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }
  function fileIcon(p) {
    const ext = p.slice(p.lastIndexOf(".")).toLowerCase();
    if (ext === ".py" || ext === ".pyw") return "🐍";
    if ([".c", ".cpp", ".cc", ".h", ".hpp"].includes(ext)) return "⚙️";
    if (ext === ".asm") return "🔧";
    if ([".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"].includes(ext)) return "🖼️";
    if ([".csv", ".json"].includes(ext)) return "🗃️";
    if ([".sh", ".conf"].includes(ext)) return "📜";
    return "📄";
  }

  // ─── CodeMirror-Modi je Dateiendung ────────────────────────────
  const CM_MODES = {
    ".py": "python", ".pyw": "python",
    ".js": "javascript",
    ".html": "htmlmixed", ".htm": "htmlmixed",
    ".xml": "xml", ".svg": "xml",
    ".css": "text/css",
    ".md": "gfm", ".markdown": "gfm",
    ".json": "application/json",
    ".sh": "shell", ".bash": "shell",
    ".yml": "yaml", ".yaml": "yaml",
    ".c": "text/x-csrc", ".h": "text/x-csrc",
    ".cpp": "text/x-c++src", ".cc": "text/x-c++src", ".hpp": "text/x-c++src",
    ".asm": "text/x-nasm",
    ".conf": "text/x-nginx-conf", ".nginx": "text/x-nginx-conf",
    ".sql": "text/x-sql",
    ".ini": "text/x-ini",
    ".toml": "text/x-toml",
    ".txt": "text/plain",
  };
  function cmModeForPath(p) {
    const dot = String(p).lastIndexOf(".");
    if (dot < 0) return null;
    const m = CM_MODES[p.slice(dot).toLowerCase()];
    // Modi sind je nach Registration unter CodeMirror.modes (Namens-Key,
    // z.B. "clike") ODER CodeMirror.mimeModes (MIME-Key, z.B. "text/x-c++src")
    // verfügbar — beide Tabellen prüfen.
    if (!m || !window.CodeMirror) return null;
    const known = (CodeMirror.modes && CodeMirror.modes[m]) ||
                  (CodeMirror.mimeModes && CodeMirror.mimeModes[m]);
    return known ? m : null;
  }

  // ─── Medien-Erkennung (Preview statt Editor) ────────────────────
  const IMG_EXTS = [".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"];
  const VIDEO_EXTS = [".mp4", ".webm", ".ogv", ".mov", ".m4v"];
  const AUDIO_EXTS = [".mp3", ".wav", ".ogg", ".oga", ".m4a", ".flac", ".aac"];

  function mediaKind(path, contentType) {
    const p = String(path).toLowerCase();
    const ends = exts => exts.some(e => p.endsWith(e));
    if (ends(IMG_EXTS) || (contentType || "").startsWith("image/")) return "image";
    if (ends(VIDEO_EXTS)) return "video";
    if (ends(AUDIO_EXTS)) return "audio";
    if (p.endsWith(".pdf")) return "pdf";
    if ((contentType || "").includes("octet-stream")) return "binary";
    return null;
  }

  function encPath(p) {
    return String(p).split("/").map(encodeURIComponent).join("/");
  }

  // ─── Kontextmenü (global, max. 1 offen) ─────────────────────────
  let ctxMenu = null, ctxEsc = null, ctxClick = null;
  function hideCtxMenu() {
    if (ctxMenu) { ctxMenu.remove(); ctxMenu = null; }
    if (ctxEsc) { document.removeEventListener("keydown", ctxEsc); ctxEsc = null; }
    if (ctxClick) { document.removeEventListener("click", ctxClick, true); ctxClick = null; }
  }
  function showCtxMenu(x, y, items) {
    hideCtxMenu();
    const menu = document.createElement("div");
    menu.className = "ws-ctx-menu";
    items.forEach(it => {
      if (it.sep) {
        const s = document.createElement("div");
        s.className = "ws-ctx-sep";
        menu.appendChild(s);
        return;
      }
      const el = document.createElement("div");
      el.className = "ws-ctx-item" + (it.danger ? " ws-ctx-danger" : "") +
        (it.header ? " ws-ctx-header" : "");
      el.textContent = it.label;
      el.title = it.label;
      if (!it.header && it.fn) el.onclick = () => { hideCtxMenu(); it.fn(); };
      menu.appendChild(el);
    });
    document.body.appendChild(menu);
    const r = menu.getBoundingClientRect();
    menu.style.left = Math.max(8, Math.min(x, window.innerWidth - r.width - 8)) + "px";
    menu.style.top = Math.max(8, Math.min(y, window.innerHeight - r.height - 8)) + "px";
    ctxMenu = menu;
    ctxEsc = e => { if (e.key === "Escape") hideCtxMenu(); };
    ctxClick = e => { if (ctxMenu && !ctxMenu.contains(e.target)) hideCtxMenu(); };
    // Listener erst nach dem klick, der das Menü geöffnet hat
    setTimeout(() => {
      document.addEventListener("keydown", ctxEsc);
      document.addEventListener("click", ctxClick, true);
    }, 0);
  }

  // ─── Init ──────────────────────────────────────────────
  // opts:
  //   treeEl, editorEl      HTMLElement (Pflicht)
  //   mediaEl               HTMLElement | null (Medien-Preview-Bereich)
  //   apiBase               string | null (null = Task noch nicht gespeichert)
  //   mainFile              Ausgangs-Main-Datei ("" = keine)
  //   allowMain             ⭐-Marker + Main-Datei setzbar (Kontextmenü)
  //   readOnlyCheck         fn(path) -> bool
  //   canCreate / canDelete / canMove / allowBulk   boolesche Rechte
  //   canReorder            Drag & Drop ändert die Reihenfolge (Dateien + Ordner)
  //   localOrderKey         localStorage-Key für die LOKALE Reihenfolge
  //                         (Student: eigene Ansicht, pro User+Task); Tutor:
  //                         weglassen (global via /reorder-API)
  //   canSetAccess          Kontextmenü bietet „Zugriff“ (Tutor: true)
  //   folderMove            Ordner-Move = ein API-Call /folders/move (Tutor)
  //   moveGate              fn(src, dst) -> {ok, reason?}
  //   saveStateEl           optionales HTMLElement (Auto-Save-Status)
  //   emptyMsg / noTaskMsg  Platzhalter im Baum
  //   onMainFileChange(p) / onFilesLoaded(files)
  function init(opts) {
    const {
      treeEl, editorEl, mediaEl = null, apiBase = null,
      mainFile = "", allowMain = false,
      readOnlyCheck = () => false,
      canCreate = false, canDelete = false, canMove = false, allowBulk = false,
      canReorder = false, canSetAccess = false, folderMove = false,
      localOrderKey = null,
      moveGate = () => ({ ok: true }),
      saveStateEl = null,
      emptyMsg = "(keine Dateien)", noTaskMsg = null,
      onMainFileChange = null, onFilesLoaded = null,
    } = opts;

    const state = {
      files: [],            // [{path, size, is_binary, access, file_access, init}]
      folders: [],          // persistierte Ordner-Klassen [{path, access, own}]
      folderMap: {},        // path → explizite Klasse ("readonly"/"hidden")
      folderOrder: {},      // path → Anzeige-Order (Ordner + [init]-Artefakte, Backend)
      localOrder: {},       // Student: lokale Reihenfolge je Ordner (localStorage)
      currentFile: null,
      dirty: false,
      mainFile: String(mainFile || ""),
      collapsed: new Set(), // zugeklappte Ordner (Pfad ohne Slash)
      extraDirs: new Set(), // client-seitige Ordner (nicht persistiert)
      mediaUrl: null,
      saveTimer: null,
      dragPath: null,
      dragType: null,       // "file" | "dir" (während des Drags gesetzt)
      dragRejected: null,   // letzter Ablehnungs-Grund (→ Fehlermeldung beim dragend)
    };

    // ── Lokale Reihenfolge (Student: nur eigene Ansicht) ──────────
    // {"<ordner>": {"dirs": [Namen], "files": [Namen]}} — nur explizit
    // per Drag geänderte Gruppen; neue Einträge sortieren sich ans Ende.
    function loadLocalOrder() {
      if (!localOrderKey) return {};
      try {
        const raw = localStorage.getItem(localOrderKey);
        const o = raw ? JSON.parse(raw) : null;
        return (o && typeof o === "object") ? o : {};
      } catch (err) { return {}; }
    }
    function saveLocalOrder() {
      if (!localOrderKey) return;
      try { localStorage.setItem(localOrderKey, JSON.stringify(state.localOrder)); }
      catch (err) { /* ignore (z. B. Quota/Privatmodus) */ }
    }
    state.localOrder = loadLocalOrder();

    // ── Effektive Zugriffs-Klassen (client-seitig; Primärquelle ist das
    //    Backend-Feld `access` — diese Berechnung dient als Fallback +
    //    für Ordner/Probe-Pfade) ────────────────────────────────────
    // [init]-Erkennung: das Backend flaggt Init-Artefakte (init: true) —
    // sie liegen am realen Pfad und sind read-only.
    function isInitPath(path) {
      const f = state.files.find(x => x.path === String(path || ""));
      return !!(f && f.init);
    }
    function ancestorRank(path) {
      let rank = 0;
      let parent = dirOf(path);
      while (parent) {
        rank = Math.max(rank, ACCESS_RANK[state.folderMap[parent] || "edit"] || 0);
        parent = dirOf(parent);
      }
      return rank;
    }
    function effWithAncestors(path, own) {
      const ownRank = ACCESS_RANK[own || "edit"] || 0;
      return ACCESS_BY_RANK[Math.max(ownRank, ancestorRank(path))];
    }
    function effectiveAccess(path) {
      const p = String(path || "").replace(/\/+$/, "");
      if (!p) return "edit";
      const f = state.files.find(x => x.path === p);
      if (f) return f.access || effWithAncestors(p, f.file_access);
      return effWithAncestors(p, state.folderMap[p]);
    }

    const cm = window.CodeMirror.fromTextArea(editorEl, {
      // Explizit „text/plain“: ohne Mode würde CM5 den ERSTEN registrierten
      // Mode als Default nehmen (hier „python“) — irreführend vor der ersten
      // Datei. openFile() setzt pro Datei den korrekten Mode.
      mode: "text/plain",
      theme: "dracula",
      lineNumbers: true,
      autoCloseBrackets: true,
      indentUnit: 4,
      tabSize: 4,
      lineWrapping: true,
      matchBrackets: true,
    });

    // ── Trenner Tree/Editor (ziehen → Baum/Editor vergrößern) ──────
    // Orientation folgt dem Layout-Breakpoint (sm = 640 px):
    // Desktop (Zeilen) ändert die Breite des Tree-Panels,
    // Mobile (Spalten) seine Höhe. Doppelklick = Standardgröße.
    const splitHandle = document.getElementById("ws-split-handle");
    const splitPanel = splitHandle && treeEl ? treeEl.parentElement : null;
    const splitWrap = splitHandle ? splitHandle.closest(".ws-tree-editor-wrap") : null;
    const SPLIT_MIN = 180;
    if (splitHandle && splitPanel && splitWrap) {
      splitHandle.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        const horizontal = window.innerWidth >= 640;
        const rect = splitPanel.getBoundingClientRect();
        const startX = e.clientX, startY = e.clientY;
        const startSize = horizontal ? rect.width : rect.height;
        const maxSize = () => {
          const r = splitWrap.getBoundingClientRect();
          return Math.floor((horizontal ? r.width : r.height) * 0.6);
        };
        let raf = 0;
        function onMove(ev) {
          const delta = (horizontal ? ev.clientX : ev.clientY)
                        - (horizontal ? startX : startY);
          const size = Math.max(SPLIT_MIN, Math.min(startSize + delta, maxSize()));
          if (horizontal) splitPanel.style.width = size + "px";
          else splitPanel.style.height = size + "px";
          if (!raf) raf = requestAnimationFrame(() => { raf = 0; cm.refresh(); });
        }
        function onUp() {
          splitHandle.removeEventListener("pointermove", onMove);
          splitHandle.removeEventListener("pointerup", onUp);
          splitHandle.removeEventListener("pointercancel", onUp);
          splitHandle.classList.remove("ws-split-dragging");
          document.body.classList.remove("ws-split-drag", "ws-split-drag-h", "ws-split-drag-v");
          cm.refresh();
        }
        splitHandle.setPointerCapture(e.pointerId);
        splitHandle.classList.add("ws-split-dragging");
        document.body.classList.add("ws-split-drag",
                                     horizontal ? "ws-split-drag-h" : "ws-split-drag-v");
        splitHandle.addEventListener("pointermove", onMove);
        splitHandle.addEventListener("pointerup", onUp);
        splitHandle.addEventListener("pointercancel", onUp);
      });
      splitHandle.addEventListener("dblclick", () => {
        splitPanel.style.width = "";
        splitPanel.style.height = "";
        cm.refresh();
      });
      // Inline-Maß beim Breakpoint-Übergang verwerfen (sonst bliebe
      // z. B. die Desktop-Breite im Mobile-Layout hängen).
      window.addEventListener("resize", () => {
        if (window.innerWidth < 640) splitPanel.style.width = "";
        else splitPanel.style.height = "";
      });
    }

    // ── Vollbild (Tree + Editor füllt das ganze Browser-Fenster) ─────
    // Toggle-Button in der Tree-Toolbar (beide Templates). ESC beendet
    // den Modus — das CodeMirror-Search-Dialog konsumiert ESC vor uns
    // (e_stop → stopPropagation), daher keine Kollision.
    const fsBtn = document.getElementById("ws-fullscreen-btn") ||
                  document.getElementById("ws-files-fullscreen-btn");
    const fsWrap = fsBtn ? fsBtn.closest(".ws-tree-editor-wrap") : null;
    function setFullscreen(on) {
      if (!fsWrap) return;
      fsWrap.classList.toggle("ws-fullscreen", on);
      document.body.classList.toggle("ws-fs-lock", on);
      if (fsBtn) {
        fsBtn.classList.toggle("ws-tree-btn-active", on);
        fsBtn.title = on ? "Vollbild beenden (Esc)" : "Vollbild";
      }
      // CodeMirror bemerkt Größenänderungen nicht selbst → Refresh.
      requestAnimationFrame(() => cm.refresh());
    }
    if (fsBtn && fsWrap) {
      fsBtn.addEventListener("click", () =>
        setFullscreen(!fsWrap.classList.contains("ws-fullscreen")));
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && fsWrap.classList.contains("ws-fullscreen"))
          setFullscreen(false);
      });
    }

    // ── Status ─────────────────────────────────────────────────────
    function setSaveState(txt) {
      if (saveStateEl) saveStateEl.textContent = txt || "";
    }

    // ── Baum-Bau (Dateien + persistierte Ordner + client-seitige Ordner) ──
    function buildTree() {
      const root = {};
      const ensureDir = parts => {
        let node = root;
        parts.forEach(part => {
          if (!node[part] || node[part].file) node[part] = {};
          node = node[part];
        });
      };
      state.extraDirs.forEach(d => ensureDir(d.split("/")));
      state.folders.forEach(fd => ensureDir(fd.path.split("/")));
      state.files.forEach(f => {
        const parts = f.path.split("/");
        let node = root;
        parts.forEach((part, i) => {
          if (!node[part] || node[part].file) node[part] = {};
          node = node[part];
          if (i === parts.length - 1) {
            node.file = {
              size: f.size,
              binary: !!f.is_binary,
              fullPath: f.path,
              sort_order: f.sort_order == null ? null : f.sort_order,
              access: f.access || null,
              file_access: f.file_access == null ? null : f.file_access,
              init: !!f.init,
            };
          }
        });
      });
      return root;
    }

    function allDirPaths() {
      const dirs = new Set();
      state.files.forEach(f => {
        const parts = f.path.split("/");
        let p = "";
        for (let i = 0; i < parts.length - 1; i++) {
          p = p ? p + "/" + parts[i] : parts[i];
          dirs.add(p);
        }
      });
      state.folders.forEach(fd => dirs.add(fd.path));
      state.extraDirs.forEach(d => dirs.add(d));
      return Array.from(dirs).sort();
    }

    // ── Drag & Drop ───────────────────────────────────────────────
    function dirOf(p) {
      return p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "";
    }
    // Datei-Reihenfolge: sort_order (null = letzter Platz), dann alphabetisch.
    function cmpFileOrder(a, b) {
      const oa = a.sort_order == null ? Number.POSITIVE_INFINITY : a.sort_order;
      const ob = b.sort_order == null ? Number.POSITIVE_INFINITY : b.sort_order;
      return (oa - ob) || a.fullPath.localeCompare(b.fullPath);
    }
    // ── Gruppen-Reihenfolge (Dateien + Ordner je Ordner) ──────────
    // Anzeige: lokale Student-Ordnung (localStorage) > gepflegte
    // sort_order (Dateien: DB; Ordner/[init]-Artefakte: folder_order)
    // > alphabetisch.
    function localPos(dir, kind, name) {
      const saved = state.localOrder[dir] && state.localOrder[dir][kind];
      if (!Array.isArray(saved)) return null;
      const i = saved.indexOf(name);
      return i === -1 ? null : i;
    }
    function cmpGroup(dir, kind, defaultCmp) {
      return (a, b) => {
        const pa = localPos(dir, kind, a.name);
        const pb = localPos(dir, kind, b.name);
        if (pa !== null || pb !== null) {
          const va = pa === null ? Number.MAX_SAFE_INTEGER : pa;
          const vb = pb === null ? Number.MAX_SAFE_INTEGER : pb;
          if (va !== vb) return va - vb;
        }
        return defaultCmp(a, b);
      };
    }
    // Ordner direkt unter dir in der Grund-Reihenfolge (Namen).
    function currentDirNames(dir) {
      return allDirPaths()
        .filter(d => dirOf(d) === dir &&
                     (effectiveAccess(d) !== "hidden" || canSetAccess))
        .sort((a, b) => {
          const oa = state.folderOrder[a], ob = state.folderOrder[b];
          const va = oa == null ? Number.MAX_SAFE_INTEGER : oa;
          const vb = ob == null ? Number.MAX_SAFE_INTEGER : ob;
          if (va !== vb) return va - vb;
          return a.localeCompare(b);
        })
        .map(d => d.split("/").pop());
    }
    // Dateien direkt unter dir in der Grund-Reihenfolge (Namen).
    function currentFileNames(dir) {
      return state.files
        .filter(f => dirOf(f.path) === dir)
        .sort((a, b) => {
          const oa = a.sort_order == null ? Number.MAX_SAFE_INTEGER : a.sort_order;
          const ob = b.sort_order == null ? Number.MAX_SAFE_INTEGER : b.sort_order;
          return (oa - ob) || a.path.localeCompare(b.path);
        })
        .map(f => f.path.split("/").pop());
    }
    // Aktuelle Anzeigereihenfolge einer Gruppe (lokal vor global).
    function currentGroupNames(dir, kind) {
      const base = kind === "dirs" ? currentDirNames(dir) : currentFileNames(dir);
      const saved = state.localOrder[dir] && state.localOrder[dir][kind];
      if (!Array.isArray(saved) || !saved.length) return base;
      const pos = new Map(saved.map((n, i) => [n, i]));
      return base.slice().sort((a, b) => {
        const ia = pos.has(a) ? pos.get(a) : Number.MAX_SAFE_INTEGER;
        const ib = pos.has(b) ? pos.get(b) : Number.MAX_SAFE_INTEGER;
        return ia - ib;  // stabil → Gleichstand behält Grund-Reihenfolge
      });
    }
    // Fehlertext für abgelehnte Drop-Ziele („Bereits dort.“ bleibt still).
    function rejectReason(gate) {
      return (gate.reason && gate.reason !== "Bereits dort.") ? gate.reason : null;
    }
    // Ordner mit Inhalt (Dateien oder persistierter Zugriffs-Klasse) sind
    // verschiebbar. Student: zusätzlich nur effektiv editierbare.
    function canDragDir(dir) {
      if (!canMove) return false;
      const hasContent = state.files.some(f => f.path.startsWith(dir + "/")) ||
        state.folders.some(fd => fd.path === dir || fd.path.startsWith(dir + "/"));
      if (!hasContent) return false;
      if (folderMove) return true;
      // Student: Gate über Subtree-Dateien bzw. Probe-Pfad (leerer Ordner)
      const sub = filesInDir(dir);
      if (sub.length) return !!moveGate(sub[0].path, sub[0].path).ok;
      return !!moveGate(dir + "/__.probe", dir + "/__.probe").ok;
    }
    function dropGateDir(srcDir, dstDir) {
      const curParent = srcDir.includes("/") ? srcDir.slice(0, srcDir.lastIndexOf("/")) : "";
      if (dstDir === curParent) return { ok: false, reason: "Bereits dort." };
      if (dstDir === srcDir || dstDir.startsWith(srcDir + "/")) {
        return { ok: false, reason: "Ein Ordner kann nicht in sich selbst verschoben werden." };
      }
      // Zugriffs-Klassen-Check über eine repräsentative Datei (alle
      // wechseln gleich) — z. B. blockiert der Student-MoveGate Moves
      // aus 🔒/👤-Bereichen.
      const sub = filesInDir(srcDir);
      if (sub.length) {
        const f = sub[0];
        const subPath = f.path.slice(srcDir.length + 1);
        return moveGate(f.path, dstDir ? dstDir + "/" + subPath : subPath);
      }
      return { ok: true };
    }
    function dropGate(src, dstDir) {
      const name = src.split("/").pop();
      const dst = dstDir ? dstDir + "/" + name : name;
      if (dst === src) return { ok: false, reason: "Bereits dort." };
      if (dstDir && src.startsWith(dstDir + "/")) {
        return { ok: false, reason: "Datei liegt bereits in diesem Ordner." };
      }
      return moveGate(src, dst);
    }

    // Verschieben ohne Bestätigungs-Popup (sichtbar im Baum, per ↺/Undo nicht
    // nötig — Ziel ist beim Drop explizit gewählt).
    function doMove(src, dstDir) {
      const gate = dropGate(src, dstDir);
      if (!gate.ok) {
        if (gate.reason && gate.reason !== "Bereits dort.") toast(gate.reason, "warning");
        return;
      }
      const name = src.split("/").pop();
      const dst = dstDir ? dstDir + "/" + name : name;
      moveFile(src, dst);
    }

    // Ordner verschieben: Gate-Check über alle Subtree-Dateien, dann
    // je nach Rolle: Tutor = ein Call /folders/move (Disk + Datei-Rows +
    // Ordner-Klassen-Zeilen ziehen mit); Student = sequentiell jede Datei
    // (Ordner-Klassen ändert ein Student nie). Leere client-seitige Ordner
    // (extraDirs) werden nur umgemappt, ohne API-Call.
    async function moveDirImpl(srcDir, newDir) {
      if (state.files.some(f => f.path === newDir || f.path.startsWith(newDir + "/"))) {
        toast("„" + newDir + "“ existiert bereits — bitte umbenennen.", "error");
        return;
      }
      if (folderMove) {
        const persisted = filesInDir(srcDir).length > 0 ||
          state.folders.some(fd => fd.path === srcDir || fd.path.startsWith(srcDir + "/"));
        if (persisted) {
          try {
            const res = await fetch(apiBase + "/folders/move", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              credentials: "same-origin",
              body: JSON.stringify({ src: srcDir, dst: newDir }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || res.status);
          } catch (err) {
            toast("Ordner-Verschieben fehlgeschlagen: " + err.message, "error");
            return;
          }
        }
      } else {
        const inDir = filesInDir(srcDir);
        for (const f of inDir) {
          const sub = f.path.slice(srcDir.length + 1);
          const g = moveGate(f.path, newDir + "/" + sub);
          if (!g.ok) {
            toast(g.reason || ("„" + f.path + "“ kann nicht verschoben werden."), "warning");
            return;
          }
        }
        for (const f of inDir) {
          const sub = f.path.slice(srcDir.length + 1);
          if (!await moveFile(f.path, newDir + "/" + sub)) return;
        }
      }
      // client-seitige (leere) Ordner des Subtrees neu mappen
      Array.from(state.extraDirs).forEach(d => {
        if (d === srcDir) {
          state.extraDirs.delete(d);
          state.extraDirs.add(newDir);
        } else if (d.startsWith(srcDir + "/")) {
          state.extraDirs.delete(d);
          state.extraDirs.add(newDir + d.slice(srcDir.length));
        }
      });
      state.extraDirs.delete(srcDir);  // Quell-Ordner wird leer → entfernen
      await refresh();
    }

    async function doMoveDir(srcDir, dstDir) {
      const gate = dropGateDir(srcDir, dstDir);
      if (!gate.ok) {
        if (gate.reason && gate.reason !== "Bereits dort.") toast(gate.reason, "warning");
        return;
      }
      const name = srcDir.split("/").pop();
      await moveDirImpl(srcDir, dstDir ? dstDir + "/" + name : name);
    }

    // Sortierung: src in dir vor/nach target einsortieren (Gruppe
    // "files"|"dirs"). Student: nur eigene Ansicht (localStorage, kein
    // API-Call); Tutor: global per /reorder persistiert.
    async function reorderInDir(dir, kind, src, target, before) {
      const srcName = src.split("/").pop();
      const tName = target.split("/").pop();
      const without = currentGroupNames(dir, kind).filter(n => n !== srcName);
      let idx = without.indexOf(tName);
      if (idx === -1) return;
      if (!before) idx += 1;
      const ordered = without.slice(0, idx).concat(srcName, without.slice(idx));
      if (localOrderKey) {
        state.localOrder[dir] = Object.assign({}, state.localOrder[dir], {
          [kind]: ordered,
        });
        saveLocalOrder();
        renderTree();
        return;
      }
      const full = n => (dir ? dir + "/" + n : n);
      // Tutor: leere client-seitige Ordner (extraDirs) haben keine
      // Backend-Präsenz → filtern (behalten die Grund-Position).
      const send = kind === "dirs"
        ? ordered.filter(n => !state.extraDirs.has(full(n))).map(full)
        : ordered.map(full);
      if (!send.length) return;
      try {
        const res = await fetch(apiBase + "/reorder", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ order: send }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || res.status);
        await refresh();
      } catch (err) {
        toast("Sortierung fehlgeschlagen: " + err.message, "error");
        await refresh();
      }
    }
    // Ordner auf Datei-Zeile im selben Ordner: die Ordner-Gruppe steht
    // immer über den Dateien → Ordner ans Ende der Gruppe.
    function reorderDirToEnd(dir, srcDir) {
      const others = currentGroupNames(dir, "dirs")
        .filter(n => n !== srcDir.split("/").pop());
      if (!others.length) return;
      const last = others[others.length - 1];
      reorderInDir(dir, "dirs", srcDir, dir ? dir + "/" + last : last, false);
    }

    // ── Zeilen ────────────────────────────────────────────────────────────
    function fileRow(name, meta, path, depth) {
      const div = document.createElement("div");
      const isInit = !!meta.init;
      const acc = isInit ? "readonly" : (meta.access || effectiveAccess(path));
      const accDef = ACCESS[acc] || ACCESS.edit;
      const isMain = allowMain && path === state.mainFile;
      const active = path === state.currentFile;
      const ro = readOnlyCheck(path);
      // Alle Dateien sind per Handle ziehbar; Zugriffs-Klassen/skriptfeste
      // Pfade blockt der Drop-Gate (mit Fehlermeldung, nicht ohne Handle).
      const draggable = !!(canMove || canReorder);
      div.className = "ws-row ws-row-file px-1 py-1.5 rounded flex items-center gap-1 " +
        (active ? "bg-indigo-100 text-indigo-900" : "text-gray-700 hover:bg-gray-200");
      // Handle bleibt linksbündig; die Tiefe-Einrückung sitzt am Icon.
      div.style.paddingLeft = "2px";
      div.dataset.path = path;
      div.title = isInit
        ? "init.sh-Ergebnis — read-only (wird per neuem Init-Build neu erzeugt)"
        : (acc !== "edit" ? accDef.label : "");
      div.innerHTML =
        '<span class="ws-drag' + (draggable ? "" : " ws-drag-off") + '"' +
        (draggable ? ' title="Ziehen: verschieben / sortieren"' : "") + ">⠿</span>" +
        '<span class="shrink-0"' + (depth ? ' style="margin-left:' + (depth * 14) + 'px"' : "") + ">" + fileIcon(path) + "</span>" +
        (isInit
          ? '<span title="init.sh-Ergebnis — read-only">📦</span>'
          : '<span title="' + esc(accDef.label) + '">' + accDef.icon + "</span>") +
        '<span class="truncate flex-1">' + esc(name) + "</span>" +
        (isMain ? '<span class="shrink-0" title="Main-Datei (Editor-Fokus)">⭐</span>' : "") +
        '<span class="text-[10px] text-gray-400 shrink-0">' + fmtBytes(meta.size) + "</span>";
      div.onclick = () => openFile(path);
      div.oncontextmenu = e => showFileMenu(e, path);
      // Drag-Handle (nicht die ganze Zeile → Long-Press auf Touch-Geräten
      // triggert weiterhin das Kontextmenü statt Drag & Drop).
      if (draggable) {
        const handle = div.querySelector(".ws-drag");
        handle.draggable = true;
        handle.ondragstart = e => {
          state.dragPath = path;
          state.dragType = "file";
          state.dragRejected = null;
          e.dataTransfer.effectAllowed = "move";
          try { e.dataTransfer.setData("text/plain", path); } catch (err) { /* IE */ }
          e.stopPropagation();
        };
        handle.ondragend = () => {
          const rejected = state.dragRejected;
          state.dragPath = null;
          state.dragType = null;
          state.dragRejected = null;
          clearDropMarks();
          if (rejected) toast(rejected, "warning");
        };
      }
      // Drop-Target: in den Ordner dieser Datei ziehen (auch über Dateien in
      // geöffneten Ordnern) oder — wenn sortierbar und im selben Ordner —
      // vor/nach der Datei einsortieren (obere/halbe Zeile = davor, untere = danach).
      div.ondragover = e => {
        if (!state.dragPath || state.dragPath === path) return;
        const src = state.dragPath;
        const myDir = dirOf(path);
        // Ordner-Drag: Datei-Zeile im selben Ordner = Ordner-Gruppe neu
        // ordnen (Ordner stehen über allen Dateien → ans Ende); sonst
        // Drop-Target „in den Ordner dieser Datei“.
        if (state.dragType === "dir") {
          if (canReorder && dirOf(src) === myDir) {
            e.preventDefault();
            e.stopPropagation();
            e.dataTransfer.dropEffect = "move";
            treeEl.classList.remove("ws-tree-droproot");
            div.classList.add("ws-drop-after");
            return;
          }
          const gd = dropGateDir(src, myDir);
          if (!gd.ok) { state.dragRejected = rejectReason(gd); return; }
          state.dragRejected = null;
          e.preventDefault();
          e.stopPropagation();
          e.dataTransfer.dropEffect = "move";
          treeEl.classList.remove("ws-tree-droproot");
          div.classList.add("ws-drop-ok");
          return;
        }
        if (canReorder && dirOf(src) === myDir) {
          e.preventDefault();
          e.stopPropagation();
          e.dataTransfer.dropEffect = "move";
          treeEl.classList.remove("ws-tree-droproot");
          const r = div.getBoundingClientRect();
          const before = (e.clientY - r.top) < r.height / 2;
          div.classList.toggle("ws-drop-before", before);
          div.classList.toggle("ws-drop-after", !before);
          return;
        }
        const g = dropGate(src, myDir);
        if (!g.ok) { state.dragRejected = rejectReason(g); return; }
        state.dragRejected = null;
        e.preventDefault();
        e.stopPropagation();
        e.dataTransfer.dropEffect = "move";
        treeEl.classList.remove("ws-tree-droproot");
        div.classList.add("ws-drop-ok");
      };
      div.ondragleave = () => div.classList.remove("ws-drop-ok", "ws-drop-before", "ws-drop-after");
      div.ondrop = e => {
        if (!state.dragPath || state.dragPath === path) return;
        e.preventDefault();
        e.stopPropagation();
        const src = state.dragPath;
        const dt = state.dragType;
        const myDir = dirOf(path);
        const r = div.getBoundingClientRect();
        const before = (e.clientY - r.top) < r.height / 2;
        state.dragPath = null;
        state.dragType = null;
        clearDropMarks();
        if (dt === "dir") {
          if (canReorder && dirOf(src) === myDir) reorderDirToEnd(myDir, src);
          else doMoveDir(src, myDir);
          return;
        }
        if (canReorder && dirOf(src) === myDir) reorderInDir(myDir, "files", src, path, before);
        else doMove(src, myDir);
      };
      return div;
    }

    function dirRow(name, path, depth) {
      const div = document.createElement("div");
      const acc = effectiveAccess(path);
      const accDef = ACCESS[acc] || ACCESS.edit;
      const open = !state.collapsed.has(path);
      // Jeder Ordner ist per Handle ziehbar (Reihenfolge = immer erlaubt;
      // Moves blockt der Drop-Gate mit Fehlermeldung, nicht der Handle).
      const dirDraggable = !!(canMove || canReorder);
      div.className = "ws-row ws-row-dir px-1 py-1.5 flex items-center gap-1 text-gray-500 hover:bg-gray-100 rounded cursor-pointer";
      // Handle bleibt linksbündig; die Tiefe-Einrückung sitzt am Icon.
      div.style.paddingLeft = "2px";
      div.dataset.path = path;
      div.title = acc !== "edit" ? accDef.label : "";
      div.innerHTML =
        '<span class="ws-drag' + (dirDraggable ? "" : " ws-drag-off") + '"' +
        (dirDraggable ? ' title="Ziehen: Ordner verschieben / sortieren"' : "") + ">⠿</span>" +
        '<span class="shrink-0"' + (depth ? ' style="margin-left:' + (depth * 14) + 'px"' : "") + '>' + (open ? "📂" : "📁") + "</span>" +
        '<span title="' + esc(accDef.label) + '">' + accDef.icon + "</span>" +
        '<span class="truncate flex-1">' + esc(name) + "/</span>";
      div.onclick = () => {
        if (state.collapsed.has(path)) state.collapsed.delete(path);
        else state.collapsed.add(path);
        renderTree();
      };
      div.oncontextmenu = e => showDirMenu(e, path);
      if (dirDraggable) {
        const handle = div.querySelector(".ws-drag");
        handle.draggable = true;
        handle.ondragstart = e => {
          state.dragPath = path;
          state.dragType = "dir";
          state.dragRejected = null;
          e.dataTransfer.effectAllowed = "move";
          try { e.dataTransfer.setData("text/plain", path); } catch (err) { /* IE */ }
          e.stopPropagation();
        };
        handle.ondragend = () => {
          const rejected = state.dragRejected;
          state.dragPath = null;
          state.dragType = null;
          state.dragRejected = null;
          clearDropMarks();
          if (rejected) toast(rejected, "warning");
        };
      }
      // Drop-Target (Ordner) — für Datei- UND Ordner-Drags
      div.ondragover = e => {
        if (!state.dragPath) return;
        const src = state.dragPath;
        if (src === path) return;
        // Ordner-Drag im selben Ordner: neu ordnen (halbe Zeile =
        // davor/danach) — rein Anzeige, ohne Move-Gates.
        if (state.dragType === "dir" && canReorder && dirOf(src) === dirOf(path)) {
          e.preventDefault();
          e.stopPropagation();
          e.dataTransfer.dropEffect = "move";
          treeEl.classList.remove("ws-tree-droproot");
          const r = div.getBoundingClientRect();
          const before = (e.clientY - r.top) < r.height / 2;
          div.classList.toggle("ws-drop-before", before);
          div.classList.toggle("ws-drop-after", !before);
          return;
        }
        const gate = state.dragType === "dir" ? dropGateDir(src, path) : dropGate(src, path);
        if (!gate.ok) { state.dragRejected = rejectReason(gate); return; }
        state.dragRejected = null;
        e.preventDefault();
        e.stopPropagation();
        e.dataTransfer.dropEffect = "move";
        treeEl.classList.remove("ws-tree-droproot");
        div.classList.add("ws-drop-ok");
      };
      div.ondragleave = () => div.classList.remove("ws-drop-ok", "ws-drop-before", "ws-drop-after");
      div.ondrop = e => {
        e.preventDefault();
        e.stopPropagation();
        const src = state.dragPath;
        const dt = state.dragType;
        state.dragPath = null;
        state.dragType = null;
        clearDropMarks();
        if (!src || src === path) return;
        if (dt === "dir") {
          if (canReorder && dirOf(src) === dirOf(path)) {
            const r = div.getBoundingClientRect();
            const before = (e.clientY - r.top) < r.height / 2;
            reorderInDir(dirOf(path), "dirs", src, path, before);
          } else {
            doMoveDir(src, path);
          }
          return;
        }
        doMove(src, path);
      };
      return div;
    }

    function clearDropMarks() {
      treeEl.querySelectorAll(".ws-drop-ok,.ws-drop-before,.ws-drop-after")
        .forEach(el => el.classList.remove("ws-drop-ok", "ws-drop-before", "ws-drop-after"));
      treeEl.classList.remove("ws-tree-droproot");
    }

    // Wurzel des Baums = Drop-Target für „nach Wurzel verschieben“.
    // Greift überall dort, wo keine Zeile den Drop selbst übernommen hat
    // (leerer Bereich, Zeilen ohne gültiges Ziel) — so lässt sich auch
    // zuverlässig aus Ordner in die Wurzel ziehen.
    treeEl.ondragover = e => {
      if (!state.dragPath) return;
      const src = state.dragPath;
      const gate = state.dragType === "dir" ? dropGateDir(src, "") : dropGate(src, "");
      if (!gate.ok) {
        // Zeilen-Targets haben den Gate-Check schon gemacht (bubbling): ihren
        // (spezifischeren) Fehlertext behalten, falls die Wurzel kein Ziel ist.
        state.dragRejected = rejectReason(gate) || state.dragRejected;
        return;
      }
      state.dragRejected = null;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      treeEl.classList.add("ws-tree-droproot");
    };
    treeEl.ondragleave = e => {
      if (!treeEl.contains(e.relatedTarget)) treeEl.classList.remove("ws-tree-droproot");
    };
    treeEl.ondrop = e => {
      e.preventDefault();
      const src = state.dragPath;
      const dt = state.dragType;
      state.dragPath = null;
      state.dragType = null;
      clearDropMarks();
      if (!src) return;
      if (dt === "dir") doMoveDir(src, "");
      else doMove(src, "");
    };

    function renderTree() {
      const top = treeEl.scrollTop;
      treeEl.innerHTML = "";
      if (!apiBase) {
        treeEl.innerHTML = '<div class="text-gray-400 text-xs p-1">' +
          esc(noTaskMsg || emptyMsg) + "</div>";
        treeEl.scrollTop = top;
        return;
      }
      if (!state.files.length && !state.extraDirs.size && !state.folders.length) {
        treeEl.innerHTML = '<div class="text-gray-400 text-xs p-1">' + esc(emptyMsg) + "</div>";
        treeEl.scrollTop = top;
        return;
      }
      const root = buildTree();
      const frag = document.createDocumentFragment();
      const walk = (node, parentPath, depth) => {
        const dirs = [];
        const filesList = [];
        Object.keys(node).forEach(k => {
          const child = node[k];
          const childPath = parentPath ? parentPath + "/" + k : k;
          if (child.file) filesList.push({ name: k, meta: child.file, path: childPath });
          else dirs.push({ name: k, node: child, path: childPath });
        });
        // Ordner zuerst, dann Dateien — jede Gruppe in der gepflegten
        // Reihenfolge (lokal > sort_order > alphabetisch).
        dirs.sort(cmpGroup(parentPath, "dirs", (a, b) => {
          const oa = state.folderOrder[a.path], ob = state.folderOrder[b.path];
          const va = oa == null ? Number.MAX_SAFE_INTEGER : oa;
          const vb = ob == null ? Number.MAX_SAFE_INTEGER : ob;
          if (va !== vb) return va - vb;
          return a.name.localeCompare(b.name);
        }));
        filesList.sort(cmpGroup(parentPath, "files", (a, b) => cmpFileOrder(a.meta, b.meta)));
        dirs.forEach(d => {
          // 👤-Ordner: für Nicht-Tutoren (keine canSetAccess) unsichtbar.
          if (effectiveAccess(d.path) === "hidden" && !canSetAccess) return;
          frag.appendChild(dirRow(d.name, d.path, depth));
          if (!state.collapsed.has(d.path)) walk(d.node, d.path, depth + 1);
        });
        filesList.forEach(f => {
          if (effectiveAccess(f.path) === "hidden" && !canSetAccess) return;
          frag.appendChild(fileRow(f.name, f.meta, f.path, depth));
        });
      };
      walk(root, "", 0);
      treeEl.appendChild(frag);
      treeEl.scrollTop = top;
    }

    // ── Kontextmenüs ──────────────────────────────────────────────
    // Ziel-Ordner für „Verschieben nach:“ (Kontextmenü; Touch-Fallback für
    // Drag & Drop). isDir: src ist ein Ordner — das Gate prüft über den
    // Drop-Gate (inkl. Zugriffs-Klassen-Check der Subtree-Dateien).
    function moveDestDirs(src, isDir) {
      const out = [];
      const name = src.split("/").pop();
      const srcParent = src.includes("/") ? src.slice(0, src.lastIndexOf("/")) : "";
      const seen = new Set();
      const gateDir = (dir) => {
        if (isDir) return dropGateDir(src, dir);
        const dst = dir ? dir + "/" + name : name;
        return moveGate(src, dst);
      };
      const add = (label, dir) => {
        if (dir === srcParent || seen.has(dir)) return;
        const gate = gateDir(dir);
        if (!gate.ok) return;
        seen.add(dir);
        out.push({ label: label, path: dir });
      };
      add("(Wurzel)", "");
      allDirPaths().forEach(d => {
        if (d === src || d.startsWith(src + "/")) return; // nicht in/unter sich selbst
        add(d + "/", d);
      });
      return out;
    }

    function showFileMenu(e, path) {
      e.preventDefault();
      e.stopPropagation();
      const f = state.files.find(x => x.path === path);
      const isInit = !!(f && f.init);
      const items = [];
      items.push({ label: "📂 Öffnen", fn: () => openFile(path) });
      if (isInit) {
        // init.sh-Ergebnis: read-only, nichts weiter zu verwalten.
        showCtxMenu(e.clientX, e.clientY, items);
        return;
      }
      if (canSetAccess) {
        items.push({ sep: true });
        const fixed = systemAccessOf(path);
        if (fixed) {
          // System-Skript: feste Klasse, keine Auswahl-Items.
          items.push({ label: "🔐 Zugriff: " + accessLabel(fixed) +
                            " (System-Skript — festgelegt)", header: true });
        } else {
          const own = f ? f.file_access : null;
          items.push({ label: "🔐 Zugriff (aktuell: " + accessLabel(effectiveAccess(path)) + ")", header: true });
          items.push({ label: (own == null ? "✓ " : "↳ ") + "Erben vom Ordner",
                      fn: () => setAccess(path, false, null) });
          items.push({ label: (own === "readonly" ? "✓ " : "🔒 ") + "Read-only",
                      fn: () => setAccess(path, false, "readonly") });
          items.push({ label: (own === "hidden" ? "✓ " : "👤 ") + "Versteckt",
                      fn: () => setAccess(path, false, "hidden") });
        }
      }
      if (canMove && moveGate(path, path).ok) {
        items.push({ label: "✏️ Umbenennen …", fn: () => renameFile(path) });
        const dirs = moveDestDirs(path);
        if (dirs.length) {
          items.push({ sep: true });
          items.push({ label: "Verschieben nach:", header: true });
          dirs.forEach(d => items.push({
            label: "→ " + d.label,
            fn: () => {
              const dst = d.path ? d.path + "/" + path.split("/").pop() : path.split("/").pop();
              moveFile(path, dst);
            },
          }));
        }
      }
      if (allowMain && effectiveAccess(path) === "edit") {
        if (state.mainFile !== path) {
          items.push({ label: "⭐ Als Main-Datei setzen", fn: () => setMainFile(path) });
        }
      }
      if (canDelete && !readOnlyCheck(path)) {
        items.push({ sep: true });
        items.push({ label: "🗑 Löschen", danger: true, fn: () => deleteFile(path) });
      }
      if (items.length) showCtxMenu(e.clientX, e.clientY, items);
    }

    function showDirMenu(e, dirPath) {
      e.preventDefault();
      e.stopPropagation();
      const items = [];
      if (canCreate) {
        items.push({ label: "＋ Neue Datei …", fn: () => newFileIn(dirPath) });
        items.push({ label: "＋ Neuer Ordner …", fn: () => newFolderIn(dirPath) });
      }
      if (canSetAccess) {
        if (items.length) items.push({ sep: true });
        const own = state.folderMap[dirPath] || null;
        items.push({ label: "🔐 Zugriff (aktuell: " + accessLabel(effectiveAccess(dirPath)) + ")", header: true });
        items.push({ label: (own == null ? "✓ " : "✏️ ") + "Editierbar",
                    fn: () => setAccess(dirPath, true, null) });
        items.push({ label: (own === "readonly" ? "✓ " : "🔒 ") + "Read-only",
                    fn: () => setAccess(dirPath, true, "readonly") });
        items.push({ label: (own === "hidden" ? "✓ " : "👤 ") + "Versteckt",
                    fn: () => setAccess(dirPath, true, "hidden") });
      }
      if (canMove && canDragDir(dirPath)) {
        const dirs = moveDestDirs(dirPath, true);
        if (dirs.length) {
          if (items.length) items.push({ sep: true });
          items.push({ label: "Verschieben nach:", header: true });
          dirs.forEach(d => items.push({
            label: "→ " + d.label,
            fn: () => doMoveDir(dirPath, d.path),
          }));
        }
      }
      if (allowBulk) {
        if (items.length) items.push({ sep: true });
        items.push({ label: "✏️ Ordner umbenennen …", fn: () => renameDir(dirPath) });
        items.push({ label: "🗑 Ordner löschen", danger: true, fn: () => deleteDir(dirPath) });
      }
      if (items.length) showCtxMenu(e.clientX, e.clientY, items);
    }

    // Zugriffs-Klasse setzen (Datei: null = erben / Ordner: null = edit).
    async function setAccess(path, isFolder, access) {
      try {
        const res = await fetch(apiBase + "/access", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ path: path, is_folder: isFolder, access: access }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || res.status);
        await refresh();
        return true;
      } catch (err) {
        toast("Zugriffs-Klasse nicht geändert: " + err.message, "error");
        return false;
      }
    }

    // ── Datei-Operationen ─────────────────────────────────────────
    function validRelPath(p) {
      return !!p && !p.startsWith("/") && !p.split("/").includes("..");
    }

    async function refresh(openPath) {
      if (!apiBase) {
        renderTree();
        return;
      }
      try {
        // no-store: nach Moves/Deletes sonst die gecachte (alte) Liste
        // zurückkommen und Dateien scheinbar „dupliziert“ wirken.
        const res = await fetch(apiBase + "/files", { credentials: "same-origin", cache: "no-store" });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || res.status);
        state.files = data.files || [];
        state.folders = data.folders || [];
        state.folderOrder = data.folder_order || {};
        state.folderMap = {};
        state.folders.forEach(fd => { state.folderMap[fd.path] = fd.own || "edit"; });
        renderTree();
        if (onFilesLoaded) onFilesLoaded(state.files);
        if (openPath) {
          await openFile(openPath);
          return;
        }
        // (Neu)öffnen nur, wenn die aktuell geöffnete Datei fehlt
        if (!state.currentFile || !state.files.some(f => f.path === state.currentFile)) {
          const main = state.mainFile || "";
          const target = (state.files.some(f => f.path === main) && main) ||
            (state.files[0] && state.files[0].path);
          if (target) await openFile(target);
        }
      } catch (err) {
        treeEl.innerHTML =
          '<div class="text-red-500 text-xs p-1">Dateien nicht ladbar: ' +
          esc(err.message) + "</div>";
      }
    }

    async function openFile(path) {
      if (!apiBase) return;
      if (path === state.currentFile) {
        cm.focus();
        return;
      }
      if (state.dirty) {
        // Auto-Save: aktuelle Datei beim Wechsel speichern (statt verwerfen).
        if (!await saveFile()) return;  // Speichern fehlgeschlagen → bleiben
      }
      try {
        const res = await fetch(apiBase + "/files/" + encPath(path), { credentials: "same-origin", cache: "no-store" });
        if (!res.ok) {
          let detail = res.status;
          try { detail = (await res.json()).detail || detail; } catch (err) { /* ignore */ }
          throw new Error(detail);
        }
        const ct = res.headers.get("Content-Type") || "";
        const kind = mediaKind(path, ct);
        if (kind && mediaEl) {
          await showMediaView(kind, path, res);
          return;
        }
        if (kind) {
          toast("Binärdatei — kann nicht im Text-Editor geöffnet werden.", "warning");
          return;
        }
        const text = await res.text();
        closeMediaView();
        state.currentFile = path;
        state.dirty = false;
        if (state.saveTimer) { clearTimeout(state.saveTimer); state.saveTimer = null; }
        cm.setOption("readOnly", readOnlyCheck(path) ? "nocursor" : false);
        cm.setValue(text);
        // Immer Mode setzen (Fallback „text/plain"): sonst bliebe der Mode
        // der vorherigen Datei bei unbekannter Extension hängen.
        const mode = cmModeForPath(path);
        cm.setOption("mode", mode || "text/plain");
        setSaveState("");
        renderTree();
      } catch (err) {
        toast("Datei nicht geöffnet: " + err.message, "error");
      }
    }

    // Liefert true, wenn gespeichert (oder nichts zu speichern) ist.
    async function saveFile() {
      if (state.saveTimer) { clearTimeout(state.saveTimer); state.saveTimer = null; }
      if (!state.currentFile || !state.dirty) return true;
      if (!apiBase) return false;
      if (readOnlyCheck(state.currentFile)) {
        toast("Diese Datei ist read-only (vom Tutor verwaltet).", "warning");
        return false;
      }
      setSaveState("⏳ Speichern …");
      try {
        const res = await fetch(apiBase + "/files/" + encPath(state.currentFile), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ content: cm.getValue() }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || res.status);
        state.dirty = false;
        setSaveState("✓ gespeichert " + new Date().toLocaleTimeString());
        setTimeout(() => setSaveState(""), 3000);
        await refresh();
        return true;
      } catch (err) {
        setSaveState("");
        toast("Speichern fehlgeschlagen: " + err.message, "error");
        return false;
      }
    }

    async function createFile(path) {
      path = String(path || "").trim();
      if (!validRelPath(path)) {
        toast("Ungültiger Pfad (relativ, ohne „..“).", "error");
        return false;
      }
      if (readOnlyCheck(path)) {
        toast("Dieser Bereich ist read-only — dort kann keine Datei angelegt werden.", "warning");
        return false;
      }
      try {
        const res = await fetch(apiBase + "/files", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ path: path, content: "" }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || res.status);
        await refresh(path);
        return true;
      } catch (err) {
        toast("Anlegen fehlgeschlagen: " + err.message, "error");
        return false;
      }
    }

    async function deleteFile(path) {
      if (!confirm("„" + path + "“ wirklich löschen?")) return false;
      return deleteFileQuiet(path);
    }

    async function deleteFileQuiet(path) {
      try {
        const res = await fetch(apiBase + "/files/" + encPath(path), {
          method: "DELETE", credentials: "same-origin",
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || res.status);
        if (state.currentFile === path) clearEditorState();
        if (state.mainFile === path) setMainFileState("");
        return true;
      } catch (err) {
        toast("Löschen fehlgeschlagen: " + err.message, "error");
        return false;
      }
    }

    async function moveFile(src, dst) {
      try {
        const res = await fetch(apiBase + "/files/move", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ src: src, dst: dst }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || res.status);
        if (state.currentFile === src) state.currentFile = dst;
        if (state.mainFile === src) setMainFileState(dst);
        await refresh();
        return true;
      } catch (err) {
        toast("Verschieben fehlgeschlagen: " + err.message, "error");
        return false;
      }
    }

    async function uploadFile(file, path) {
      try {
        const fd = new FormData();
        fd.append("file", file);
        fd.append("path", String(path || "").trim());
        const res = await fetch(apiBase + "/upload", {
          method: "POST", credentials: "same-origin", body: fd,
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || res.status);
        toast("Upload fertig — Sync läuft im Hintergrund.", "success");
        await refresh();
        return true;
      } catch (err) {
        toast("Upload fehlgeschlagen: " + err.message, "error");
        return false;
      }
    }

    // ── Main-Datei ────────────────────────────────────────────────
    function setMainFileState(p) {
      state.mainFile = String(p || "");
    }
    function setMainFile(p) {
      p = String(p || "").trim();
      if (p && !validRelPath(p)) {
        toast("Ungültiger Pfad für die Main-Datei.", "error");
        return;
      }
      setMainFileState(p);
      renderTree();
      if (onMainFileChange) onMainFileChange(p);
    }
    function getMainFile() {
      return state.mainFile;
    }

    // ── Editor-Reset / Medien ─────────────────────────────────────
    function clearEditorState() {
      state.currentFile = null;
      state.dirty = false;
      if (state.saveTimer) { clearTimeout(state.saveTimer); state.saveTimer = null; }
      closeMediaView();
      cm.setOption("readOnly", false);
      cm.setValue("");
      setSaveState("");
      renderTree();
    }

    async function showMediaView(kind, path, res) {
      const blob = await res.blob();
      if (state.mediaUrl) URL.revokeObjectURL(state.mediaUrl);
      const url = URL.createObjectURL(blob);
      state.mediaUrl = url;
      state.currentFile = path;
      state.dirty = false;
      if (state.saveTimer) { clearTimeout(state.saveTimer); state.saveTimer = null; }
      setSaveState("");
      const name = path.split("/").pop();
      cm.getWrapperElement().style.display = "none";
      let mediaHtml, label;
      if (kind === "image") {
        label = "🖼 Bild";
        mediaHtml = '<img src="' + url + '" alt="' + esc(name) + '" class="max-w-full h-auto rounded shadow-sm">';
      } else if (kind === "video") {
        label = "🎬 Video";
        mediaHtml = '<video src="' + url + '" controls class="max-w-full rounded shadow-sm"></video>';
      } else if (kind === "audio") {
        label = "🎧 Audio";
        mediaHtml = '<div class="w-full bg-white border border-gray-200 rounded-lg p-4"><audio src="' + url + '" controls class="w-full"></audio></div>';
      } else if (kind === "pdf") {
        label = "📄 PDF";
        mediaHtml = '<iframe src="' + url + '" title="' + esc(name) + '" class="w-full h-full bg-white rounded shadow-sm"></iframe>';
      } else {
        label = "📦 Binärdatei";
        mediaHtml = '<div class="text-center text-gray-500 text-sm bg-white border border-gray-200 rounded-lg p-6">' +
          "Diese Datei kann nicht als Vorschau angezeigt werden." +
          '<div class="mt-3"><a href="' + url + '" download="' + esc(name) + '" ' +
          'class="text-blue-700 hover:text-blue-900 border border-blue-300 hover:border-blue-500 px-3 py-2 rounded-lg transition text-sm inline-block">⬇️ Datei herunterladen</a></div></div>';
      }
      mediaEl.classList.remove("hidden");
      mediaEl.innerHTML =
        '<div class="h-full overflow-auto p-3 flex flex-col gap-2">' +
        '<div class="flex items-center gap-2 text-xs text-gray-500">' +
        "<span>" + label + "</span>" +
        '<span class="font-mono truncate">' + esc(path) + "</span>" +
        (kind !== "binary"
          ? '<a href="' + url + '" download="' + esc(name) + '" ' +
            'class="ml-auto text-blue-600 hover:underline shrink-0">⬇️ Download</a>' : "") +
        "</div>" +
        '<div class="flex-1 min-h-[200px] flex items-start justify-center bg-gray-100 rounded-lg p-2 overflow-auto">' +
        mediaHtml +
        "</div>" +
        "</div>";
      if (kind === "pdf") {
        const frame = mediaEl.querySelector("iframe");
        if (frame) frame.style.height = "500px";
      }
      renderTree();
    }

    function closeMediaView() {
      if (!mediaEl) {
        if (state.mediaUrl) { URL.revokeObjectURL(state.mediaUrl); state.mediaUrl = null; }
        return;
      }
      if (!mediaEl.classList.contains("hidden")) {
        mediaEl.classList.add("hidden");
        mediaEl.innerHTML = "";
      }
      cm.getWrapperElement().style.display = "";
      if (state.mediaUrl) {
        URL.revokeObjectURL(state.mediaUrl);
        state.mediaUrl = null;
      }
    }

    // ── Neue Datei / Ordner ───────────────────────────────────────
    function newFileIn(dir) {
      const prefix = dir ? dir + "/" : "";
      const name = prompt("Dateiname in " + (dir || "Wurzel") + ":", "neue_datei.py");
      if (!name || !name.trim()) return;
      const n = name.trim();
      if (n.includes("/") || n.includes("\\")) {
        toast("Nur ein Dateiname (keine Pfadtrenner) — für Unterverzeichnisse erst einen Ordner anlegen.", "error");
        return;
      }
      createFile(prefix + n);
    }

    function newFolderIn(dir) {
      const prefix = dir ? dir + "/" : "";
      const name = prompt("Name des neuen Ordners in " + (dir || "Wurzel") + ":", "neuer_ordner");
      if (!name || !name.trim()) return;
      const n = name.trim().replace(/[/\\]/g, "");
      if (!n) return;
      const p = prefix + n;
      if (state.files.some(f => f.path === p || f.path.startsWith(p + "/"))) {
        toast("Es existiert bereits eine Datei/dieser Ordner unter „" + p + "“.", "warning");
        return;
      }
      state.extraDirs.add(p);
      // alle Eltern ebenfalls sichtbar machen
      const parts = p.split("/");
      for (let i = 1; i < parts.length; i++) state.extraDirs.add(parts.slice(0, i).join("/"));
      renderTree();
      toast("Ordner angelegt (bleibt dauerhaft, sobald eine Datei darin liegt " +
            "oder eine Zugriffs-Klasse gesetzt wird).", "success");
    }

    // ── Ordner-Umbenennen / -Löschen (Bulk über Datei-Moves) ──────
    function filesInDir(dir) {
      return state.files.filter(f => f.path.startsWith(dir + "/"));
    }

    async function renameDir(dir) {
      const name = dir.split("/").pop();
      const parent = dir.slice(0, dir.length - name.length);
      const nn = prompt("Neuer Name für Ordner „" + dir + "“:", name);
      if (!nn) return;
      const n = nn.trim().replace(/[/\\]/g, "");
      if (!n || n === name) return;
      const newDir = parent + n;
      if (state.files.some(f => f.path === newDir || f.path.startsWith(newDir + "/"))) {
        toast("„" + newDir + "“ existiert bereits.", "error");
        return;
      }
      const inDir = filesInDir(dir);
      const hasRow = state.folders.some(fd => fd.path === dir || fd.path.startsWith(dir + "/"));
      if (!inDir.length && !hasRow) {
        if (state.extraDirs.has(dir)) {
          state.extraDirs.delete(dir);
          state.extraDirs.add(newDir);
          renderTree();
        }
        return;
      }
      if (!confirm("„" + dir + "“ in „" + newDir + "“ umbenennen?\n" +
        inDir.length + " Datei(en) werden verschoben (Zugriffs-Klassen bleiben erhalten).")) return;
      await moveDirImpl(dir, newDir);
    }

    async function deleteDir(dir) {
      const inDir = filesInDir(dir);
      const rows = state.folders.filter(fd => fd.path === dir || fd.path.startsWith(dir + "/"));
      if (!inDir.length && !rows.length && !state.extraDirs.has(dir)) return;
      if (!confirm("Ordner „" + dir + "“ löschen?\n" +
        inDir.length + " Datei(en) werden endgültig entfernt.")) return;
      for (const f of inDir) {
        if (!await deleteFileQuiet(f.path)) return;
      }
      if (state.extraDirs.has(dir)) state.extraDirs.delete(dir);
      // Persistierte Zugriffs-Klassen des (nun leeren) Ordners entfernen:
      for (const r of rows) {
        if (!await setAccess(r.path, true, null)) return;
      }
      await refresh();
    }

    function renameFile(path) {
      const name = path.split("/").pop();
      const dir = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
      const nn = prompt("Neuer Name für „" + name + "“:", name);
      if (!nn) return;
      const n = nn.trim().replace(/[/\\]/g, "");
      if (!n || n === name) return;
      const dst = dir ? dir + "/" + n : n;
      if (state.files.some(f => f.path === dst)) {
        toast("„" + dst + "“ existiert bereits.", "error");
        return;
      }
      const gate = moveGate(path, dst);
      if (!gate.ok) { toast(gate.reason || "Nicht möglich.", "warning"); return; }
      moveFile(path, dst);
    }

    // ── Editor-Events ─────────────────────────────────────────────
    // Auto-Save: 3 s nach der letzten Änderung (auch beim Dateiwechsel wird
    // über openFile→saveFile sofort gespeichert).
    cm.on("change", () => {
      if (!apiBase || cm.getOption("readOnly")) return;
      if (!state.dirty) {
        state.dirty = true;
        setSaveState("● ungespeichert");
      }
      if (state.saveTimer) clearTimeout(state.saveTimer);
      state.saveTimer = setTimeout(() => saveFile(), 3000);
    });

    // Seitenwechsel/Tab zu: ausstehende Änderungen per keepalive-PUT retten.
    window.addEventListener("beforeunload", () => {
      if (!apiBase || !state.dirty || !state.currentFile) return;
      if (readOnlyCheck(state.currentFile)) return;
      try {
        fetch(apiBase + "/files/" + encPath(state.currentFile), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          keepalive: true,
          body: JSON.stringify({ content: cm.getValue() }),
        });
      } catch (err) { /* ignore */ }
    });

    document.addEventListener("keydown", e => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        saveFile();
      }
    });

    return {
      get files() { return state.files; },
      get folders() { return state.folders; },
      get currentFile() { return state.currentFile; },
      get dirty() { return state.dirty; },
      accessOf: effectiveAccess,
      isInitPath,
      refresh,
      openFile,
      saveFile,
      createFile,
      deleteFile,
      moveFile,
      uploadFile,
      newFileIn,
      newFolderIn,
      getMainFile,
      setMainFile,
      getCurrentFile: () => state.currentFile,
      isDirty: () => state.dirty,
      clearEditorState,
      closeMediaView,
      // für Tests/Debug
      _state: state,
    };
  }

  window.WorkspaceUI = {
    ACCESS,
    ACCESS_RANK,
    accessLabel,
    fmtBytes,
    fileIcon,
    cmModeForPath,
    mediaKind,
    init,
  };
})();
