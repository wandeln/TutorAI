"""
Folien-API: Slide-Theme (Design) pro Kurs + LLM-Deck-Generierung.

- GET /api/courses/{cid}/slides-theme: aufgelöstes Theme + Logo-URL (alle Mitglieder)
- PUT /api/courses/{cid}/slides-theme: Theme setzen (PROF/TUTOR)
- POST /api/courses/{cid}/slides/ai-generate: LLM generiert/ändert ein Slide-Deck (PROF/TUTOR)

Das Theme ist ein JSON-Objekt (validiert: services.slides_service.validate_theme);
die Auflösung auf Template-Defaults übernimmt resolve_theme().
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from api.script import _chapter_label, _scan_headings, _scan_labels
from database.base import get_session
from database.models import (
    Course,
    CourseMaterial,
    CourseMedia,
    CourseRole,
    CourseSlidesTheme,
    GlobalUserRole,
    MaterialType,
    ScriptSection,
    Task,
    User,
    UserCourse,
)
from services import media_service
from services.auth_service import get_current_user, require_course_access
from services.llm_service import LLMService
from services.settings_resolver import get_effective_llm_config
from services.slides_service import (
    SlideError,
    THEME_TEMPLATES,
    apply_slide_edits,
    numbered_slide_content,
    parse_slides,
    resolve_theme,
    validate_theme,
)

router = APIRouter(prefix="/api", tags=["Folien"])
llm_service = LLMService()


def _check_member(user: User, session: Session, course_id: int) -> None:
    """Jedes Kurs-Mitglied (oder Admin) darf lesen."""
    if user.role == GlobalUserRole.ADMIN:
        return
    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == course_id)
    ).first()
    if not membership:
        raise HTTPException(403, "Du bist kein Mitglied dieses Kurses.")


def _logo_url(session: Session, course_id: int, theme: dict) -> Optional[str]:
    """URL des hinterlegten Logos (nur bei gültigem Bild-Medium desselben Kurses)."""
    logo_id = theme.get("logo_media_id")
    if not logo_id:
        return None
    media = session.get(CourseMedia, logo_id)
    if media and media.course_id == course_id and media.media_type == "image":
        return media_service.media_url(media)
    return None


@router.get("/courses/{course_id}/slides-theme")
async def get_slides_theme(
    course_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Aufgelöstes Folien-Theme (inkl. Template-Defaults) + Logo-URL."""
    _check_member(user, session, course_id)
    row = session.get(CourseSlidesTheme, course_id)
    theme = resolve_theme(row.theme if row else None)
    return {
        "theme": theme,
        "logo_url": _logo_url(session, course_id, theme),
        "templates": list(THEME_TEMPLATES.keys()),
    }


@router.put("/courses/{course_id}/slides-theme")
async def put_slides_theme(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF, CourseRole.TUTOR)),
):
    """Folien-Theme setzen (PROF/TUTOR). Body = Theme-JSON (s. slides_service)."""
    user, _ = user_and_course
    body = await request.json()

    try:
        theme = validate_theme(body)
    except SlideError as e:
        raise HTTPException(400, str(e))

    # Logo muss ein Bild aus der Medienbibliothek dieses Kurses sein
    if theme["logo_media_id"]:
        media = session.get(CourseMedia, theme["logo_media_id"])
        if not media or media.course_id != course_id or media.media_type != "image":
            raise HTTPException(400, "Das Logo muss ein Bild aus der Medienbibliothek dieses Kurses sein.")

    row = session.get(CourseSlidesTheme, course_id)
    if row:
        row.theme = theme
        row.updated_at = datetime.now(timezone.utc)
    else:
        row = CourseSlidesTheme(course_id=course_id, theme=theme)
    session.add(row)
    session.commit()

    return {
        "message": "Folien-Design gespeichert.",
        "theme": theme,
        "logo_url": _logo_url(session, course_id, theme),
    }


@router.post("/courses/{course_id}/slides/ai-generate")
async def ai_generate_slide_deck(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    _user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF, CourseRole.TUTOR)),
):
    """
    LLM generiert/ändert Titel und Inhalt eines Slide-Decks.

    Request:
        {
            "deck_id": 5,
            "topic": "Folien zu Kapitel 3 (Rekursion) erstellen",
            "minutes": 90,
            "current_title": "...",
            "current_content": "...",
            "generate_fields": {"title": true, "content": true}
        }

    Response:
        {
            // Leeres Feld = LLM hat das Feld nicht geändert (bestehender Wert bleibt).
            "title": "...", "content": "...",
            "edits_applied": 2,  // Anzahl angewendeter „content_edits“ (0 = Volltext/keine Änderung)
            "warnings": [],
            "latency_ms": 123
        }
    """
    body = await request.json()
    gen = body.get("generate_fields", {}) or {}
    generate_fields = [f for f in ("title", "content") if gen.get(f)]
    if not generate_fields:
        raise HTTPException(400, "Keine Felder angefordert.")

    deck_id = body.get("deck_id")
    deck = session.get(CourseMaterial, deck_id) if deck_id else None
    if not deck or deck.course_id != course_id or deck.material_type != MaterialType.SLIDES:
        raise HTTPException(404, "Slide-Deck nicht gefunden.")

    course = session.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Kurs nicht gefunden.")

    # Skript-Kapitel mit INHALT — das LLM baut die Folien daraus. Content pro
    # Kapitel wird gekürzt (Kontextgröße), max. 20 Kapitel. Die vorhandenen
    # Labels sind wichtig: gleiche Objekte → gleiche Labels → gleiche Nummer
    # wie im Skript + klickbarer Link zum Skript.
    chapters = []
    for s in session.exec(
        select(ScriptSection)
        .where(ScriptSection.course_id == course_id)
        .order_by(ScriptSection.display_order.asc())  # type: ignore[attr-defined]
    ).all()[:20]:
        figs, eqs = _scan_labels(s.content)
        sec_labels = [h["label"] for h in _scan_headings(s.content) if h["label"]]
        ch_label = _chapter_label(s.content)
        if ch_label and ch_label in sec_labels:
            sec_labels.remove(ch_label)
            sec_labels.insert(0, ch_label + " (Kapitel-Label)")
        chapters.append(
            {
                "title": s.title,
                "labels":
                    [f"@fig:{l}" for l in figs]
                    + [f"@eq:{l}" for l in eqs]
                    + [f"@sec:{l}" for l in sec_labels],
                "content": (s.content or "")[:4000],
            }
        )

    # Alle Medien des Kurses — Medien dürfen in Skript UND Slides vorkommen,
    # daher nicht nur die skript-unabhängigen übergeben (ggf. einbinden bzw.
    # mit demselben @fig:-Label wie im Skript wiederverwenden).
    course_media = media_service.all_media_for_course(session, course_id)

    # Übungsaufgaben des Kurses (ID + Titel) — ggf. per @task:{id} im Deck
    # einbindbar (max. 1–2, eigene Folie „Übung“).
    course_tasks = [
        {"id": t.id, "title": t.title}
        for t in session.exec(
            select(Task)
            .where(Task.course_id == course_id)
            .order_by(Task.display_order.asc())  # type: ignore[attr-defined]
        ).all()
    ]

    try:
        minutes = int(body.get("minutes"))
    except (TypeError, ValueError):
        minutes = 90
    minutes = max(5, min(minutes, 480))

    current_title = (body.get("current_title") or "").strip()
    current_content = (body.get("current_content") or "").strip()
    # Für das LLM: Folien mit expliziten Nummern („%% Folie N %%“), damit es
    # bei „content_edits“ fehlerfrei auf Folien referenzieren kann (nur wenn
    # „content“ angefordert ist — sonst fehlen die Marker-Erklärung und die
    # STELLENWEISE-Sektion). Die Rohversion bleibt für das serverseitige
    # Anwenden der Edits erhalten.
    llm_content = (
        numbered_slide_content(current_content)
        if current_content and "content" in generate_fields
        else current_content
    )
    llm_cfg = get_effective_llm_config(session, course_id)
    result = await llm_service.generate_slide_deck(
        course_name=course.name,
        topic=(body.get("topic") or "").strip(),
        minutes=minutes,
        generate_fields=generate_fields,
        current_title=current_title,
        current_content=llm_content,
        chapters=chapters,
        course_media=course_media,
        course_tasks=course_tasks,
        config=llm_cfg,
    )
    if not result.get("success"):
        raise HTTPException(502, f"LLM-Fehler: {result.get('error', 'Unbekannter Fehler')}")

    data = result.get("data") or {}
    response = {
        "title": "", "content": "", "edits_applied": 0, "warnings": [],
        "latency_ms": result.get("latency_ms", 0),
    }
    if "title" in generate_fields:
        response["title"] = (data.get("title") or "").strip()
    if "content" in generate_fields:
        new_content = (data.get("content") or "").strip()
        content_edits = data.get("content_edits")
        if new_content:
            # Serverseitig validieren, damit ein Format-Fehler nie in den
            # Editor gelangt (parse_slides ist strikt und wirft SlideError
            # mit deutscher Meldung).
            try:
                parse_slides(new_content)
            except SlideError as e:
                raise HTTPException(400, f"Das LLM lieferte ein ungültiges Slide-Deck: {e}")
            response["content"] = new_content
        elif content_edits:
            # Stellenweise Bearbeitung: Edits serverseitig auf den bestehenden
            # Inhalt anwenden (Anker müssen eindeutig sein, sonst HTTP 400 —
            # der Inhalt bleibt unverändert).
            if not current_content:
                raise HTTPException(400, "LLM lieferte stellenweise Edits („content_edits“), aber es existiert kein Inhalt zum Editieren. Bitte erneut versuchen.")
            try:
                merged = apply_slide_edits(current_content, content_edits)
            except SlideError as e:
                raise HTTPException(400, f"LLM-Edits nicht anwendbar: {e}")
            response["content"] = merged
            response["edits_applied"] = len(content_edits)
    return response
