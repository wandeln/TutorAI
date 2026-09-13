"""
Datenbank-Modelle (SQLModel).

Alle Tabellen von TutorAI. Jede Tabelle hat:
- Ein Table-Model (für die DB)
- Ein CreateSchema (für POST/PUT)
- Ein ReadSchema (für Responses)

Das hält die API sauber und typisiert.
"""

from datetime import datetime
from typing import Optional, List
from enum import Enum
from sqlalchemy import Column, JSON, UniqueConstraint
from sqlmodel import SQLModel, Field, Relationship


# ═══════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════

class GlobalUserRole(str, Enum):
    ADMIN = "ADMIN"
    USER = "USER"


# ─── Import-Stage-Status (Kurs-Material-Import) ──────────────────
# pending      noch nicht gestartet
# running      Job läuft
# paused       vom User pausiert
# done         erfolgreich abgeschlossen
# skipped      bewusst übersprungen (User)
# error        Fehler (Details pro Einheit in chapter_plan/file_map/report)
# cancelled    vom User abgebrochen
# interrupted  App-Neustart während des Jobs (Runner startet stateless neu)
IMPORT_STAGE_STATUSES = (
    "pending", "running", "paused", "done", "skipped", "error", "cancelled", "interrupted",
)
IMPORT_STAGE_ACTIVE = ("running", "paused")
IMPORT_STAGE_ACTIVE_OR_INTERRUPTED = ("running", "paused", "interrupted")


class CourseRole(str, Enum):
    PROF = "PROF"
    TUTOR = "TUTOR"
    STUDENT = "STUDENT"


class TaskType(str, Enum):
    TEXT = "text"
    CODE = "code"



class SubmissionStatus(str, Enum):
    PENDING = "pending"
    GRADED = "graded"
    OVERRIDDEN = "overridden"


class FeedbackSource(str, Enum):
    LLM = "llm"
    HUMAN = "human"


# ═══════════════════════════════════════════════════════════════════
# USER
# ═══════════════════════════════════════════════════════════════════

class UserBase(SQLModel):
    username: str = Field(unique=True, index=True, max_length=100)
    email: str = Field(max_length=200)
    name: str = Field(max_length=200)
    role: GlobalUserRole = GlobalUserRole.USER    # Global: ADMIN oder USER
    password_hash: Optional[str] = Field(default=None)   # NULL bei LDAP-Users
    ldap_dn: Optional[str] = Field(default=None)         # Distinguished Name
    avatar: Optional[str] = Field(default=None, max_length=255)  # relativer Pfad: avatars/<uuid>.<ext>


class User(UserBase, table=True):
    __tablename__ = "users"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    
    # Relationships (mit back_populates, um SQLAlchemy-Warnings zu vermeiden)
    user_courses: List["UserCourse"] = Relationship(back_populates="user")
    created_tasks: List["Task"] = Relationship(back_populates="creator")
    submissions: List["Submission"] = Relationship(back_populates="student")
    feedback_given: List["Feedback"] = Relationship(back_populates="giver")


class UserCreate(UserBase):
    plain_password: str = Field(min_length=6, max_length=128)


class UserRead(UserBase):
    id: int
    username: str


class UserInDB(UserBase):
    id: int
    password_hash: Optional[str]


# ═══════════════════════════════════════════════════════════════════
# COURSE
# ═══════════════════════════════════════════════════════════════════

class CourseBase(SQLModel):
    name: str = Field(max_length=200)
    description: str = Field(default="", max_length=2000)
    semester: str = Field(max_length=50)  # z.B. "WS 2025/26"
    toc_visible: bool = Field(default=True)  # Inhaltsverzeichnis für Studenten sichtbar


class Course(CourseBase, table=True):
    __tablename__ = "courses"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    created_by: int = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.now)
    
    # Relationships
    course_members: List["UserCourse"] = Relationship(back_populates="course")
    tasks: List["Task"] = Relationship(back_populates="course")
    materials: List["CourseMaterial"] = Relationship(back_populates="course")
    script_sections: List["ScriptSection"] = Relationship(back_populates="course")
    media: List["CourseMedia"] = Relationship(back_populates="course")
    references: List["CourseReference"] = Relationship(back_populates="course")
    settings: Optional["CourseSettings"] = Relationship(back_populates="course")
    channels: List["ForumChannel"] = Relationship(back_populates="course")


class CourseCreate(CourseBase):
    pass


class CourseRead(CourseBase):
    id: int
    created_by: int
    created_at: datetime


# ═══════════════════════════════════════════════════════════════════
# USER-COURSE (M:N mit Rolle im Kurs)
# ═══════════════════════════════════════════════════════════════════

class UserCourseBase(SQLModel):
    user_id: int = Field(foreign_key="users.id")
    course_id: int = Field(foreign_key="courses.id")
    role_in_course: CourseRole


class UserCourse(UserCourseBase, table=True):
    __tablename__ = "user_courses"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    
    user: User = Relationship(back_populates="user_courses")
    course: Course = Relationship(back_populates="course_members")


class UserCourseCreate(SQLModel):
    user_ids: List[int] = Field(default=[])   # IDs der User hinzuzufügen
    role_in_course: CourseRole


class UserCourseRead(SQLModel):
    id: int
    user_id: int
    course_id: int
    role_in_course: CourseRole
    user: Optional[UserRead] = None


# ═══════════════════════════════════════════════════════════════════
# TASK (Aufgabe)
# ═══════════════════════════════════════════════════════════════════

class TaskBase(SQLModel):
    course_id: int = Field(foreign_key="courses.id")
    title: str = Field(max_length=300)     # z.B. "Blatt3-01: Rekursion"
    task_type: TaskType
    description: str                        # Aufgabenstellung
    model_solution: Optional[str] = Field(default=None)  # Musterlösung (optional, versteckt für Studenten)
    max_points: int = Field(ge=0)
    max_attempts: Optional[int] = Field(default=None)  # NULL = unlimitiert
    deadline: Optional[str] = Field(default=None)      # ISO-Format: "2025-02-15T23:59"
    code_template: Optional[str] = Field(default=None) # Für Code-Aufgaben
    test_code: Optional[str] = Field(default=None)     # Unit-Tests (einziger String mit PublicTest + PrivateTest)
    is_visible: bool = Field(default=True)             # Für Studenten sichtbar
    display_order: int = Field(default=0)              # Anzeigereihenfolge im Kurs
    hints_enabled: bool = Field(default=True)          # Socratic-Hints fuer Studenten


class Task(TaskBase, table=True):
    __tablename__ = "tasks"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    created_by: int = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    
    # Relationships
    course: Course = Relationship(back_populates="tasks")
    creator: User = Relationship(back_populates="created_tasks")
    submissions: List["Submission"] = Relationship(back_populates="task")
    hint_exchanges: List["HintExchange"] = Relationship(back_populates="task")


class TaskCreate(SQLModel):
    course_id: int
    title: str
    task_type: TaskType
    description: str
    model_solution: Optional[str] = Field(default=None)
    max_points: int = Field(ge=0)
    max_attempts: Optional[int] = Field(default=None)
    deadline: Optional[str] = Field(default=None)
    code_template: Optional[str] = Field(default=None)
    test_code: Optional[str] = Field(default=None)
    is_visible: bool = Field(default=True)
    display_order: int = Field(default=0)
    hints_enabled: bool = Field(default=True)


class TaskRead(TaskBase):
    id: int
    created_by: int
    created_at: datetime
    updated_at: datetime


class TaskUpdate(SQLModel):
    """Partial update — alle Felder optional"""
    title: Optional[str] = None
    task_type: Optional[TaskType] = None
    description: Optional[str] = None
    model_solution: Optional[str] = None
    max_points: Optional[int] = None
    max_attempts: Optional[int] = None
    deadline: Optional[str] = None
    code_template: Optional[str] = None
    test_code: Optional[str] = None
    is_visible: Optional[bool] = None
    display_order: Optional[int] = None
    hints_enabled: Optional[bool] = None


# ═══════════════════════════════════════════════════════════════════
# COURSE MATERIAL (Vorlesungsskript & Slides)
# ═══════════════════════════════════════════════════════════════════

class MaterialType(str, Enum):
    SCRIPT = "script"
    SLIDES = "slides"


class CourseMaterialBase(SQLModel):
    course_id: int = Field(foreign_key="courses.id", index=True)
    title: str = Field(max_length=300)
    material_type: MaterialType
    content: str = Field(default="")              # Markdown (Slides: Folien mit `---` getrennt)
    is_visible: bool = Field(default=True)        # Für Studenten sichtbar
    display_order: int = Field(default=0)         # Reihenfolge (wichtig für mehrere Slide-Decks)
    summary: str = Field(default="")              # Interne LLM-Zusammenfassung (NICHT für Studenten; Konsistenz zwischen Decks)


class CourseMaterial(CourseMaterialBase, table=True):
    __tablename__ = "course_materials"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_by: int = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    # Relationships
    course: Course = Relationship(back_populates="materials")


class CourseMaterialCreate(SQLModel):
    title: str
    material_type: MaterialType
    content: str = ""
    is_visible: bool = True


class CourseMaterialRead(CourseMaterialBase):
    id: int
    created_by: int
    created_at: datetime
    updated_at: datetime


class CourseSlidesTheme(SQLModel, table=True):
    """Design (Theme) für die Slide-Decks eines Kurses (eine Zeile pro Kurs)."""
    __tablename__ = "course_slides_theme"

    course_id: int = Field(foreign_key="courses.id", primary_key=True)
    theme: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    updated_at: datetime = Field(default_factory=datetime.now)


# ═══════════════════════════════════════════════════════════════════
# SCRIPT SECTIONS (Vorlesungsskript als mehrere Markdown-Kapitel)
# ═══════════════════════════════════════════════════════════════════

class ScriptSectionBase(SQLModel):
    course_id: int = Field(foreign_key="courses.id", index=True)
    title: str = Field(max_length=300)         # z.B. "Rekursion" (Nummer wird automatisch angezeigt)
    content: str = Field(default="")           # Markdown (mit LaTeX/Mermaid); Kapitel-Label = {#sec:label} als eigene Zeile am Anfang
    is_visible: bool = Field(default=False)    # für Studenten freigeschaltet
    display_order: int = Field(default=0)      # Reihenfolge im Skript
    summary: str = Field(default="")           # Interne LLM-Zusammenfassung (NICHT für Studenten; Konsistenz zwischen Kapiteln)


class ScriptSection(ScriptSectionBase, table=True):
    __tablename__ = "course_script_sections"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_by: int = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    # Relationships
    course: Course = Relationship(back_populates="script_sections")


# ═══════════════════════════════════════════════════════════════════
# COURSE MEDIA (Bilder/Applets je Kurs)
# ═══════════════════════════════════════════════════════════════════

class CourseMediaBase(SQLModel):
    course_id: int = Field(foreign_key="courses.id", index=True)
    title: str = Field(max_length=300)
    file_path: str = Field(max_length=500, unique=True)  # relativ zu MEDIA_DIR: course_1/<uuid>.png
    media_type: str = Field(default="image", max_length=50)  # image (später: applet, figure)
    mime_type: str = Field(default="image/png", max_length=100)
    file_size: int = Field(default=0)
    content_hash: Optional[str] = Field(default=None, max_length=64, index=True)  # SHA256 der Quelldatei (Medien-Import-Dedup)
    llm_description: Optional[str] = Field(default=None, max_length=2000)  # Was zeigt das Medium? (für LLM-Pipeline)


class CourseMedia(CourseMediaBase, table=True):
    __tablename__ = "course_media"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_by: int = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.now)

    # Relationships
    course: Course = Relationship(back_populates="media")
    usages: List["MediaUsage"] = Relationship(back_populates="media")


class CourseMediaRead(CourseMediaBase):
    id: int
    created_by: int
    created_at: datetime


# ═══════════════════════════════════════════════════════════════════
# COURSE REFERENCE (Quellen-Bibliothek, BibTeX-artig, PROF/TUTOR/Admin)
# ═══════════════════════════════════════════════════════════════════

class CourseReferenceBase(SQLModel):
    course_id: int = Field(foreign_key="courses.id", index=True)
    key: str = Field(max_length=100)            # BibTeX-Schlüssel (einzig pro Kurs; @cite:{key})
    entry_type: str = Field(default="misc", max_length=50)   # article/book/inproceedings/...
    authors: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    title: str = Field(default="", max_length=500)
    year: str = Field(default="", max_length=10)
    venue: str = Field(default="", max_length=300)   # Zeitschrift / Konferenz / Verlag
    detail: str = Field(default="", max_length=200)   # Band(Heft), Seiten, Edition ...
    address: str = Field(default="", max_length=200)
    doi: str = Field(default="", max_length=200)
    url: str = Field(default="", max_length=500)      # Link zur Quelle (Nachschlagen)
    note: str = Field(default="", max_length=500)
    description: str = Field(default="")   # Inhalt/Kernpunkte — wann soll die Quelle zitiert werden?
    display_order: int = Field(default=0)  # Reihenfolge = globale Zitations-Nummer (stabil pro Kurs)


class CourseReference(CourseReferenceBase, table=True):
    __tablename__ = "course_references"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_by: int = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.now)

    # Relationships
    course: Course = Relationship(back_populates="references")


class CourseReferenceRead(CourseReferenceBase):
    id: int
    created_by: int
    created_at: datetime


# ═══════════════════════════════════════════════════════════════════
# MEDIA USAGE (wo ein Medium eingebunden ist — abgeleitet aus Markdown)
# ═══════════════════════════════════════════════════════════════════

class MediaUsage(SQLModel, table=True):
    """Ableitung aus dem Markdown-Inhalt (Single Source of Truth = Task/Material).

    Wird von services.media_service.sync_media_usages() bei jeder
    Änderung von Aufgaben/Materialien/Medien neu aufgebaut.
    """
    __tablename__ = "media_usages"

    id: Optional[int] = Field(default=None, primary_key=True)
    media_id: int = Field(foreign_key="course_media.id", index=True)
    task_id: Optional[int] = Field(default=None, foreign_key="tasks.id", index=True)
    material_id: Optional[int] = Field(default=None, foreign_key="course_materials.id", index=True)
    location: str = Field(default="", max_length=500)  # z.B. "Aufgabe: Blatt3-01" / "Skript: Kapitel 2"

    # Relationships
    media: CourseMedia = Relationship(back_populates="usages")


# ═══════════════════════════════════════════════════════════════════
# HINT EXCHANGE (Socratic Hint Dialog)
# ═══════════════════════════════════════════════════════════════════

class HintExchangeBase(SQLModel):
    task_id: int = Field(foreign_key="tasks.id")
    student_id: int = Field(foreign_key="users.id")
    question: str
    llm_response: str = Field(default="")
    current_solution: str = Field(default="")      # Current content in the editor at time of request


class HintExchange(HintExchangeBase, table=True):
    __tablename__ = "hint_exchanges"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.now)
    response_at: Optional[datetime] = None          # Timestamp when LLM responded

    # Relationships
    task: Task = Relationship(back_populates="hint_exchanges")


class HintExchangeCreate(SQLModel):
    task_id: int
    question: str
    current_solution: str = Field(default="")


class HintExchangeRead(SQLModel):
    id: int
    task_id: int
    student_id: int
    question: str
    llm_response: str
    current_solution: str
    created_at: datetime
    response_at: Optional[datetime] = None


# ═══════════════════════════════════════════════════════════════════
# SUBMISSION (Einreichung)
# ═══════════════════════════════════════════════════════════════════

class SubmissionBase(SQLModel):
    task_id: int = Field(foreign_key="tasks.id")
    student_id: int = Field(foreign_key="users.id")
    solution: str = Field(default="")          # Für Text-Aufgaben
    code_solution: str = Field(default="")     # Für Code-Aufgaben
    attempt_number: int = Field(default=1)
    solve_time_seconds: float = Field(default=0.0)  # Zeit in Sekunden bis zum Einreichen


class Submission(SubmissionBase, table=True):
    __tablename__ = "submissions"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    submitted_at: datetime = Field(default_factory=datetime.now)
    status: SubmissionStatus = SubmissionStatus.PENDING
    
    # Relationships
    task: Task = Relationship(back_populates="submissions")
    student: User = Relationship(back_populates="submissions")
    feedback_list: List["Feedback"] = Relationship(back_populates="submission")


class SubmissionCreate(SQLModel):
    task_id: int
    solution: str = Field(default="")
    code_solution: str = Field(default="")
    solve_time_seconds: float = Field(default=0.0)


class SubmissionRead(SubmissionBase):
    id: int
    submitted_at: datetime
    status: SubmissionStatus
    solve_time_seconds: float = Field(default=0.0)
    feedback_list: List["FeedbackRead"] = []


# ═══════════════════════════════════════════════════════════════════
# FEEDBACK (LLM + manuell)
# ═══════════════════════════════════════════════════════════════════

class FeedbackBase(SQLModel):
    submission_id: int = Field(foreign_key="submissions.id")
    source: FeedbackSource
    points_earned: float = Field(ge=0)
    comment: str


class Feedback(FeedbackBase, table=True):
    __tablename__ = "feedback"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    giver_id: Optional[int] = Field(default=None, foreign_key="users.id")  # NULL bei LLM
    created_at: datetime = Field(default_factory=datetime.now)
    
    # Relationships
    submission: Submission = Relationship(back_populates="feedback_list")
    giver: Optional[User] = Relationship(back_populates="feedback_given")


class FeedbackCreate(SQLModel):
    source: FeedbackSource
    points_earned: float = Field(ge=0)
    comment: str
    giver_id: Optional[int] = None


class FeedbackRead(FeedbackBase):
    id: int
    giver_id: Optional[int]
    created_at: datetime
    giver: Optional[UserRead] = None


# ================================================================
# GLOBAL SETTINGS (LLM-Config, LDAP, Prompts — instanzweit)
# ================================================================
#
# Diese Tabelle hat genau eine Zeile und bildet die globale Basis-
# Konfiguration.  course_settings kann einzelne Felder pro Kurs
# überschreiben.
#
# Priorität (höchste → tiefste):
#   1. course_settings  (Kurs-Override)
#   2. global_settings   (Admin-Dashboard)
#   3. .env              (Umgebungsvariablen)
#   4. config.py Default (hardcoded Fallback)
#

class GlobalSettings(SQLModel, table=True):
    __tablename__ = "global_settings"

    id: int = Field(default=1, primary_key=True)   # Always id=1, exactly one row
    llm_api_url: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    grading_prompt: Optional[str] = None
    # Public Endpoint für nicht-sensitive Aufgaben (z. B. Task-/Musterlösung-Generierung).
    # Leer = Private Endpoint (llm_api_url) wird verwendet.
    llm_api_url_public: Optional[str] = None
    llm_api_key_public: Optional[str] = None
    llm_model_public: Optional[str] = None
    use_ldap: bool = Field(default=False)
    ldap_server: Optional[str] = None
    ldap_base_dn: Optional[str] = None
    ldap_bind_dn: Optional[str] = None
    ldap_bind_pw: Optional[str] = None
    ldap_user_search: Optional[str] = None


class GlobalSettingsUpdate(SQLModel):
    llm_api_url: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    grading_prompt: Optional[str] = None
    llm_api_url_public: Optional[str] = None
    llm_api_key_public: Optional[str] = None
    llm_model_public: Optional[str] = None
    use_ldap: Optional[bool] = None
    ldap_server: Optional[str] = None
    ldap_base_dn: Optional[str] = None
    ldap_bind_dn: Optional[str] = None
    ldap_bind_pw: Optional[str] = None
    ldap_user_search: Optional[str] = None


class GlobalSettingsRead(SQLModel, from_attributes=True):
    id: int
    llm_api_url: Optional[str]
    llm_api_key: Optional[str]
    llm_model: Optional[str]
    grading_prompt: Optional[str]
    llm_api_url_public: Optional[str]
    llm_api_key_public: Optional[str]
    llm_model_public: Optional[str]
    use_ldap: bool
    ldap_server: Optional[str]
    ldap_base_dn: Optional[str]
    ldap_bind_dn: Optional[str]
    ldap_user_search: Optional[str]


# ═══════════════════════════════════════════════════════════════════
# COURSE SETTINGS (pro Kurs: LLM-Config, LDAP, Prompts)
# ═══════════════════════════════════════════════════════════════════

class CourseSettingsBase(SQLModel):
    course_id: int = Field(foreign_key="courses.id", unique=True)
    llm_api_url: Optional[str] = None          # Override global LLM-URL
    llm_model: Optional[str] = None            # Override globales LLM-Modell
    grading_prompt: Optional[str] = None       # Custom grading prompt
    use_ldap: bool = Field(default=False)
    ldap_server: Optional[str] = None
    ldap_base_dn: Optional[str] = None
    ldap_bind_dn: Optional[str] = None
    ldap_bind_pw: Optional[str] = None
    ldap_user_search: Optional[str] = None  # e.g. (uid={username}) or (sAMAccountName={username})


class CourseSettings(CourseSettingsBase, table=True):
    __tablename__ = "course_settings"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    
    course: Course = Relationship(back_populates="settings")


class CourseSettingsCreate(SQLModel):
    llm_api_url: Optional[str] = None
    llm_model: Optional[str] = None
    grading_prompt: Optional[str] = None
    use_ldap: bool = False
    ldap_server: Optional[str] = None
    ldap_base_dn: Optional[str] = None
    ldap_bind_dn: Optional[str] = None
    ldap_bind_pw: Optional[str] = None
    ldap_user_search: Optional[str] = None


class CourseSettingsRead(CourseSettingsBase):
    id: int


class CourseSettingsUpdate(SQLModel):
    llm_api_url: Optional[str] = None
    llm_model: Optional[str] = None
    grading_prompt: Optional[str] = None
    use_ldap: Optional[bool] = None
    ldap_server: Optional[str] = None
    ldap_base_dn: Optional[str] = None
    ldap_bind_dn: Optional[str] = None
    ldap_bind_pw: Optional[str] = None
    ldap_user_search: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════
# COURSE INVITE (Einladungslink)
# ═══════════════════════════════════════════════════════════════════

class CourseInviteBase(SQLModel):
    course_id: int = Field(foreign_key="courses.id")
    token: str = Field(unique=True, index=True, max_length=100)
    expires_at: Optional[datetime] = None  # NULL = kein Ablauf
    max_uses: Optional[int] = None  # NULL = unbegrenzt
    used_count: int = Field(default=0)


class CourseInvite(CourseInviteBase, table=True):
    __tablename__ = "course_invites"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_by: int = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.now)

    course: "Course" = Relationship()


class CourseInviteCreate(SQLModel):
    expires_days: int = 7
    max_uses: Optional[int] = None


class CourseInviteRead(SQLModel):
    id: int
    course_id: int
    token: str
    expires_at: Optional[datetime]
    max_uses: Optional[int]
    used_count: int
    created_by: int
    created_at: datetime

    class Config:
        from_attributes = True


# ═══════════════════════════════════════════════════════════════════
# FORUM (Kurs-Forum: Chat in Kanälen, offen für alle Kurs-Rollen)
# ═══════════════════════════════════════════════════════════════════

class ForumChannelBase(SQLModel):
    course_id: int = Field(foreign_key="courses.id", index=True)
    name: str = Field(max_length=100)
    description: str = Field(default="", max_length=300)
    created_by: int = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.now)


class ForumChannel(ForumChannelBase, table=True):
    __tablename__ = "forum_channels"

    id: Optional[int] = Field(default=None, primary_key=True)

    course: Optional["Course"] = Relationship(back_populates="channels")
    messages: List["ForumMessage"] = Relationship(back_populates="channel")


class ForumChannelCreate(SQLModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=300)


class ForumChannelUpdate(SQLModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=300)


class ForumMessageBase(SQLModel):
    course_id: int = Field(foreign_key="courses.id", index=True)
    channel_id: Optional[int] = Field(default=None, foreign_key="forum_channels.id", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    content: str = Field(max_length=5000)
    created_at: datetime = Field(default_factory=datetime.now)


class ForumMessage(ForumMessageBase, table=True):
    __tablename__ = "forum_messages"

    id: Optional[int] = Field(default=None, primary_key=True)

    user: Optional["User"] = Relationship()
    course: Optional["Course"] = Relationship()
    channel: Optional["ForumChannel"] = Relationship(back_populates="messages")


class ForumMessageCreate(SQLModel):
    content: str = Field(min_length=1, max_length=5000)


class ForumMessageRead(SQLModel):
    id: int
    course_id: int
    channel_id: Optional[int] = None
    user_id: int
    content: str
    created_at: datetime
    username: str
    name: str
    role: str
    avatar: Optional[str] = None
    can_delete: bool = False


class ForumChannelReadState(SQLModel, table=True):
    """Letzter gelesener Nachrichten-Stand je User und Forum-Kanal.

    Basis für die Ungelesen-Zähler: Alle Nachrichten mit
    id > last_read_message_id sind ungelesen (0 = Kanal noch nie gelesen).
    Neue Tabelle wird beim App-Start per create_all angelegt.
    """

    __tablename__ = "forum_channel_read_state"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    channel_id: int = Field(foreign_key="forum_channels.id", index=True)
    last_read_message_id: int = Field(default=0)

    __table_args__ = (UniqueConstraint("user_id", "channel_id"),)


# ═══════════════════════════════════════════════════════════════════
# SKRIPT-FRAGEN (Studenten-Fragen zum Skript, inkl. LLM- +
# Human-Antworten; für Tutoren/Profs zur Skript-Verbesserung)
# ═══════════════════════════════════════════════════════════════════

class ScriptQuestionBase(SQLModel):
    course_id: int = Field(foreign_key="courses.id", index=True)
    # None = Allgemeine Frage (kein konkretes Kapitel / Kapitel gelöscht)
    section_id: Optional[int] = Field(default=None, foreign_key="course_script_sections.id", index=True)
    student_id: int = Field(foreign_key="users.id", index=True)
    question: str = Field(max_length=2000)
    # Optionaler Text-Exkurs aus dem Skript, auf den sich die Frage bezieht
    quote: str = Field(default="", max_length=1000)
    # Kontext um die Quote (Quote + ~120 Zeichen vor/nachher, normalisiert),
    # damit die Stelle im Skript eindeutig zu finden ist; None bei älteren Fragen
    quote_ctx: Optional[str] = Field(default=None, max_length=2000)
    # Startoffset der Quote innerhalb von quote_ctx (normalisierter String)
    quote_off: int = Field(default=0)
    # "open" | "addressed" (von Tutoren/Profs umschaltbar)
    status: str = Field(default="open", max_length=20)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ScriptQuestion(ScriptQuestionBase, table=True):
    __tablename__ = "script_questions"

    id: Optional[int] = Field(default=None, primary_key=True)

    responses: List["ScriptQuestionResponse"] = Relationship(back_populates="question")


class ScriptQuestionResponse(SQLModel, table=True):
    __tablename__ = "script_question_responses"

    id: Optional[int] = Field(default=None, primary_key=True)
    question_id: int = Field(foreign_key="script_questions.id", index=True)
    # None = LLM-Antwort (source == "llm")
    user_id: Optional[int] = Field(default=None, foreign_key="users.id")
    # "llm" | "human"
    source: str = Field(default="human", max_length=10)
    content: str = Field(default="", max_length=8000)
    created_at: datetime = Field(default_factory=datetime.now)

    question: Optional["ScriptQuestion"] = Relationship(back_populates="responses")


# ═══════════════════════════════════════════════════════════════════
# COURSE IMPORT (Kurs-Materialien aus einem Zip importieren, PROF/Admin)
# ═══════════════════════════════════════════════════════════════════

class CourseImport(SQLModel, table=True):
    """Eine Zeile pro Zip-Import. Max. ein Import pro Kurs (gelöscht via API).

    Der Status pro Stufe IST das Steuerungs-Flag (kein separates paused-Feld):
    pending → running → done / error, dazwischen pausierbar (paused),
    abbrechbar (cancelled), bei App-Neustart interrupted. Der Runner liest
    den Status stateless aus der DB (frische Session pro Gate/Unit) —
    Pause/Resume/Cancel/Neustart laufen über denselben Code-Pfad.

    JSON-Spalten:
    - manifest:     [{path, type, size, read_path?, pages?, line_count?, main_tex?}]
                    (klassifizierte Zip-Einträge; read_path = Datei, die gelesen
                    wird = Sidecar (.md) für docx/pptx/pdf, sonst die Originale)
    - file_map:     {path: {status, chunks: [{start_line, end_line, summary,
                    headings: [{text, line, level}], error?}], error?}}
                    (LLM-Struktur-Digests der Text-Dateien; Zeilen 1-basiert, inclusive)
    - media_map:    {path: {url, urls?, media_id, media_ids?}} (importierte Medien)
    - reference_map: {key: {entry_type, authors, title, year, venue, detail, address,
                    doi, url, note, stub?, sources: [Datei], imported_ref_id?}}
                    (detected Quellen aus .bib-Files + \\cite-Keys; stub = Key ohne Bib-Daten)
    - chapter_plan: [{title, enabled, description?, section_id?,
                    script_status: pending|done|error, script_error?}]
                    (Skript-Plan; description nennt die Text-Quelldateien mit
                    Pfad + Zeilenbereich. sources/sections (Gather-Ergebnis) und
                    section_id (generiertes Kapitel, Edit-Modus/Reihenfolge) werden
                    zur Laufzeit ergänzt; ältere Pläne können zusätzlich „sources“/
                    „slides“ enthalten und werden toleriert, aber ignoriert)
    - slides_plan:  [{title, enabled, description?, material_id?,
                    slides_status: pending|done|error, slides_error?}]
                    (Folien-Plan; description nennt Quelldateien inkl. pptx-Pfad
                    + Folienbereich. sources/sections (Gather-Ergebnis) und
                    material_id (generiertes Deck) werden zur Laufzeit ergänzt)
    - progress:     {current: str, stages: {stage: {total, done, failed}},
                    units: {unit_key: {done, total}}}
    - report:       {warnings: [], errors: [], summary?, slides_options?: {...}}
    """
    __tablename__ = "course_imports"

    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: int = Field(foreign_key="courses.id", index=True)
    # Staging-Verzeichnisname: data/imports/course_{id}/{job_id}/
    job_id: str = Field(max_length=32, unique=True)
    zip_name: str = Field(max_length=300)
    created_by: int = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    filemap_status: str = Field(default="pending", max_length=20)
    media_status: str = Field(default="pending", max_length=20)
    references_status: str = Field(default="pending", max_length=20)
    ref_extract_status: str = Field(default="pending", max_length=20)  # LLM-Quellen-Extraktion
    plan_status: str = Field(default="pending", max_length=20)  # Skript-Kapitel-Planner
    slides_plan_status: str = Field(default="pending", max_length=20)  # Folien-Planner
    script_status: str = Field(default="pending", max_length=20)
    slides_status: str = Field(default="pending", max_length=20)

    manifest: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    file_map: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    media_map: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    reference_map: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    chapter_plan: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    slides_plan: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    progress: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    report: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))


class LLMDebugEntry(SQLModel, table=True):
    """Persistentes LLM-Debug-Log für die Admin-Konsole (Tab "LLM-Debug-Log").

    Jeder LLM-Call wird mit Prompt-Typ, auslösendem Nutzer, Modell/URL,
    public/private, System-Prompt, Prompt, Antwort, Thinking, Latenz und
    Status protokolliert.
    Retention: 7 Tage — alte Einträge werden beim Purgen gelöscht
    (services/llm_service.py). Neue Tabelle wird beim App-Start per create_all angelegt.
    """
    __tablename__ = "llm_debug_entries"

    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=datetime.now, index=True)
    label: str = Field(default="", max_length=100)
    model: str = Field(default="", max_length=200)
    url: str = Field(default="", max_length=500)
    is_public: bool = False
    user_id: Optional[int] = Field(default=None, index=True)  # auslösender User (NULL = kein User-Kontext)
    system_prompt: str = ""
    prompt: str = ""
    response: str = ""
    thinking: str = ""
    success: bool = True
    error: str = ""
    latency_ms: int = 0
    attempts: int = 1