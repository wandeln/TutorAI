# 📚 Tutor-System

AI-gestütztes Tutor-System für Übungsaufgaben an Universitäten.

Tutoren erstellen Aufgaben mit LLM-Unterstützung, Studenten lösen Aufgaben und erhalten sofortiges, konstruktives Feedback — alles über einen zentralen Server.

## ✨ Features

### Rollen

| Rolle | Kann |
|---|---|
| **Administrator** | Kurse erstellen, User verwalten, LLM-Endpoint konfigurieren, LDAP einrichten, Grading-Prompts anpassen |
| **Tutor** | Aufgaben erstellen (Text/Code/MC), LLM bei Erstellung nutzen, Einreichungen korrigieren, Feedback überschreiben, Übersichtstabelle + Excel-Export |
| **Student** | Aufgaben sehen & lösen, sofortiges LLM-Feedback erhalten, eigene Punkte einsehen, Tests ausführen (Code-Aufgaben) |

### Aufgabentypen

- **Textaufgaben** — Freier Text, LLM-basierte Korrektur
- **Codeaufgaben** — Python-Code mit Unit-Tests (public/private), sandbox-basierte Ausführung
- **Multiple Choice** — Auswahlaufgaben

### LLM-Integration

- OpenAI-kompatibler API-Endpoint (Qwen3, Llama, Mistral, etc.)
- Konfigurierbar pro Kurs
- Grading-Prompts vom Admin anpassbar
- LLM-Assisted Task Creation für Tutoren

### Sicherheit

- Server-basierte Code-Sandbox (`subprocess` + `resource` limits)
- Timeout, Memory-Limit, CPU-Limit
- JWT-Auth mit httpOnly-Cookies
- Optionale LDAP-Authentifizierung
- RBAC pro Kurs

## 🛠️ Tech-Stack (100% Open-Source)

| Komponente | Technologie | Lizenz |
|---|---|---|
| Backend | FastAPI + SQLModel | MIT |
| Frontend | Jinja2 + HTMX + Tailwind CDN | MIT / BSD |
| Code-Editor | CodeMirror 5 | MIT |
| Datenbank | SQLite (→ PostgreSQL) | Public Domain |
| Auth | JWT + bcrypt + LDAP | MIT |
| LLM Client | openai SDK | Apache 2.0 |
| Excel | openpyxl | MIT |

## 🚀 Installation

### 1. Clone & Dependencies

```bash
cd tutor-system
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Konfiguration

```bash
cp .env.example .env
# .env bearbeiten (LLM-Endpoint, LDAP, etc.)
```

### 3. Start

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Der Server ist dann unter `http://localhost:8000` erreichbar.

### 4. Demo-Accounts

Beim ersten Start werden automatisch Demo-Daten erstellt:

| Role | Username | Password |
|---|---|---|
| Admin | `admin` | `admin123` |
| Tutor | `tutor1` | `tutor123` |
| Student | `student1` | `student123` |
| ... | `student2-5` | `student123` |

## 📁 Projektstruktur

```
tutor-system/
├── main.py                  # FastAPI App + Web-Routes
├── config.py                # Zentrale Konfiguration (env vars)
├── .env                     # Secrets & Settings
├── requirements.txt
├── database/
│   ├── base.py              # DB Engine + Session
│   └── models.py            # SQLModel Tabellen (User, Course, Task, ...)
├── services/
│   ├── auth_service.py      # JWT + LDAP Auth + RBAC
│   ├── llm_service.py       # OpenAI-kompatibler LLM-Client
│   ├── grading_service.py   # Grading-Orchestrierung
│   ├── sandbox_runner.py    # Sichere Code-Ausführung
│   └── export_service.py    # Excel-Export (.xlsx)
├── api/
│   ├── auth.py              # Login/Logout/Register
│   ├── admin.py             # Kurs + User + Settings
│   ├── tutor.py             # Aufgaben + Korrektur + Übersicht
│   └── student.py           # Aufgaben + Einreichung + Feedback
├── templates/
│   ├── base.html            # Master-Layout
│   ├── login.html
│   ├── dashboard.html
│   ├── admin/dashboard.html
│   ├── tutor/course_overview.html
│   ├── tutor/task_detail.html
│   ├── tutor/overview.html
│   └── student/course_overview.html
│       └── student/task_solve.html
├── static/css/main.css
└── prompts/
    ├── grading_prompt.py    # LLM-Grading-Prompt-Templates
    └── creation_prompt.py   # LLM-Task-Creation-Prompt-Templates
```

## 🎓 LLM-Setup

Das System erwartet einen **OpenAI-kompatiblen** API-Endpoint. Beispiele:

### Qwen3 auf Uni-Server

```bash
# .env
LLM_API_URL=http://llm-server.uni.de:8001/v1
LLM_API_KEY=sk-your-key
LLM_MODEL=Qwen3-32B
```

### Ollama (lokal)

```bash
# .env
LLM_API_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=qwen2.5:32b
```

### vLLM / TGI

```bash
LLM_API_URL=http://localhost:8000/v1
LLM_API_KEY=
LLM_MODEL=meta-llama/Llama-3.1-70B-Instruct
```

## 📖 API-Dokumentation

Automatisch generierte OpenAPI-Docs:
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

## 🔐 LDAP-Auth

Optional konfigurierbar im Admin-Bereich oder via `.env`:

```bash
LDAP_ENABLED=true
LDAP_SERVER=ldap.uni.de
LDAP_BASE_DN=ou=people,dc=uni,dc=de
LDAP_BIND_DN=cn=admin,dc=uni,dc=de
LDAP_BIND_PW=...
```

## 🤝 Contributing

1. Fork & Branch erstellen
2. Änderungen committen
3. Pull Request eröffnen

## 📄 License

MIT — 100% Open-Source, freier Einsatz an Universitäten.

---

*Entwickelt für den akademischen Einsatz — keine kommerzielle Lizenz erforderlich.*