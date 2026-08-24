"""
Skript-Kapitel: Vorlesungsskript aus mehreren Markdown-Dateien.

Regeln (analog zu den Übungsaufgaben):
- Reihenfolge per display_order (Drag-and-Drop → PATCH .../reorder).
- is_visible = für Studenten freigeschaltet (Default: aus).
- Anlegen/Bearbeiten/Reihenfolge/Sichtbarkeit/Löschen: PROF/TUTOR.
- LLM-Generierung: POST .../ai-generate (Titel/Inhalt).
- Nach jeder Content-Änderung: sync_media_usages() (Medien-Einbindung).
"""

import re
from datetime import datetime, timezone
from typing import Optional, TypedDict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from database.base import get_session
from database.models import (
    Course,
    CourseRole,
    FeedbackSource,
    GlobalUserRole,
    ScriptSection,
    Submission,
    Task,
    User,
    UserCourse,
)
from services.auth_service import get_current_user, require_course_access
from services.llm_service import LLMService
from services import media_service
from services.media_service import sync_media_usages
from services.settings_resolver import get_effective_llm_config

router = APIRouter(prefix="/api", tags=["Skript"])
llm_service = LLMService()

# Regexen identisch zu static/js/markdown-renderer.js:
# Labels werden nur außerhalb von Code-Blöcken gezählt (fenced + inline
# Code werden vorher entfernt). [\w-] ≈ JS [\p{L}0-9_-] (Unicode-Buchstaben,
# Ziffern, Unterstrich, Bindestrich).
_CODE_FENCED_RE = re.compile(r"```[\s\S]*?```")
_CODE_INLINE_RE = re.compile(r"`[^`]+`")
_FIG_LABEL_RE = re.compile(r"!\[[^\]]*\]\([^)\s]+\)\s*\{#fig:([\w-]+)\}")
_EQ_LABEL_RE = re.compile(r"\$\$[\s\S]*?\$\$\s*\{#eq:([\w-]+)\}")


def _scan_labels(content: str) -> tuple[list[str], list[str]]:
    """{#fig:…}/{#eq:…}-Labels aus Markdown in Reihenfolge des Vorkommens (Code-Blöcke ignoriert)."""
    text = _CODE_FENCED_RE.sub("", content or "")
    text = _CODE_INLINE_RE.sub("", text)
    figs = _FIG_LABEL_RE.findall(text)
    eqs = _EQ_LABEL_RE.findall(text)
    return figs, eqs


# ─── LLM-Edits: stellenweise Änderungen am bestehenden Inhalt ────────────
# Das LLM darf für lokale Änderungen statt des Volltexts eine Liste von
# Edit-Objekten liefern („content_edits“). Diese werden hier serverseitig
# auf den bestehenden Inhalt angewendet — der Anker (Heading bzw. kurzes
# Snippet) muss dabei eindeutig sein, sonst wird der Edit abgelehnt und
# der Inhalt bleibt unverändert.

# Markdown-Heading-Zeile (ATX, ## … ######); Code-Blöcke werden vorher maskiert.
_HEADING_LINE_RE = re.compile(r"^#{1,6}\s+(\S.*?)\s*$", re.MULTILINE)


class _Heading(TypedDict):
    """Eine erkannte Markdown-Heading (Positionen beziehen sich auf den Originaltext)."""

    start: int
    end: int
    full: str
    text: str


def _mask_code_blocks(text: str) -> str:
    """Maskiert den Inhalt gefenceter Code-Blöcke (Länge und Zeilenstruktur bleiben
    erhalten), damit #-Zeilen in Code nicht als Markdown-Headings erkannt werden."""
    def _mask(m: re.Match[str]) -> str:
        return "".join(ch if ch == "\n" else " " for ch in m.group(0))
    return _CODE_FENCED_RE.sub(_mask, text or "")


def _find_headings(content: str) -> list[_Heading]:
    """Alle Markdown-Headings (außerhalb von Code-Blöcken) mit Position, Level und Text."""
    masked = _mask_code_blocks(content or "")
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
        raise HTTPException(400, f"LLM-Edit nicht anwendbar: Heading „{heading}“ wurde im aktuellen Inhalt nicht gefunden.")
    if len(matches) > 1:
        raise HTTPException(400, f"LLM-Edit nicht anwendbar: Heading „{heading}“ ist mehrdeutig ({len(matches)} Treffer).")
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
        raise HTTPException(400, "LLM-Edit nicht anwendbar: „replace_span“ ohne „old“.")
    idx = content.find(old)
    if idx >= 0:
        if content.find(old, idx + 1) >= 0:
            raise HTTPException(400, f"LLM-Edit nicht anwendbar: Snippet „{old[:60]}…“ ist mehrdeutig (mehrere Treffer).")
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
    raise HTTPException(400, f"LLM-Edit nicht anwendbar: Snippet „{old[:60]}…“ wurde im aktuellen Inhalt nicht (eindeutig) gefunden.")


def _label_diff_warnings(old: str, new: str) -> list[str]:
    """Warnungen, wenn Edits fig/eq-Labels entfernen oder Duplikate erzeugen."""
    warnings: list[str] = []
    old_figs, old_eqs = _scan_labels(old)
    new_figs, new_eqs = _scan_labels(new)
    for kind, old_labels, new_labels in (("fig", old_figs, new_figs), ("eq", old_eqs, new_eqs)):
        for label in dict.fromkeys(set(old_labels) - set(new_labels)):
            warnings.append(f"Label {kind}:{label} wurde entfernt — ggf. in anderen Kapiteln referenziert.")
        for label in dict.fromkeys(new_labels):
            if new_labels.count(label) > 1:
                warnings.append(f"Label {kind}:{label} kommt mehrfach vor — Labels müssen im Skript eindeutig sein.")
    return warnings


def _apply_content_edits(content: str, edits: object) -> tuple[str, list[str]]:
    """Wendet die LLM-Edit-Liste („content_edits“) auf den bestehenden Inhalt an.

    Gibt (neuer_content, warnings) zurück. Wirft HTTPException(400), wenn ein Edit
    ungültig oder unklar ist (Anker nicht gefunden/mehrdeutig, Überlappung,
    unbekanntes op) — dann bleibt der Inhalt unverändert."""
    if not isinstance(edits, list) or not edits:
        raise HTTPException(400, "LLM-Antwort ungültig: „content_edits“ ist keine (nicht-leere) Liste von Edit-Objekten.")
    headings = _find_headings(content)
    spans: list[tuple[int, int, str]] = []  # (start, end, Ersetzung)
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise HTTPException(400, f"LLM-Edit nicht anwendbar: Edit {i + 1} ist kein Objekt.")
        op = str(edit.get("op") or "").strip()
        if op in ("replace_section", "insert_after", "delete_section"):
            heading = str(edit.get("heading") or "")
            idx = _resolve_heading(heading, headings)
            s, e = _section_span(len(content), headings, idx)
            new_body = str(edit.get("content") or "")
            if op == "replace_section":
                if not new_body.strip():
                    raise HTTPException(400, f"LLM-Edit nicht anwendbar: „replace_section“ („{heading}“) ohne Inhalt.")
                # Heading bleibt erhalten, nur der Abschnittsbody wird ersetzt.
                spans.append((headings[idx]["end"], e, "\n" + new_body.strip("\n") + "\n"))
            elif op == "delete_section":
                spans.append((s, e, ""))
            else:  # insert_after
                if not new_body.strip():
                    raise HTTPException(400, f"LLM-Edit nicht anwendbar: „insert_after“ („{heading}“) ohne Inhalt.")
                spans.append((e, e, "\n\n" + new_body.strip("\n") + "\n"))
        elif op == "replace_span":
            old = str(edit.get("old") or "")
            new = str(edit.get("new") or "")
            s, e = _resolve_span(content, old)
            spans.append((s, e, new))
        else:
            raise HTTPException(400, f"LLM-Edit nicht anwendbar: unbekanntes „op“ {op!r} (Edit {i + 1}).")
    # Überlappungs-Check: Einfügepunkte (0 Breite) konfligieren nur, wenn sie
    # STRENG innerhalb eines anderen Spans liegen.
    for a in range(len(spans)):
        for b in range(a + 1, len(spans)):
            a1, a2, _ = spans[a]
            b1, b2, _ = spans[b]
            if a1 == a2 or b1 == b2:
                p = a1 if a1 == a2 else b1
                lo, hi = (b1, b2) if a1 == a2 else (a1, a2)
                if lo < p < hi:
                    raise HTTPException(400, "LLM-Edit nicht anwendbar: Edits überschneiden sich.")
            elif a1 < b2 and b1 < a2:
                raise HTTPException(400, "LLM-Edit nicht anwendbar: Edits überschneiden sich.")
    spans.sort(key=lambda sp: (sp[0], sp[1]))
    parts: list[str] = []
    pos = 0
    for s, e, repl in spans:
        parts.append(content[pos:s])
        parts.append(repl)
        pos = e
    parts.append(content[pos:])
    new_content = "".join(parts)
    # Mehrere Leerzeilen, die durch Edits entstehen können, zusammenziehen.
    new_content = re.sub(r"\n{3,}", "\n\n", new_content)
    return new_content, _label_diff_warnings(content, new_content)


def _section_to_dict(s: ScriptSection, include_content: bool = False, include_summary: bool = False) -> dict:
    d = {
        "id": s.id,
        "title": s.title,
        "is_visible": s.is_visible,
        "display_order": s.display_order,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }
    if include_content:
        d["content"] = s.content
    if include_summary:  # nur für Tutor/PROF/Admin — Studenten sehen sie nicht
        d["summary"] = s.summary or ""
    return d


def _get_membership(session: Session, user: User, course_id: int) -> Optional[UserCourse]:
    if user.role == GlobalUserRole.ADMIN:
        return None  # Admin braucht keine Membership
    return session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == course_id)
    ).first()


def _check_member(user: User, session: Session, course_id: int) -> None:
    """Jedes Kurs-Mitglied (oder Admin) darf lesen."""
    if user.role == GlobalUserRole.ADMIN:
        return
    if not _get_membership(session, user, course_id):
        raise HTTPException(403, "Du bist kein Mitglied dieses Kurses.")



def _next_order(session: Session, course_id: int) -> int:
    rows = session.exec(
        select(ScriptSection)
        .where(ScriptSection.course_id == course_id)
        .order_by(ScriptSection.display_order.desc())  # type: ignore[attr-defined]
    ).all()
    return rows[0].display_order + 1 if rows else 0


def _check_course_tutor(user: User, course_id: int, session: Session) -> None:
    """PROF/TUTOR-Check für Routen ohne course_id im Path. Admin darf immer."""
    if user.role == GlobalUserRole.ADMIN:
        return
    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == course_id)
    ).first()
    if not membership or membership.role_in_course not in (CourseRole.PROF, CourseRole.TUTOR):
        raise HTTPException(403, "Keine Berechtigung, dieses Kapitel zu bearbeiten.")


# ─── Lesen ────────────────────────────────────────────────────────

@router.get("/courses/{course_id}/script-sections")
async def list_sections(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Kapitel des Skripts (Studenten nur freigeschaltene, optional mit Content)."""
    _check_member(user, session, course_id)
    is_tutor = user.role == GlobalUserRole.ADMIN or (
        (m := _get_membership(session, user, course_id))
        is not None
        and m.role_in_course in (CourseRole.PROF, CourseRole.TUTOR)
    )

    q = select(ScriptSection).where(ScriptSection.course_id == course_id)
    if not is_tutor:
        q = q.where(ScriptSection.is_visible == True)  # noqa: E712
    sections = session.exec(q.order_by(ScriptSection.display_order.asc())).all()  # type: ignore[attr-defined]

    include_content = request.query_params.get("include_content") in ("1", "true")
    return [
        _section_to_dict(s, include_content=include_content, include_summary=is_tutor)
        for s in sections
    ]


@router.get("/script-sections/{section_id}")
async def get_section(
    section_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Einzelnes Kapitel inkl. Markdown-Content."""
    section = session.get(ScriptSection, section_id)
    if not section:
        raise HTTPException(404, "Kapitel nicht gefunden.")
    _check_member(user, session, section.course_id)

    is_tutor = user.role == GlobalUserRole.ADMIN or (
        (m := _get_membership(session, user, section.course_id))
        is not None
        and m.role_in_course in (CourseRole.PROF, CourseRole.TUTOR)
    )
    if not is_tutor and not section.is_visible:
        raise HTTPException(404, "Kapitel nicht gefunden oder noch nicht freigeschaltet.")

    return _section_to_dict(section, include_content=True, include_summary=is_tutor)


@router.get("/courses/{course_id}/script-refmap")
async def script_refmap(
    course_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Live-berechnete Nummerierungs-Map für Querverweise (ohne DB-Speicherung).

    Die Nummerierung ist ein abgeleiteter Wert (wie LaTeX zur Kompilierzeit):
    Jedes beschriftete Objekt erhält eine durchlaufende Nummer über das
    gesamte Skript. Studenten sehen nur freigeschaltete Kapitel → saubere
    „veröffentlichte“ Nummerierung.

    Payload:
        mode:     "reading" (Student → Skript-Seite) bzw. "edit" (PROF/TUTOR/Admin → Kapitel-Editseite)
        courseId: Kurs-ID
        chapters: {id: {title, maxFig, maxEq}}  # Preview-Fallback (neue, ungespeicherte Labels)
        labels:   {label: {kind, sectionId, num}}  # globale Nummer, bei Duplikaten: erstes Vorkommen gewinnt
        tasks:    {id: {id, title, taskType, maxPoints, myPoints, attemptsUsed, maxAttempts, deadline}}
                  # für @task:{id}-Referenzen. Student: nur freigeschaltete Aufgaben +
                  # eigene Punkte (analog Aufgabenübersicht); PROF/TUTOR/Admin: alle.
    """
    _check_member(user, session, course_id)
    is_tutor = user.role == GlobalUserRole.ADMIN or (
        (m := _get_membership(session, user, course_id))
        is not None
        and m.role_in_course in (CourseRole.PROF, CourseRole.TUTOR)
    )

    q = select(ScriptSection).where(ScriptSection.course_id == course_id)
    if not is_tutor:
        q = q.where(ScriptSection.is_visible == True)  # noqa: E712
    sections = session.exec(q.order_by(ScriptSection.display_order.asc())).all()  # type: ignore[attr-defined]

    chapters: dict[str, dict[str, str | int]] = {}
    labels: dict[str, dict[str, str | int]] = {}
    fig_running = 0
    eq_running = 0
    for s in sections:
        figs, eqs = _scan_labels(s.content)
        max_fig = 0
        max_eq = 0
        for label in figs:
            fig_running += 1
            if label not in labels:
                labels[label] = {"kind": "fig", "sectionId": s.id, "num": fig_running}
            max_fig = max(max_fig, fig_running)
        for label in eqs:
            eq_running += 1
            if label not in labels:
                labels[label] = {"kind": "eq", "sectionId": s.id, "num": eq_running}
            max_eq = max(max_eq, eq_running)
        chapters[str(s.id)] = {"title": s.title, "maxFig": max_fig, "maxEq": max_eq}

    # Aufgaben für @task:{id}-Referenzen (Datenquelle der Aufgaben-Box).
    tasks_map: dict[str, dict] = {}
    tq = select(Task).where(Task.course_id == course_id)
    if not is_tutor:
        tq = tq.where(Task.is_visible == True)  # noqa: E712
    for task in session.exec(tq.order_by(Task.display_order.asc())).all():  # type: ignore[attr-defined]
        my_points = 0.0
        attempts_used = 0
        if not is_tutor and task.id is not None:
            # Eigene Punkte: wie in der Aufgabenübersicht (my-points-Endpoint) —
            # letzte Abgabe, menschliche Bewertung schlägt LLM-Bewertung.
            subs = session.exec(
                select(Submission)
                .where(Submission.task_id == task.id)
                .where(Submission.student_id == user.id)
                .order_by(Submission.submitted_at.desc())  # type: ignore[attr-defined]
            ).all()
            attempts_used = len(subs)
            if subs:
                human_points = 0.0
                llm_points = 0.0
                override_exists = False
                for fb in subs[0].feedback_list:
                    if fb.source == FeedbackSource.HUMAN:
                        human_points = max(human_points, fb.points_earned)
                        override_exists = True
                    else:
                        llm_points = max(llm_points, fb.points_earned)
                my_points = human_points if override_exists else llm_points
        tasks_map[str(task.id)] = {
            "id": task.id,
            "title": task.title,
            "taskType": task.task_type.value,
            "maxPoints": task.max_points,
            "myPoints": round(my_points, 1),
            "attemptsUsed": attempts_used,
            "maxAttempts": task.max_attempts,
            "deadline": task.deadline,
        }

    return {
        "mode": "edit" if is_tutor else "reading",
        "courseId": course_id,
        "chapters": chapters,
        "labels": labels,
        "tasks": tasks_map,
    }


# ─── Schreiben (PROF/TUTOR) ───────────────────────────────────────

@router.post("/courses/{course_id}/script-sections")
async def create_section(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF, CourseRole.TUTOR)),
):
    """Neues Skript-Kapitel anlegen (am Ende der Reihenfolge)."""
    user, _ = user_and_course
    body = await request.json()

    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "Titel darf nicht leer sein.")

    section = ScriptSection(
        course_id=course_id,
        title=title,
        content=body.get("content") or "",
        is_visible=bool(body.get("is_visible", False)),
        display_order=_next_order(session, course_id),
        summary=body.get("summary") or "",
        created_by=user.id,  # type: ignore[arg-type]
    )
    session.add(section)
    session.commit()
    session.refresh(section)
    sync_media_usages(session, course_id)

    return {
        "message": f"Kapitel '{section.title}' erstellt.",
        "section": _section_to_dict(section),
    }


@router.put("/script-sections/{section_id}")
async def update_section(
    section_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Kapitel aktualisieren (Titel, Content, Sichtbarkeit)."""
    section = session.get(ScriptSection, section_id)
    if not section:
        raise HTTPException(404, "Kapitel nicht gefunden.")
    _check_course_tutor(user, section.course_id, session)
    body = await request.json()

    if "title" in body:
        title = (body.get("title") or "").strip()
        if not title:
            raise HTTPException(400, "Titel darf nicht leer sein.")
        section.title = title
    if "content" in body:
        section.content = body.get("content") or ""
    if "is_visible" in body:
        section.is_visible = bool(body.get("is_visible"))
    if "summary" in body:
        section.summary = body.get("summary") or ""

    section.updated_at = datetime.now(timezone.utc)
    session.add(section)
    session.commit()
    session.refresh(section)
    sync_media_usages(session, section.course_id)

    return {"message": "Kapitel aktualisiert.", "section": _section_to_dict(section)}


@router.patch("/script-sections/{section_id}/visibility")
async def toggle_section_visibility(
    section_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Kapitel für Studenten freischalten/verstecken."""
    section = session.get(ScriptSection, section_id)
    if not section:
        raise HTTPException(404, "Kapitel nicht gefunden.")
    _check_course_tutor(user, section.course_id, session)
    body = await request.json()

    section.is_visible = bool(body.get("is_visible", not section.is_visible))
    section.updated_at = datetime.now(timezone.utc)
    session.add(section)
    session.commit()
    session.refresh(section)

    return {"message": "Sichtbarkeit aktualisiert.", "is_visible": section.is_visible}


@router.delete("/script-sections/{section_id}")
async def delete_section(
    section_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Kapitel löschen (nur PROF/TUTOR)."""
    section = session.get(ScriptSection, section_id)
    if not section:
        raise HTTPException(404, "Kapitel nicht gefunden.")
    _check_course_tutor(user, section.course_id, session)

    section_course_id = section.course_id
    section_title = section.title
    session.delete(section)
    session.commit()
    sync_media_usages(session, section_course_id)

    return {"message": f"Kapitel '{section_title}' gelöscht."}


@router.patch("/courses/{course_id}/script-sections/reorder")
async def reorder_sections(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    _user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF, CourseRole.TUTOR)),
):
    """Reihenfolge der Kapitel ändern (nur PROF/TUTOR)."""
    body = await request.json()
    section_ids = body.get("section_ids")
    if not isinstance(section_ids, list) or not section_ids:
        raise HTTPException(400, "section_ids muss eine nicht-leere Liste sein.")

    sections = session.exec(
        select(ScriptSection).where(ScriptSection.course_id == course_id)
    ).all()
    by_id = {s.id: s for s in sections}

    for idx, sid in enumerate(section_ids):
        s = by_id.get(sid)
        if not s:
            raise HTTPException(404, f"Kapitel {sid} nicht gefunden.")
        s.display_order = idx
        session.add(s)
    session.commit()

    return {"message": "Reihenfolge aktualisiert."}


@router.post("/courses/{course_id}/script-sections/ai-generate")
async def ai_generate_section(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    _user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF, CourseRole.TUTOR)),
):
    """
    LLM generiert/ändert die angeforderten Felder eines Skript-Kapitels.

    Request:
        {
            "topic": "Rekursion / Füge ein Beispiel mit Baumdiagramm hinzu",
            "section_id": 3,            // optional: aktuell bearbeitetes Kapitel
            "current_title": "...",
            "current_content": "...",
            "generate_fields": {"title": true, "content": true, "summary": true}
        }

    Response:
        {
            // Leeres Feld = LLM hat das Feld nicht geändert (bestehender Wert bleibt).
            "title": "...", "content": "...", "summary": "...",
            "edits_applied": 0,   // >0: content wurde aus „content_edits“ gemerged
            "warnings": [],       // z.B. entfernte/doppelte fig/eq-Labels
            "latency_ms": 123
        }
    """
    body = await request.json()
    gen = body.get("generate_fields", {}) or {}
    generate_fields = [f for f in ("title", "content", "summary") if gen.get(f)]
    if not generate_fields:
        raise HTTPException(400, "Keine Felder angefordert.")

    course = session.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Kurs nicht gefunden.")

    # Andere Kapitel (Titel + interne Zusammenfassung + vorhandene Labels) —
    # für Notations- und Label-Konsistenz. Das aktuell bearbeitete
    # Kapitel wird ausgeschlossen.
    section_id = body.get("section_id")
    other_chapters = []
    for s in session.exec(
        select(ScriptSection)
        .where(ScriptSection.course_id == course_id)
        .order_by(ScriptSection.display_order.asc())  # type: ignore[attr-defined]
    ).all():
        if s.id == section_id:
            continue
        figs, eqs = _scan_labels(s.content)
        other_chapters.append(
            {
                "title": s.title,
                "summary": (s.summary or "").strip(),
                "labels": [f"@fig:{l}" for l in figs] + [f"@eq:{l}" for l in eqs],
            }
        )
    other_chapters = other_chapters[:20]

    # Noch nicht im Skript verwendete (sichtbare) Medien — ggf. einbindbar.
    unused_media = media_service.unused_media_for_script(session, course_id)[:15]

    # Übungsaufgaben des Kurses (ID + Titel) — das LLM kann passende Aufgaben
    # per @task:{id} im Kapitel einbinden (Aufgaben-Box für Studenten).
    course_tasks = [
        {"id": t.id, "title": t.title}
        for t in session.exec(
            select(Task)
            .where(Task.course_id == course_id)
            .order_by(Task.display_order.asc())  # type: ignore[attr-defined]
        ).all()
    ]

    current_title = (body.get("current_title") or "").strip()
    current_content = (body.get("current_content") or "").strip()
    llm_cfg = get_effective_llm_config(session, course_id)
    result = await llm_service.generate_script_section(
        course_name=course.name,
        topic=(body.get("topic") or "").strip(),
        generate_fields=generate_fields,
        current_title=current_title,
        current_content=current_content,
        other_chapters=other_chapters,
        unused_media=unused_media,
        course_tasks=course_tasks,
        config=llm_cfg,
    )
    if not result.get("success"):
        raise HTTPException(502, f"LLM-Fehler: {result.get('error', 'Unbekannter Fehler')}")

    data = result.get("data") or {}
    response = {"title": "", "content": "", "summary": "", "edits_applied": 0, "warnings": []}
    if "title" in generate_fields:
        response["title"] = (data.get("title") or "").strip()
    if "content" in generate_fields:
        new_content = (data.get("content") or "").strip()
        content_edits = data.get("content_edits")
        if new_content:
            response["content"] = new_content
        elif content_edits:
            # Stellenweise Bearbeitung: Edits serverseitig auf den bestehenden
            # Inhalt anwenden (Anker müssen eindeutig sein, sonst HTTP 400).
            if not current_content:
                raise HTTPException(400, "LLM lieferte stellenweise Edits („content_edits“), aber es existiert kein Inhalt zum Editieren. Bitte erneut versuchen.")
            merged, warnings = _apply_content_edits(current_content, content_edits)
            response["content"] = merged
            response["edits_applied"] = len(content_edits)
            response["warnings"] = warnings
        # sonst: LLM hat weder „content“ noch „content_edits“ geliefert → ""
    if "summary" in generate_fields:
        response["summary"] = (data.get("summary") or "").strip()
    response["latency_ms"] = result.get("latency_ms", 0)
    return response
