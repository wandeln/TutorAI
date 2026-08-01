"""
Settings-Resolver: Bestimmt die effektive Konfiguration fuer LLM und LDAP.

Prioritaet ( hoechste → tiefste ):
    1. course_settings   (Kurs-Override)
    2. global_settings    (Admin-Dashboard Systemeinstellungen)
    3. .env / Umgebungsvariablen
    4. config.py Default  (hardcoded Fallback)
"""

from typing import Optional
from sqlmodel import Session, select

from config import (
    LLM_API_URL, LLM_API_KEY, LLM_MODEL,
    LDAP_ENABLED, LDAP_SERVER, LDAP_BASE_DN,
    LDAP_BIND_DN, LDAP_BIND_PW, LDAP_USER_SEARCH,
)
from database.models import GlobalSettings, CourseSettings


# ─── LLM Config ──────────────────────────────────────────────────

def get_effective_llm_config(
    session: Session,
    course_id: Optional[int] = None,
) -> dict:
    """
    Gibt die effektive LLM-Konfiguration zurueck.

    Returns:
        {
            "api_url": str,
            "api_key": str,
            "model": str,
            "grading_prompt": str | None,
            "source": "course" | "global" | "env",
        }
    """
    # Start: config.py Defaults (.env + hardcoded)
    api_url = LLM_API_URL
    api_key = LLM_API_KEY
    model = LLM_MODEL
    grading_prompt = None
    source = "env"

    # Ebene 2: global_settings
    gs = session.exec(select(GlobalSettings)).first()
    if gs:
        if gs.llm_api_url:
            api_url = gs.llm_api_url
            source = "global"
        if gs.llm_api_key:
            api_key = gs.llm_api_key
        if gs.llm_model:
            model = gs.llm_model
        if gs.grading_prompt:
            grading_prompt = gs.grading_prompt

    # Ebene 1: course_settings (Override)
    if course_id:
        cs = session.exec(
            select(CourseSettings).where(CourseSettings.course_id == course_id)
        ).first()
        if cs:
            if cs.llm_api_url:
                api_url = cs.llm_api_url
                source = "course"
            if cs.llm_model:
                model = cs.llm_model
            if cs.grading_prompt:
                grading_prompt = cs.grading_prompt

    return {
        "api_url": api_url,
        "api_key": api_key,
        "model": model,
        "grading_prompt": grading_prompt,
        "source": source,
    }


# ─── LDAP Config ─────────────────────────────────────────────────

def get_effective_ldap_config(
    session: Session,
    course_id: Optional[int] = None,
) -> dict:
    """
    Gibt die effektive LDAP-Konfiguration zurueck.

    Returns:
        {
            "use_ldap": bool,
            "ldap_server": str | None,
            "ldap_base_dn": str | None,
            "ldap_bind_dn": str | None,
            "ldap_bind_pw": str | None,
            "ldap_user_search": str | None,
        }
    """
    # Start: config.py Defaults (.env + hardcoded)
    use_ldap = LDAP_ENABLED
    server = LDAP_SERVER if LDAP_ENABLED else None
    base_dn = LDAP_BASE_DN if LDAP_ENABLED else None
    bind_dn = LDAP_BIND_DN if LDAP_ENABLED else None
    bind_pw = LDAP_BIND_PW if LDAP_ENABLED else None
    user_search = LDAP_USER_SEARCH if LDAP_ENABLED else None

    # Ebene 2: global_settings
    gs = session.exec(select(GlobalSettings)).first()
    if gs:
        if gs.use_ldap:
            use_ldap = True
            if gs.ldap_server:
                server = gs.ldap_server
            if gs.ldap_base_dn:
                base_dn = gs.ldap_base_dn
            if gs.ldap_bind_dn:
                bind_dn = gs.ldap_bind_dn
            if gs.ldap_bind_pw is not None:
                bind_pw = gs.ldap_bind_pw
            if gs.ldap_user_search:
                user_search = gs.ldap_user_search

    # Ebene 1: course_settings (Override)
    if course_id:
        cs = session.exec(
            select(CourseSettings).where(CourseSettings.course_id == course_id)
        ).first()
        if cs:
            if cs.use_ldap:
                use_ldap = True
            if cs.ldap_server:
                server = cs.ldap_server
            if cs.ldap_base_dn:
                base_dn = cs.ldap_base_dn
            if cs.ldap_bind_dn:
                bind_dn = cs.ldap_bind_dn
            if cs.ldap_bind_pw is not None:
                bind_pw = cs.ldap_bind_pw
            if cs.ldap_user_search:
                user_search = cs.ldap_user_search

    return {
        "use_ldap": use_ldap,
        "ldap_server": server,
        "ldap_base_dn": base_dn,
        "ldap_bind_dn": bind_dn,
        "ldap_bind_pw": bind_pw,
        "ldap_user_search": user_search,
    }