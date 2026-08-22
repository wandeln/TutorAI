"""
TutorAI: Zentrale Konfiguration / Settings

Alle Konfigurationswerte werden von Umgebungsvariablen oder .env-Datei gelesen.
Ermöglicht flexibles Deployen ohne Code-Änderungen.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# .env-Datei laden (wenn vorhanden)
load_dotenv()

# ─── Projekt-Root ───────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
DB_FILE = BASE_DIR / "data" / "tutor.db"
DB_FILE.parent.mkdir(parents=True, exist_ok=True)

# Kurs-Medien (Bilder/Applets): data/media/course_{id}/<uuid>.<ext>
# Versand über authentifizierte Route GET /media/{course_id}/{filename} (kein Static-Mount!)
MEDIA_DIR = BASE_DIR / "data" / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# ─── Server ──────────────────────────────────────────────────────
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
DEBUG = os.getenv("DEBUG", "true").lower() in ("true", "1", "yes")

# ─── Datenbank ──────────────────────────────────────────────────
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{DB_FILE}"
)

# ─── Auth / JWT ─────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))  # 8h

# ─── LLM / OpenAI-kompatibler Endpoint ──────────────────────────
LLM_API_URL = os.getenv("LLM_API_URL", "http://localhost:8001/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "sk-default")
LLM_MODEL = os.getenv("LLM_MODEL", "Qwen3-32B")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "120"))

# Public Endpoint für nicht-sensitive Aufgaben
# (z. B. Generierung neuer Aufgaben oder Musterlösungen).
# Leer lassen = Private Endpoint (wie oben) wird verwendet.
LLM_API_URL_PUBLIC = os.getenv("LLM_API_URL_PUBLIC", "")
LLM_API_KEY_PUBLIC = os.getenv("LLM_API_KEY_PUBLIC", "")
LLM_MODEL_PUBLIC = os.getenv("LLM_MODEL_PUBLIC", "")

# ─── LDAP (optional) ───────────────────────────────────────────
LDAP_ENABLED = os.getenv("LDAP_ENABLED", "false").lower() in ("true", "1", "yes")
LDAP_SERVER = os.getenv("LDAP_SERVER", "ldap.uni.de")
LDAP_BASE_DN = os.getenv("LDAP_BASE_DN", "ou=people,dc=uni,dc=de")
LDAP_BIND_DN = os.getenv("LDAP_BIND_DN", "cn=admin,dc=uni,dc=de")
LDAP_BIND_PW = os.getenv("LDAP_BIND_PW", "")
LDAP_USER_SEARCH = os.getenv("LDAP_USER_SEARCH", "(uid={username})")

# ─── Sandbox / Code-Ausführung ──────────────────────────────────
SANDBOX_TIMEOUT = int(os.getenv("SANDBOX_TIMEOUT", "15"))
SANDBOX_MEMORY_MB = int(os.getenv("SANDBOX_MEMORY_MB", "512"))
SANDBOX_CPU_SECONDS = int(os.getenv("SANDBOX_CPU_SECONDS", "30"))

# Erlaubte Python-Module in der Sandbox (Standardbibliothek)
SANDBOX_ALLOWED_MODULES = [
    "matplotlib", 
    "math", "collections", "itertools", "typing", "dataclasses",
    "array", "heapq", "bisect", "random", "string", "re",
    "datetime", "unittest", "io", "sys", "json", "functools",
    "operator", "copy", "enum", "abc", "numbers", "fractions",
    "decimal", "statistics", "time", "os", "pathlib", "hashlib",
]

# ─── Frontend ───────────────────────────────────────────────────
CODEMIRROR_THEME = os.getenv("CODEMIRROR_THEME", "dracula")
CODEMIRROR_VERSION = "5.65.16"

# ─── Excel-Export ────────────────────────────────────────────────
EXCEL_SHEET_NAME = os.getenv("EXCEL_SHEET_NAME", "Punktestand")