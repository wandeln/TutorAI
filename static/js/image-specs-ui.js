/*
 * AICampus Image-Spec-UI — gemeinsame UI-Logik für die Image-Spec-Verwaltung
 * (Admin-Konsole: globale Specs, Kurs-Settings: Kurs-Specs).
 *
 * - Spec = "Rezept" (reines Dockerfile, Name in eigener DB-Spalte)
 * - Installation = konkretes Image auf einer Engine (deterministisches Tag)
 *
 * Das Modul rendert eine komplette Sektion in einen Container-Element:
 *   1. Spec-Liste als zusammenklappbare Karten (Design analog
 *      Compute-Engine-Zeilen / Kurs-Material-Import):
 *      - Summary: editierbarer Name (wie Engine-Zeilen), Scope-Badge,
 *        [📦 installieren], Chevron
 *      - Aufgeklappt: Dockerfile-Editor, dann LLM-Box (Prompt +
 *        „🤖 Draft generieren" / „🤖 Änderung anwenden", Design wie
 *        die LLM-Boxen bei Skript-Kapiteln), darunter rot
 *        „🗑 Spec entfernen…" (bzw. „✕ Entwurf verwerfen" bei neuen,
 *        noch nicht gespeicherten Specs) und rechts „💾 Speichern"
 *      - Read-only-Scopes (readonlyScopes): nur Dockerfile-Ansicht, keine Aktionen
 *   2. „＋ Image-spec hinzufügen" unter der Liste → neue (transiente) Karte,
 *      die erst mit „💾 Speichern" persistiert wird
 *   3. Install-Modal (optional, deaktivierbar via installModal: false):
 *      Engine-Picker (Quellen + Engines mit Health/GPU-Badge), Install-Button,
 *      Image-Liste der Engine (mit Build-Status + Löschen)
 *
 * Aufruf (s. templates/admin/dashboard.html, templates/course/settings.html):
 *   window.AICampusImageSpecs.init({
 *     mount:        HTMLElement,   // Container, in den die Sektion gerendert wird
 *     specsUrl:     string,        // z. B. "/api/admin/image-specs"
 *     title:        string,        // Überschrift (optional)
 *     coursesUrl:   string,        // optional — enables "Kurs-Engines"-Quelle
 *     readonlyScopes: [string],    // Specs in diesen Scopes sind read-only
 *     engineSources:[{id, label, usesCourse?, load}],
 *     //   load: async (courseId) => Agenten-Liste (courseId nur bei usesCourse)
 *     install:      (specId, sourceId, engineName, courseId) => Promise,
 *     uninstall:    (specId, sourceId, engineName, ref, courseId) => Promise,
 *     engineImages: (sourceId, engineName, courseId) => Promise<images[]>,
 *     installModal: bool,          // false → kein 📦-Button/Install-Modal
 *     onSpecSaved:  (spec) => void // optional, nach erfolgreichem Speichern
 *   });
 *
 * Agenten-Liste-Elemente (aus workspace_service.status()):
 *   {name, url, healthy, error, gpu, gpu_info}
 * Image-Elemente (Agent GET /images):
 *   {ref, size, created, spec_name, building?, build_log?, build_failed?}
 */
(function () {
  "use strict";

  function _toast(msg, type) {
    if (window.showToast) { showToast(msg, type || "info"); }
    else { alert(msg); }
  }

  function _esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function _fetchJson(url, opts) {
    const res = await fetch(url, Object.assign({ credentials: "same-origin" }, opts || {}));
    let data = null;
    try { data = await res.json(); } catch (e) { /* nicht-JSON */ }
    if (!res.ok) {
      throw new Error((data && data.detail) || ("HTTP " + res.status));
    }
    return data;
  }

  function init(opts) {
    const mount = opts.mount;
    const specsUrl = opts.specsUrl;
    const engineSources = opts.engineSources || [];
    const readonlyScopes = opts.readonlyScopes || [];
    if (!mount || !specsUrl) { return; }

    let specs = [];
    let installSpec = null;       // Spec im Install-Modal
    let pollTimer = null;

    // ── Sektion rendern ──────────────────────────────────────────
    mount.innerHTML =
      "<style>" +
      "/* Chevron der Spec-Zeilen: animiert (analog Compute-Engines) */" +
      ".tspec-card .tspec-chevron { transition: transform 0.2s ease; }" +
      ".tspec-card[open] .tspec-chevron { transform: rotate(180deg); }" +
      "</style>" +
      '<h3 class="text-sm font-semibold text-gray-700 mb-2">' + _esc(opts.title || "Image-Specs") + "</h3>" +
      '<p class="text-xs text-gray-400 mb-3">Spec = Image-„Rezept“ (reines Dockerfile auf öffentlichem ' +
      "Base-Image). Installation baut das konkrete Image auf einer Engine (deterministisches Tag, idempotent — " +
      'No-op-Specs mit nur FROM bauen nichts). Neue Specs und Änderungen werden pro Zeile per „💾 Speichern“ übernommen.</p>' +
      '<div class="tspec-list"></div>' +
      '<button type="button" class="tspec-add-btn mt-1 text-xs px-2.5 py-1 rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-100 transition">＋ Image-spec hinzufügen</button>';

    const listEl = mount.querySelector(".tspec-list");
    mount.querySelector(".tspec-add-btn").onclick = addSpecCard;

    function _readonly(s) {
      return readonlyScopes.indexOf(s.scope) >= 0;
    }

    // ── Spec-Karten ──────────────────────────────────────────────
    // Offen-Zustand (persistierte Karten, per ID) und Entwürfe (transiente
    // Karten inkl. Feldwerten) vor dem DOM-Tausch aus dem Live-DOM
    // übernehmen — wie bei den Compute-Engines: Toggle-Events beim
    // innerHTML-Tausch feuern unzuverlässig, das DOM-Lesen ist eindeutig.
    function _captureCardStates() {
      const openById = {};
      const transient = [];
      listEl.querySelectorAll("details.tspec-card").forEach(d => {
        if (d.dataset.transient) {
          transient.push({
            open: d.open,
            dirty: !!d._dirty,
            name: (d.querySelector(".tspec-name") || { value: "" }).value,
            desc: (d.querySelector(".tspec-desc") || { value: "" }).value,
            yaml: (d.querySelector(".tspec-yaml") || { value: "" }).value,
          });
        } else if (d.dataset.specId) {
          openById[d.dataset.specId] = d.open;
        }
      });
      return { openById, transient };
    }

    async function loadSpecs() {
      const states = _captureCardStates();
      try {
        const data = await _fetchJson(specsUrl);
        specs = data.specs || [];
      } catch (err) {
        listEl.innerHTML = '<div class="p-3 text-red-500 text-xs">Specs nicht ladbar: ' + _esc(err.message) + "</div>";
        return;
      }
      listEl.innerHTML = "";
      if (!specs.length && !states.transient.length) {
        listEl.innerHTML = '<div class="tspec-empty text-xs text-gray-400 py-1">Keine Image-Specs vorhanden.</div>';
      }
      specs.forEach(s => {
        const d = _specCard(s);
        d.open = !!states.openById[s.id];
        listEl.appendChild(d);
      });
      states.transient.forEach(t => {
        const d = _specCard(null, t);
        d.open = !!t.open;
        listEl.appendChild(d);
      });
    }

    // Eine Spec-Karte (details). s = persistierte Spec (null → neuer
    // Entwurf), t = optional erhaltener Transient-State (Feldwerte).
    function _specCard(s, t) {
      const ro = s ? _readonly(s) : false;
      const isNew = !s;
      const d = document.createElement("details");
      d.className = "tspec-card border border-gray-200 rounded-lg bg-white mb-3";
      if (isNew) { d.dataset.transient = "1"; }
      else { d.dataset.specId = s.id; }

      const descPlaceholder = isNew
        ? "Beschreibung: z. B. Python-Umgebung für Deep Learning (PyTorch, torchvision)"
        : "Änderungswunsch: z. B. „Füge noch das Paket nvidia-warp hinzu“";
      const llmTitle = isNew ? "🤖 LLM-Draft generieren" : "🤖 Mit LLM anpassen";
      const llmBtnLabel = isNew ? "Draft generieren" : "Änderung anwenden";

      d.innerHTML =
        '<summary class="flex items-center gap-2 sm:gap-3 px-3 sm:px-4 py-2.5 sm:py-3 cursor-pointer select-none list-none [&::-webkit-details-marker]:hidden">' +
        (ro
          ? '<span class="tspec-sum-name font-mono text-sm font-semibold text-gray-800 truncate min-w-0">' + _esc(s.name) + "</span>"
          : '<input type="text" class="tspec-name w-full min-w-0 sm:w-52 border border-gray-300 rounded px-2 py-1 text-sm font-mono" placeholder="Name (Slug, z. B. dl-torch)" spellcheck="false" title="Name (Slug) — Änderungen per „💾 Speichern“ übernehmen">'
        ) +
        (s ? (ro
          ? '<span class="text-[10px] px-1 rounded bg-blue-50 text-blue-600 whitespace-nowrap" title="Globale Specs werden in der Admin-Konsole verwaltet">global (read-only)</span>'
          : '<span class="text-[10px] px-1 rounded bg-indigo-50 text-indigo-600 whitespace-nowrap">kurs</span>')
          : '<span class="text-[10px] px-1 rounded bg-gray-100 text-gray-500 whitespace-nowrap">neuer Entwurf</span>') +
        '<span class="ml-auto flex items-center gap-2 flex-shrink-0">' +
        (!ro && !isNew && opts.installModal !== false
          ? '<button type="button" class="tspec-install text-xs px-2 py-1 rounded border border-green-200 text-green-700 hover:bg-green-50" title="Auf Engine installieren / Images anzeigen">📦</button>'
          : "") +
        '<svg class="tspec-chevron h-5 w-5 text-gray-400 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>' +
        "</span>" +
        "</summary>" +
        '<div class="px-4 pb-4 pt-3 border-t border-gray-100">' +
        '<label class="block text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-1">Dockerfile' + (ro ? ", read-only" : "") + '</label>' +
        '<textarea class="tspec-yaml w-full border ' + (ro ? "border-gray-200 bg-gray-50" : "border-gray-300") + ' rounded px-2 py-1.5 text-xs font-mono" rows="10" ' + (ro ? "readonly " : "") + 'spellcheck="false"></textarea>' +
        (ro ? "":
          '<div class="mt-3 bg-purple-50 border border-purple-300 rounded-lg p-3">' +
          '<div class="text-xs font-semibold text-gray-700 mb-2">' + llmTitle + "</div>" +
          '<textarea class="tspec-desc w-full border border-purple-300 rounded px-2 py-1.5 text-xs bg-white mb-2" rows="2" placeholder="' + descPlaceholder + '" spellcheck="false"></textarea>' +
          '<div class="flex justify-end">' +
          '<button type="button" class="tspec-llm bg-purple-600 text-white px-3 py-1.5 rounded-lg text-xs hover:bg-purple-700 transition font-medium">' + llmBtnLabel + "</button>" +
          "</div>" +
          "</div>" +
          '<div class="tspec-card-error hidden mt-2 px-3 py-2 rounded-lg text-xs bg-red-50 border border-red-200 text-red-700"></div>' +
          '<div class="mt-3 flex flex-wrap gap-2 items-center justify-between">' +
          '<button type="button" class="tspec-card-del text-xs px-2.5 py-1 rounded-lg border ' +
          (isNew
            ? 'border-gray-300 text-gray-600 hover:bg-gray-100" title="Entwurf verwerfen (noch nicht gespeichert)">✕ Entwurf verwerfen'
            : 'border-red-200 text-red-600 hover:bg-red-50" title="Spec löschen — bereits installierte Images bleiben auf den Engines">🗑 Spec entfernen…') +
          "</button>" +
          '<button type="button" class="tspec-save text-xs px-3 py-1 rounded-lg bg-green-600 text-white hover:bg-green-700 transition">💾 Speichern</button>' +
          "</div>"
        ) +
        "</div>";

      const nameIn = d.querySelector(".tspec-name");
      const descIn = d.querySelector(".tspec-desc");
      const yamlTa = d.querySelector(".tspec-yaml");
      const errBox = d.querySelector(".tspec-card-error");
      const cardError = (msg) => {
        if (!errBox) { return; }
        errBox.textContent = msg;
        errBox.classList.toggle("hidden", !msg);
      };

      // Initialwerte (persistierte Spec bzw. erhaltener Entwurfs-State)
      if (nameIn) { nameIn.value = t ? t.name : (s ? s.name : ""); }
      if (descIn) { descIn.value = t ? t.desc : ""; }
      if (yamlTa) { yamlTa.value = t ? t.yaml : (s ? s.dockerfile : ""); }
      let dirty = !!(t && t.dirty);
      d._dirty = dirty;

      // Interaktive Elemente im Summary dürfen das Details nicht umklappen
      d.querySelector("summary").addEventListener("click", (e) => {
        if (e.target.closest("input,button,select,textarea,label,a")) { e.preventDefault(); }
      });

      // Ungespeicherte Änderungen: Schließen erst nach Confirm
      [nameIn, descIn, yamlTa].forEach(el => {
        if (el) {
          el.addEventListener("input", () => { dirty = true; d._dirty = true; });
        }
      });
      d.addEventListener("toggle", () => {
        if (!d.open && dirty && !confirm("Ungespeicherte Änderungen an dieser Image-Spec verwerfen?")) {
          d.open = true;
        }
      });

      const installBtn = d.querySelector(".tspec-install");
      if (installBtn) { installBtn.onclick = () => openInstallModal(s); }

      const llmBtn = d.querySelector(".tspec-llm");
      if (llmBtn) {
        llmBtn.onclick = async function () {
          const description = descIn.value.trim();
          const name = nameIn.value.trim();
          if (!description) {
            cardError(isNew
              ? 'Bitte zuerst eine Beschreibung eingeben (Basis für den LLM-Draft).'
              : 'Bitte zuerst einen Änderungswunsch eingeben.');
            return;
          }
          if (!name) {
            cardError('Bitte zuerst einen Namen eingeben (Dockerfiles enthalten keinen Namen).');
            return;
          }
          llmBtn.disabled = true;
          const oldLabel = llmBtn.textContent;
          llmBtn.textContent = "⏳ Generiert …";
          cardError("");
          try {
            const data = await _fetchJson(specsUrl + "/generate", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                description: description,
                name: name,
                current_dockerfile: s ? s.dockerfile : null,
              }),
            });
            if (nameIn && !nameIn.value.trim()) {
              nameIn.value = data.name || "";
            }
            yamlTa.value = data.dockerfile || "";
            dirty = true;
            d._dirty = true;
            cardError(s
              ? 'Dockerfile per LLM angepasst (Entwurf) — bitte prüfen, dann speichern.'
              : 'LLM-Draft erzeugt — bitte prüfen, dann speichern.');
          } catch (err) {
            cardError("LLM-Generierung fehlgeschlagen: " + err.message);
          } finally {
            llmBtn.disabled = false;
            llmBtn.textContent = oldLabel;
          }
        };
      }

      const saveBtn = d.querySelector(".tspec-save");
      if (saveBtn) {
        saveBtn.onclick = async function () {
          const name = nameIn.value.trim();
          const dockerfile = yamlTa.value.trim();
          if (!name) { cardError("Name fehlt."); return; }
          if (!dockerfile) { cardError("Dockerfile fehlt."); return; }
          saveBtn.disabled = true;
          cardError("");
          try {
            const body = JSON.stringify({ name: name, dockerfile: dockerfile });
            const data = await _fetchJson(
              s ? specsUrl + "/" + s.id : specsUrl,
              { method: s ? "PUT" : "POST",
                headers: { "Content-Type": "application/json" }, body: body });
            _toast("Spec „" + (data.name || name) + "“ gespeichert.", "success");
            d.remove();  // Karte kommt via API-Reload zurück
            loadSpecs();
            if (typeof opts.onSpecSaved === "function") { opts.onSpecSaved(data); }
          } catch (err) {
            cardError("Speichern fehlgeschlagen: " + err.message);
          } finally {
            saveBtn.disabled = false;
          }
        };
      }

      const delBtn = d.querySelector(".tspec-card-del");
      if (delBtn) {
        delBtn.onclick = async () => {
          if (isNew) { d.remove(); return; }
          if (!confirm('Image-Spec „' + s.name + '“ wirklich löschen?\n\n' +
                       'Bereits installierte Images bleiben auf den Engines erhalten.')) { return; }
          try {
            await _fetchJson(specsUrl + "/" + s.id, { method: "DELETE" });
            _toast("Spec „" + s.name + "“ gelöscht.", "success");
            loadSpecs();
          } catch (err) {
            _toast("Löschen fehlgeschlagen: " + err.message, "error");
          }
        };
      }
      return d;
    }

    function addSpecCard() {
      const hint = listEl.querySelector(".tspec-empty");
      if (hint) { hint.remove(); }
      const d = _specCard(null);
      d.open = true;
      listEl.appendChild(d);
      const nameIn = d.querySelector(".tspec-name");
      if (nameIn) { nameIn.focus(); nameIn.select(); }
      d.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    // ── Install-Modal (Engine-Picker + Image-Liste) ──────────────
    // Deaktivierbar (installModal: false, z. B. Admin: Installation erfolgt
    // pro Engine in der Engine-Liste) → Rest des Modals wird nicht gebaut.
    if (opts.installModal === false) {
      loadSpecs();
      return;
    }
    const imodal = document.createElement("div");
    imodal.className = "hidden fixed inset-0 z-50 bg-black bg-opacity-50 flex items-center justify-center p-4";
    imodal.innerHTML =
      '<div class="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] flex flex-col">' +
      '<div class="p-3 border-b border-gray-200 flex items-center gap-2">' +
      '<span class="imodal-title text-sm font-semibold text-gray-700 flex-1">Auf Engine installieren</span>' +
      '<button type="button" class="imodal-close text-gray-400 hover:text-gray-600 text-lg px-2" title="Schließen">✕</button>' +
      "</div>" +
      '<div class="p-4 space-y-3">' +
      '<div class="grid grid-cols-1 md:grid-cols-2 gap-3">' +
      '<div>' +
      '<label class="block text-xs font-medium text-gray-600 mb-1">Engine-Quelle</label>' +
      '<select class="imodal-source w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"></select>' +
      "</div>" +
      '<div class="imodal-course-wrap hidden">' +
      '<label class="block text-xs font-medium text-gray-600 mb-1">Kurs</label>' +
      '<select class="imodal-course w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"></select>' +
      "</div>" +
      "<div>" +
      '<label class="block text-xs font-medium text-gray-600 mb-1">Engine</label>' +
      '<select class="imodal-engine w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"></select>' +
      "</div>" +
      "</div>" +
      '<div class="flex gap-2">' +
      '<button type="button" class="imodal-install bg-green-600 text-white px-3 py-1.5 rounded-lg text-sm hover:bg-green-700 transition">📦 Installieren</button>' +
      '<button type="button" class="imodal-images text-xs px-3 py-1.5 rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-100 transition">⟳ Images der Engine</button>' +
      "</div>" +
      '<div class="imodal-hint text-xs text-gray-400">Installation ist idempotent: vorhandene Images (gleiche Spec) bleiben, ' +
      "sonst startet ein Build im Hintergrund.</div>" +
      '<div class="imodal-imglist border border-gray-200 rounded-lg divide-y divide-gray-100 text-xs text-gray-400 min-h-[3rem] p-2">(noch keine Images geladen)</div>' +
      "</div>" +
      "</div>";
    document.body.appendChild(imodal);

    const srcSel = imodal.querySelector(".imodal-source");
    const courseWrap = imodal.querySelector(".imodal-course-wrap");
    const courseSel = imodal.querySelector(".imodal-course");
    const engSel = imodal.querySelector(".imodal-engine");
    const imgList = imodal.querySelector(".imodal-imglist");
    let enginesCache = {};   // (sourceId + ":" + courseId) → agents[]
    let coursesLoaded = false;

    function _currentSource() {
      return engineSources.find(s => s.id === srcSel.value) || engineSources[0];
    }
    function _currentCourseId() {
      return _currentSource() && _currentSource().usesCourse ? (courseSel.value || null) : null;
    }
    function _cacheKey(sourceId, courseId) {
      return sourceId + ":" + (courseId || "");
    }

    function openInstallModal(spec) {
      installSpec = spec;
      imodal.querySelector(".imodal-title").textContent = "Installieren: " + spec.name;
      srcSel.innerHTML = engineSources.map(s =>
        '<option value="' + _esc(s.id) + '">' + _esc(s.label) + "</option>").join("");
      engSel.innerHTML = "";
      imgList.innerHTML = "(noch keine Images geladen)";
      imodal.classList.remove("hidden");
      loadEnginesForSource();
    }
    function closeInstallModal() {
      imodal.classList.add("hidden");
      installSpec = null;
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    }
    imodal.querySelector(".imodal-close").onclick = closeInstallModal;
    imodal.addEventListener("click", (e) => {
      if (e.target === imodal) { closeInstallModal(); }
    });

    async function loadCoursesIfNeeded() {
      if (!opts.coursesUrl || coursesLoaded) { return; }
      coursesLoaded = true;
      courseSel.innerHTML = '<option value="">(Kurs wählen …)</option>';
      try {
        const courses = await _fetchJson(opts.coursesUrl);
        (courses || []).forEach(c => {
          const opt = document.createElement("option");
          opt.value = c.id;
          opt.textContent = c.name;
          courseSel.appendChild(opt);
        });
      } catch (err) {
        courseSel.innerHTML = '<option value="">Kurse nicht ladbar: ' + _esc(err.message) + "</option>";
      }
    }

    srcSel.onchange = loadEnginesForSource;
    courseSel.onchange = loadEnginesForSource;

    async function loadEnginesForSource() {
      const source = _currentSource();
      if (!source) { return; }
      courseWrap.classList.toggle("hidden", !source.usesCourse);
      engSel.disabled = true;
      if (source.usesCourse) {
        await loadCoursesIfNeeded();  // einmalig — KURZ VOR der Wert-Prüfung,
        if (!courseSel.value) {       // sonst würde innerHTML=… die Auswahl werfen
          engSel.innerHTML = '<option value="">(Kurs wählen)</option>';
          return;
        }
      }
      const courseId = _currentCourseId();
      const key = _cacheKey(source.id, courseId);
      let agents = enginesCache[key];
      if (!agents) {
        try {
          agents = await source.load(courseId);
          enginesCache[key] = agents;
        } catch (err) {
          engSel.innerHTML = '<option value="">Fehler: ' + _esc(err.message) + "</option>";
          return;
        }
      }
      engSel.innerHTML = agents.length
        ? agents.map(a =>
            '<option value="' + _esc(a.name) + '">' +
            (a.healthy ? "🟢" : "🔴") + " " + _esc(a.name) +
            (a.gpu_info ? " · " + _esc(a.gpu_info) : (a.gpu ? " · GPU" : "")) +
            (a.healthy ? "" : " (offline)") +
            "</option>").join("")
        : '<option value="">(keine Engines in dieser Quelle)</option>';
      engSel.disabled = false;
    }

    function _imgRow(img) {
      const row = document.createElement("div");
      let status = "";
      if (img.building) {
        status = '<span class="px-1.5 py-0.5 rounded bg-blue-50 text-blue-600 font-medium">🔄 buildet</span>';
      } else if (img.build_failed) {
        status = '<span class="px-1.5 py-0.5 rounded bg-red-50 text-red-600 font-medium">❌ build fehlgeschlagen</span>';
      }
      const log = (img.build_log && (img.building || img.build_failed))
        ? '<pre class="mt-1 max-h-24 overflow-y-auto bg-gray-50 border border-gray-200 rounded p-1.5 whitespace-pre-wrap text-[10px] text-gray-500">' + _esc(img.build_log) + "</pre>"
        : "";
      row.innerHTML =
        '<div class="flex items-center gap-2">' +
        '<span class="font-mono">' + _esc(img.ref) + "</span>" +
        (img.spec_name ? '<span class="text-[10px] px-1 rounded bg-indigo-50 text-indigo-600" title="Spec-Name">spec: ' + _esc(img.spec_name) + "</span>" : "") +
        status +
        '<span class="text-gray-400 ml-auto">' + _esc(img.size || "") + "</span>" +
        '<button class="imodal-imgdel text-red-500 hover:text-red-700 px-1" title="Image löschen">🗑</button>' +
        "</div>" + log;
      row.querySelector(".imodal-imgdel").onclick = async () => {
        const doDelete = (force) =>
          opts.uninstall(installSpec.id, srcSel.value, engSel.value, img.ref, _currentCourseId(), force);
        if (!confirm("Image „" + img.ref + "“ von der Engine löschen?")) { return; }
        try {
          let data;
          try {
            data = await doDelete(false);
          } catch (err) {
            if (!/genutzt/.test(err.message || "")) { throw err; }
            if (!confirm("Dieses Image wird von Workspace-Container(s) genutzt.\n\nGewaltsam löschen? Die betroffenen Container werden beendet und entfernt.\nWorkspace-Dateien bleiben in ihrem Volume erhalten; die Umgebungen sind danach\nnicht mehr startbar, bis ein neues Image für die Aufgabenstellung gesetzt ist —\ndanach sind die Daten wieder verfügbar.")) { throw err; }
            data = await doDelete(true);
          }
          _toast("Image gelöscht." + (data && data.removed_containers ? " (" + data.removed_containers + " Container beendet)" : ""), "success");
          loadEngineImages();
        } catch (err) {
          _toast("Löschen fehlgeschlagen: " + err.message, "error");
        }
      };
      return row;
    }

    function renderImageList(images) {
      imgList.innerHTML = "";
      if (!images.length) {
        imgList.innerHTML = "(keine Images auf dieser Engine)";
        return;
      }
      images.forEach(img => imgList.appendChild(_imgRow(img)));
      const building = images.some(i => i.building);
      if (building && !pollTimer) {
        pollTimer = setInterval(loadEngineImages, 5000);
      } else if (!building && pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    async function loadEngineImages() {
      const engineName = engSel.value;
      if (!engineName) { imgList.innerHTML = "(Engine wählen, um ihre Images zu sehen)"; return; }
      imgList.innerHTML = "(lade …)";
      try {
        const images = await opts.engineImages(srcSel.value, engineName, _currentCourseId());
        renderImageList(images);
      } catch (err) {
        imgList.innerHTML = '<span class="text-red-500">Images nicht ladbar: ' + _esc(err.message) + "</span>";
      }
    }
    engSel.onchange = loadEngineImages;
    imodal.querySelector(".imodal-images").onclick = loadEngineImages;

    imodal.querySelector(".imodal-install").onclick = async function () {
      const btn = this;
      const engineName = engSel.value;
      if (!engineName) { _toast("Bitte eine Engine wählen.", "warning"); return; }
      btn.disabled = true;
      btn.textContent = "⏳ Installiert …";
      try {
        await opts.install(installSpec.id, srcSel.value, engineName, _currentCourseId());
        _toast("Installation auf „" + engineName + "“ gestartet/vollendet.", "success");
        loadEngineImages();
      } catch (err) {
        _toast("Installation fehlgeschlagen: " + err.message, "error");
      } finally {
        btn.disabled = false;
        btn.textContent = "📦 Installieren";
      }
    };

    // ── Start ────────────────────────────────────────────────────
    loadSpecs();
  }

  window.AICampusImageSpecs = { init: init };
})();
