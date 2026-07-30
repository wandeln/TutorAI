"""
Datenbank-Modelle (SQLModel).

Alle Tabellen des Tutor-Systems. Jede Tabelle hat:
- Ein Table-Model (für die DB)
- Ein CreateSchema (für POST/PUT)
- Ein ReadSchema (für Responses)

Das hält die API sauber und typisiert.
"""

from datetime import datetime
from typing import Optional, List
from enum import Enum
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
    MC = "mc"


class SubmissionStatus(str, Enum):
    PENDING = "pending"
    GRADED = "graded"
    OVERRIDDEN = "overridden"


class FeedbackSource(str, Enum):
    LLM = "llm"
    HUMAN = "human"


class TestVisibility(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"


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


class Task(TaskBase, table=True):
    __tablename__ = "tasks"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    created_by: int = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    
    # Relationships
    course: Course = Relationship(back_populates="tasks")
    creator: User = Relationship(back_populates="created_tasks")
    test_cases: List["TestCase"] = Relationship(back_populates="task")
    submissions: List["Submission"] = Relationship(back_populates="task")


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
    test_cases: Optional[List["TestCaseCreate"]] = Field(default=[])


class TaskRead(TaskBase):
    id: int
    created_by: int
    created_at: datetime
    updated_at: datetime
    test_cases: List["TestCaseRead"] = []


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


# ═══════════════════════════════════════════════════════════════════
# TEST CASE (Unit-Tests)
# ═══════════════════════════════════════════════════════════════════

class TestCaseBase(SQLModel):
    task_id: int = Field(foreign_key="tasks.id")
    name: str = Field(max_length=100)         # z.B. "test_fakultaet_5"
    code: str                                  # Der Test-Code
    expected_output: str = Field(default="")
    visibility: TestVisibility = TestVisibility.PUBLIC
    input_data: Optional[str] = Field(default=None)


class TestCase(TestCaseBase, table=True):
    __tablename__ = "test_cases"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    
    task: Task = Relationship(back_populates="test_cases")


class TestCaseCreate(SQLModel):
    name: str
    code: str
    expected_output: str = Field(default="")
    visibility: TestVisibility = TestVisibility.PUBLIC
    input_data: Optional[str] = Field(default=None)


class TestCaseRead(TestCaseBase):
    id: int


class TestCaseUpdate(SQLModel):
    name: Optional[str] = None
    code: Optional[str] = None
    expected_output: Optional[str] = None
    visibility: Optional[TestVisibility] = None
    input_data: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════
# SUBMISSION (Einreichung)
# ═══════════════════════════════════════════════════════════════════

class SubmissionBase(SQLModel):
    task_id: int = Field(foreign_key="tasks.id")
    student_id: int = Field(foreign_key="users.id")
    solution: str = Field(default="")          # Für Text/MC-Aufgaben
    code_solution: str = Field(default="")     # Für Code-Aufgaben
    attempt_number: int = Field(default=1)


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


class SubmissionRead(SubmissionBase):
    id: int
    submitted_at: datetime
    status: SubmissionStatus
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