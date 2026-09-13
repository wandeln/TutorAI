"""
Serverseitiges Anwenden der LLM-„content_edits“ (stellenweise Edits) auf den
Inhalt eines Skript-Kapitels (Markdown).

Verwendet vom AI-Flow (api/script.py). Anwendung ist partiell: Jedes Edit
wird unabhängig aufgelöst; fehlgeschlagene Edits (Anker nicht
gefunden/mehrdeutig, Überlappung, unbekanntes op) werden übersprungen und als
Warnung gemeldet, die übrigen Edits werden trotzdem angewendet. Bei
Überlappung gewinnt das in der Liste zuerst genannte Edit. Wirft
ContentEditError, wenn gar kein Edit anwendbar ist — dann bleibt der Inhalt
unverändert.

Ops:
- {"op": "replace_section", "heading": "### 3.2 Beispiel", "content": "..."}
  Abschnittsbody ersetzen (Heading bleibt erhalten)
- {"op": "insert_after", "heading": "## 3.1 Grundlagen", "content": "..."}
  neuen Inhalt direkt nach dem Abschnitt einfügen
- {"op": "delete_section", "heading": "### Altes Beispiel"}
  gesamten Abschnitt (Heading + Inhalt) löschen
- {"op": "replace_span", "old": "...", "new": "..."}
  kurzes, eindeutig vorkommendes Snippet ersetzen
"""

import re
from typing import TypedDict


class ContentEditError(ValueError):
    """Edit ungültig oder unklar (deutsche Meldung)."""


# Markdown-Heading-Zeile (ATX, ## … ######); Code-Blöcke werden vorher maskiert.
_HEADING_LINE_RE = re.compile(r"^#{1,6}\s+(\S.*?)\s*$", re.MULTILINE)
_CODE_FENCED_RE = re.compile(r"```[\s\S]*?```")
_CODE_INLINE_RE = re.compile(r"`[^`]+`")
_FIG_LABEL_RE = re.compile(r"!\[[^\]]*\]\([^)\s]+\)\s*\{#fig:([\w-]+)\}")
_EQ_LABEL_RE = re.compile(r"\$\$[\s\S]*?\$\$\s*\{#eq:([\w-]+)\}")


class _Heading(TypedDict):
    """Eine erkannte Markdown-Heading (Positionen beziehen sich auf den Originaltext)."""

    start: int
    end: int
    full: str
    text: str


def mask_code_blocks(text: str) -> str:
    """Maskiert den Inhalt gefenceter Code-Blöcke (Länge und Zeilenstruktur bleiben
    erhalten), damit #-Zeilen in Code nicht als Markdown-Headings erkannt werden."""
    def _mask(m: re.Match[str]) -> str:
        return "".join(ch if ch == "\n" else " " for ch in m.group(0))
    return _CODE_FENCED_RE.sub(_mask, text or "")


def _find_headings(content: str) -> list[_Heading]:
    """Alle Markdown-Headings (außerhalb von Code-Blöcken) mit Position, Level und Text."""
    masked = mask_code_blocks(content)
    return [
        {
            "start": m.start(),
            "end": m.end(),
            "full": m.group(0).strip(),
            "text": m.group(1).strip(),
        }
        for m in _HEADING_LINE_RE.finditer(masked)
    ]


def _section_span(content_len: int, headings: list[_Heading], idx: int) -> tuple[int, int]:
    """Span eines Abschnitts: von der Heading-Zeile bis zur nächsten Heading
    (beliebiger Ebene) bzw. zum Dokumentende. Unterabschnitte gehören NICHT
    zum Abschnitt — so bleibt ein replace_section auf ein ## -Heading ohne
    die darunterliegenden ### -Abschnitte (kein Copy-Risiko für den LLM)."""
    start = headings[idx]["start"]
    end = headings[idx + 1]["start"] if idx + 1 < len(headings) else content_len
    return start, end


def _resolve_single_edit(content: str, headings: list[_Heading], edit: object) -> tuple[int, int, str]:
    """Löst ein einzelnes Edit zu (start, end, Ersetzung) auf; wirft
    ContentEditError, wenn es nicht anwendbar ist."""
    if not isinstance(edit, dict):
        raise ContentEditError("LLM-Edit nicht anwendbar: Edit ist kein Objekt.")
    op = str(edit.get("op") or "").strip()
    if op in ("replace_section", "insert_after", "delete_section"):
        heading = str(edit.get("heading") or "")
        idx = _resolve_heading(heading, headings)
        s, e = _section_span(len(content), headings, idx)
        new_body = str(edit.get("content") or "")
        if op == "replace_section":
            if not new_body.strip():
                raise ContentEditError(f"LLM-Edit nicht anwendbar: „replace_section“ („{heading}“) ohne Inhalt.")
            # Heading bleibt erhalten, nur der Abschnittsbody wird ersetzt.
            return headings[idx]["end"], e, "\n" + new_body.strip("\n") + "\n"
        if op == "delete_section":
            return s, e, ""
        if not new_body.strip():
            raise ContentEditError(f"LLM-Edit nicht anwendbar: „insert_after“ („{heading}“) ohne Inhalt.")
        return e, e, "\n\n" + new_body.strip("\n") + "\n"
    if op == "replace_span":
        old = str(edit.get("old") or "")
        new = str(edit.get("new") or "")
        s, e = _resolve_span(content, old)
        return s, e, new
    raise ContentEditError(f"LLM-Edit nicht anwendbar: unbekanntes „op“ {op!r}.")


def _spans_overlap(a: tuple[int, int, str], b: tuple[int, int, str]) -> bool:
    """True, wenn sich die beiden Spans ausschließen. Einfügepunkte (0 Breite)
    konfligieren nur, wenn sie STRENG innerhalb eines anderen Spans liegen."""
    a1, a2, _ = a
    b1, b2, _ = b
    if a1 == a2 or b1 == b2:
        p = a1 if a1 == a2 else b1
        lo, hi = (b1, b2) if a1 == a2 else (a1, a2)
        return lo < p < hi
    return a1 < b2 and b1 < a2


def _resolve_heading(heading: str, headings: list[_Heading]) -> int:
    """Index der eindeutig passenden Heading. Akzeptiert die Heading-Zeile mit oder
    ohne #-Präfix (auch mit abweichender #-Anzahl)."""
    want = " ".join((heading or "").split())
    want_title = " ".join(want.lstrip("#").split())  # Vergleich ohne #-Präfix
    matches = [
        i
        for i, h in enumerate(headings)
        if want in (" ".join(h["full"].split()), " ".join(h["text"].split()))
        or (want_title and want_title in (" ".join(h["full"].split()), " ".join(h["text"].split())))
    ]
    if not matches:
        raise ContentEditError(f"LLM-Edit nicht anwendbar: Heading „{heading}“ wurde im aktuellen Inhalt nicht gefunden.")
    if len(matches) > 1:
        raise ContentEditError(f"LLM-Edit nicht anwendbar: Heading „{heading}“ ist mehrdeutig ({len(matches)} Treffer).")
    return matches[0]


def _normalize_ws(text: str) -> tuple[str, list[int]]:
    """Komprimiert Whitespace-Runs auf ein einzelnes Leerzeichen.
    Liefert (normalisierte Zeichenkette, Originalindizes der normalisierten Zeichen)."""
    chars: list[str] = []
    pos: list[int] = []
    in_ws = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if not in_ws:
                chars.append(" ")
                pos.append(i)
            in_ws = True
        else:
            chars.append(ch)
            pos.append(i)
            in_ws = False
    return "".join(chars), pos


def _resolve_span(content: str, old: str) -> tuple[int, int]:
    """Span eines kurzen Snippets, das EXAKT EINMAL im Inhalt vorkommt.
    Zuerst exakte Suche; als Fallback whitespace-insensitive Suche
    (Zeilenumbrüche/mehrere Leerzeichen normalisiert)."""
    if not (old or "").strip():
        raise ContentEditError("LLM-Edit nicht anwendbar: „replace_span“ ohne „old“.")
    idx = content.find(old)
    if idx >= 0:
        if content.find(old, idx + 1) >= 0:
            raise ContentEditError(f"LLM-Edit nicht anwendbar: Snippet „{old[:60]}…“ ist mehrdeutig (mehrere Treffer).")
        return idx, idx + len(old)
    n_content, pos = _normalize_ws(content)
    n_old, _ = _normalize_ws(old)
    if n_old:
        n_idx = n_content.find(n_old)
        if n_idx >= 0 and n_content.find(n_old, n_idx + 1) < 0:
            start = pos[n_idx]
            end = pos[n_idx + len(n_old) - 1] + 1
            if content[end - 1].isspace():
                while end < len(content) and content[end].isspace():
                    end += 1
            return start, end
    raise ContentEditError(f"LLM-Edit nicht anwendbar: Snippet „{old[:60]}…“ wurde im aktuellen Inhalt nicht (eindeutig) gefunden.")


def _label_diff_warnings(old: str, new: str) -> list[str]:
    """Warnungen, wenn Edits fig/eq-Labels entfernen oder Duplikate erzeugen."""
    warnings: list[str] = []
    text_old = _CODE_INLINE_RE.sub("", _CODE_FENCED_RE.sub("", old or ""))
    text_new = _CODE_INLINE_RE.sub("", _CODE_FENCED_RE.sub("", new or ""))
    for kind, regex in (("fig", _FIG_LABEL_RE), ("eq", _EQ_LABEL_RE)):
        old_labels = regex.findall(text_old)
        new_labels = regex.findall(text_new)
        for label in dict.fromkeys(set(old_labels) - set(new_labels)):
            warnings.append(f"Label {kind}:{label} wurde entfernt — ggf. in anderen Kapiteln referenziert.")
        for label in dict.fromkeys(new_labels):
            if new_labels.count(label) > 1:
                warnings.append(f"Label {kind}:{label} kommt mehrfach vor — Labels müssen im Skript eindeutig sein.")
    return warnings


def apply_content_edits(content: str, edits: object) -> tuple[str, int, list[str]]:
    """Wendet die LLM-Edit-Liste („content_edits“) auf den bestehenden Inhalt an.

    Partielle Anwendung: Jedes Edit wird unabhängig aufgelöst; fehlgeschlagene
    Edits (Anker nicht gefunden/mehrdeutig, Überlappung, unbekanntes op) werden
    übersprungen und als Warnung gemeldet, die übrigen werden trotzdem
    angewendet. Bei Überlappung gewinnt das in der Liste zuerst genannte Edit.

    Gibt (neuer_content, applied_count, warnings) zurück. Wirft ContentEditError,
    wenn gar kein Edit anwendbar ist — dann bleibt der Inhalt unverändert."""
    if not isinstance(edits, list) or not edits:
        raise ContentEditError("LLM-Antwort ungültig: „content_edits“ ist keine (nicht-leere) Liste von Edit-Objekten.")
    headings = _find_headings(content)
    accepted: list[tuple[int, int, str]] = []  # (start, end, Ersetzung)
    failed: list[str] = []  # je „Edit N: <Meldung>“
    for i, edit in enumerate(edits):
        try:
            span = _resolve_single_edit(content, headings, edit)
        except ContentEditError as e:
            failed.append(f"Edit {i + 1}: {e}")
            continue
        if any(_spans_overlap(span, acc) for acc in accepted):
            failed.append(f"Edit {i + 1}: überschneidet sich mit einem vorherigen Edit — übersprungen.")
            continue
        accepted.append(span)
    if not accepted:
        raise ContentEditError("LLM-Edits nicht anwendbar: " + "; ".join(failed))
    accepted.sort(key=lambda sp: (sp[0], sp[1]))
    parts: list[str] = []
    pos = 0
    for s, e, repl in accepted:
        parts.append(content[pos:s])
        parts.append(repl)
        pos = e
    parts.append(content[pos:])
    new_content = "".join(parts)
    # Mehrere Leerzeilen, die durch Edits entstehen können, zusammenziehen.
    new_content = re.sub(r"\n{3,}", "\n\n", new_content)
    warnings = _label_diff_warnings(content, new_content)
    if len(accepted) < len(edits):
        warnings.insert(
            0,
            f"{len(accepted)} von {len(edits)} LLM-Edits umgesetzt — {len(edits) - len(accepted)} fehlgeschlagen: " + "; ".join(failed),
        )
    return new_content, len(accepted), warnings
