"""Hilfsfunktionen für das Kurs-Quellenverzeichnis (CourseReference)."""

from sqlmodel import Session, select

from database.models import CourseReference


def build_references_text(session: Session, course_id: int) -> str:
    """Quellenverzeichnis des Kurses (Key, Autoren, Titel, Kernpunkte) als Text.

    Leeres Verzeichnis → "" (aufrufseitig als „kein Quellenverzeichnis“ behandeln).
    """
    rows = session.exec(
        select(CourseReference)
        .where(CourseReference.course_id == course_id)
        .order_by(CourseReference.display_order.asc())  # type: ignore[attr-defined]
        .order_by(CourseReference.id.asc())  # type: ignore[attr-defined]
    ).all()
    if not rows:
        return ""
    lines = []
    for r in rows[:100]:
        authors = ", ".join(str(a) for a in (r.authors or []))
        meta = f"{authors} ({r.year})" if (authors or r.year) else ""
        line = f"- {r.key}: {meta} {r.title}".strip()
        if r.venue:
            line += f" [{r.venue}]"
        desc = (r.description or "").strip()
        if desc:
            line += f" — {desc[:150]}"
        lines.append(line)
    return "\n".join(lines)
