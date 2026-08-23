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
from sqlalchemy import UniqueConstraint
from sqlmodel import SQLModel, Field, Relationship


# ═══════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════

class GlobalUserRole(str, Enum):
    ADMIN = "ADMIN"
    USER = "USER"


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
    settings: Optional["CourseSettings"] = Relationship(back_populates="course")


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


class CourseMaterial(CourseMaterialBase, table=True):
    __tablename__ = "course_materials"
    __table_args__ = (UniqueConstraint("course_id", "material_type", name="uq_material_course_type"),)

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


# ═══════════════════════════════════════════════════════════════════
# SCRIPT SECTIONS (Vorlesungsskript als mehrere Markdown-Kapitel)
# ═══════════════════════════════════════════════════════════════════

class ScriptSectionBase(SQLModel):
    course_id: int = Field(foreign_key="courses.id", index=True)
    title: str = Field(max_length=300)         # z.B. "Kapitel 2: Rekursion"
    content: str = Field(default="")           # Markdown (mit LaTeX/Mermaid)
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