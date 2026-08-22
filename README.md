# 📚 TutorAI

AI-gestütztes Tutoring-System für Übungsaufgaben an Universitäten.

PROFs und Tutoren erstellen Aufgaben mit LLM-Unterstützung, Studenten lösen Aufgaben und erhalten sofortiges, konstruktives Feedback — alles über einen zentralen Server.

## ✨ Features

### Rollen-Modell

Das System unterscheidet **globale Rollen** (Systemebene) und **Kurs-Rollen** (pro Kurs):

| Globale Rolle | Beschreibung |
|---|---|
| **Admin** | Admin-Konsole: Kurse & User verwalten, globale LLM/LDAP-Einstellungen anpassen |
| **User** | Standard-Rolle — Berechtigungen werden über die Kurs-Rolle bestimmt |

| Kurs-Rolle | Kann |
|---|---|
| **Prof** | Kurs bearbeiten (Name, Semester, Beschreibung), Mitglieder verwalten (hinzufügen, Rollen ändern, entfernen), Einladungslinks erstellen, Aufgaben erstellen/bearbeiten/löschen, Sichtbarkeit umschalten, Aufgaben per Drag-and-Drop ordnen, Einreichungen korrigieren, Feedback überschreiben, Übersichtstabelle + Excel-Export, Skript/Slides anlegen & löschen, Medienbibliothek (Upload/Description/Delete) |
| **Tutor** | Aufgaben erstellen/bearbeiten (LLM-Aufgabe generieren), Einreichungen korrigieren, Feedback überschreiben, Übersichtstabelle + Excel-Export, Skript/Slides anlegen & bearbeiten |
| **Student** | Aufgaben sehen & lösen, sofortiges LLM-Feedback erhalten, eigene Punkte einsehen, Tests ausführen (Code-Aufgaben), vorherige/nächste Aufgabe navigieren, Skript & Slides lesen, Name & Passwort selbst ändern |

> **Hinweis:** Ein globaler Admin hat uneingeschränkten Zugriff auf alle Kurse, auch ohne Kurs-Mitgliedschaft. Ein Prof kann alle Kurs-Rollen zuweisen — nur die Ernennung zu Prof darf der Admin.

### Kurs-Beitritt

- **Einladungslinks:** Prof/Admin generiert Token mit Gültigkeitsdauer und optionaler Nutzungsgrenze. Copy-to-Clipboard der vollständigen Join-URL.
- **Manuelle Einladung:** Prof/Admin sucht User und fügt sie direkt zum Kurs hinzu (mit Rollenauswahl)
- **Join-Seite:** User gibt Token ein (via Link) und tritt dem Kurs bei

### Aufgabentypen

- **Textaufgaben** — Freier Text mit Markdown & LaTeX-Rendering, LLM-basierte Korrektur
- **Codeaufgaben** — Python-Code mit Unit-Tests (public/private), sandbox-basierte Ausführung, CodeMirror-Editor

### Kurs-Material & Medienbibliothek

- **Registerkarten je Kurs:** Skript, Slides, Aufgaben, Übersicht (Tutor+), Medien (PROF+), Mitglieder (PROF+)
- **Vorlesungsskript** — Markdown (LaTeX, Mermaid), pro Kurs maximal ein Skript, Sichtbarkeit umschaltbar
- **Vorlesungs-Slides** — ein Markdown-Dokument, Folien durch `---` getrennt (reveal.js-Rendering folgt in einem späteren Schritt)
- **Medienbibliothek je Kurs** (`data/media/course_{id}/`) — Bilder (PNG/JPG/WebP/GIF, max. 5 MB, UUID-Namen) mit LLM-Beschreibung pro Medium
- **Medien-Versand** nur über authentifizierte Route (Kurs-Membership erforderlich; versteckte Medien nur für Tutor/PROF)
- **Einbindung & Verwendungs-Tracking:** Medien werden per Markdown-Snippet eingebunden (`![Titel](/media/{course_id}/{datei})`); die Verwendungs-Orte (Skript/Slides/Aufgabe) werden automatisch aus dem Content abgeleitet (`media_usages`), doppelte Referenzen werden markiert

### LLM-Integration

- OpenAI-kompatibler API-Endpoint (Qwen3, Llama, Mistral, etc.)
- Globale LLM-Konfiguration in der Admin-Konsole (Endpoint, Modell, API-Key)
- Verbindungstest direkt in der UI
- LLM-Assisted Task Creation: Prof/Tutor gibt Thema + Schwierigkeitsgrad ein, LLM generiert Aufgabenentwurf

### User-Self-Service

- Name und Passwort ändern (über „Meine Einstellungen" in der Navigation)
- Bei LDAP-Accounts: Nameänderung möglich, Passwortänderung erfolgt über LDAP

### Sicherheit

- Server-basierte Code-Sandbox (`subprocess` + `resource` limits)
- Timeout, Memory-Limit, CPU-Limit
- Erlaubte Python-Module konfigurierbar (Standardbibliothek + `math`, `collections`, `matplotlib`, etc.)
- JWT-Auth mit httpOnly-Cookies
- Optionale LDAP-Authentifizierung (globale Konfiguration)
- RBAC: Globale Rollen + Kurs-Rollen

## 🛠️ Tech-Stack (100% Open-Source)

| Komponente | Technologie | Lizenz |
|---|---|---|
| Backend | FastAPI + SQLModel | MIT |
| Frontend | Jinja2 + HTMX + Tailwind CDN | MIT |
| Code-Editor | CodeMirror 5 | MIT |
| Markdown + LaTeX | marked + KaTeX | MIT |
| Syntax-Highlighting | highlight.js | BSD-3 |
| Drag-and-Drop | SortableJS | MIT |
| Datenbank | SQLite (→ PostgreSQL) | Public Domain |
| Auth | JWT + hashlib (SHA-256) + LDAP | MIT |
| LLM Client | openai SDK | Apache 2.0 |
| Excel | openpyxl | MIT |

## 🚀 Installation

### 1. Clone & Dependencies

```bash
cd TutorAI
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Konfiguration

```bash
cp .env.example .env
# .env bearbeiten (LLM-Endpoint, LDAP, etc.)
```

Wichtige Umgebungsvariablen:

| Variable | Standard | Beschreibung |
|---|---|---|
| `SECRET_KEY` | `dev-secret-change-me-in-production` | JWT-Signatur-Secret |
| `LLM_API_URL` | `http://localhost:8001/v1` | OpenAI-kompatibler Endpoint |
| `LLM_API_KEY` | `sk-default` | API-Key für LLM |
| `LLM_MODEL` | `Qwen3-32B` | Modellname |
| `LDAP_ENABLED` | `false` | LDAP-Auth aktivieren |
| `SANDBOX_TIMEOUT` | `15` | Code-Ausführung Timeout (Sekunden) |
| `SANDBOX_MEMORY_MB` | `512` | Memory-Limit für Sandbox |

### 3. Start

**Entwicklungsmodus** (mit Hot-Reload):
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**Hintergrund-Start** (Output wird geloggt):
```bash
nohup python -m uvicorn main:app --host 0.0.0.0 --port 8000 > server-output.log 2>&1 &
```

Der Server ist dann unter `http://localhost:8000` erreichbar.

> **Debugging:** Server-Logs (Errors, Request-Trace, etc.) landen in `server-output.log`. Bei Problemen immer zuerst die letzten Zeilen dieser Datei prüfen:
> ```bash
> tail -n 50 server-output.log
> ```

### 4. Erster Start

Beim ersten Start wird automatisch ein Admin-Account angelegt:

| Username | Password | Rolle |
|---|---|---|
| `admin` | `admin` | Admin |

> **Wichtig:** Ändere das Admin-Passwort nach dem ersten Login in den Einstellungen!

Melde dich mit diesen Credentials an und erstelle über die **Admin-Konsole** deine ersten Kurse, User und Aufgaben.

## 📁 Projektstruktur

```
TutorAI/
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
│   ├── settings_resolver.py # Settings-Auflösung (global → Kurs)
│   ├── export_service.py    # Excel-Export (.xlsx)
│   └── media_service.py     # Medien-Speicher + MediaUsage-Reconciliation
├── api/
│   ├── auth.py              # Login/Logout/Register
│   ├── admin.py             # Kurs + User + globale Settings (Admin)
│   ├── course_members.py    # Kurs-Mitglieder + Einladungen (Prof/Admin)
│   ├── tutor.py             # Aufgaben + Korrektur + Übersicht
│   ├── student.py           # Aufgaben + Einreichung + Feedback
│   ├── media.py             # Medienbibliothek: Upload/Liste/Edit/Delete (PROF)
│   ├── materials.py         # Skript & Slides (Markdown, je Kurs max. 1 pro Typ)
│   └── user_settings.py     # Eigene Einstellungen bearbeiten
├── templates/
│   ├── base.html            # Master-Layout (Nav, Toast, Markdown/LaTeX)
│   ├── login.html           # Login-Seite
│   ├── dashboard.html       # Kurs-Übersicht nach Login
│   ├── join.html            # Kurs-Beitritt per Einladungslink
│   ├── user_settings.html   # Eigene Einstellungen
│   ├── admin/
│   │   └── dashboard.html   # Admin-Konsole (Kurse, User, Settings)
│   ├── course/              # Kurs-Tab-Seiten (Tab-Leiste + Rollensichtbarkeit)
│   │   ├── base.html        # Parent: Kurs-Header, Tabs, Kurs-Edit-Modal
│   │   ├── _tabs.html       # Tab-Leiste-Partial (Skript/Slides/Aufgaben/Übersicht/Medien/Mitglieder)
│   │   ├── material.html    # Tab 'Skript' & 'Slides' (Markdown, Editor für Tutor/PROF)
│   │   ├── media.html       # Tab 'Medien': Upload, Verwendungs-Orte (PROF/Admin)
│   │   ├── tasks_student.html  # Tab 'Aufgaben': Punktestand + Filter (Student)
│   │   ├── tasks_tutor.html    # Tab 'Aufgaben': Drag-and-Drop, Sichtbarkeit (Tutor/PROF)
│   │   ├── overview.html      # Tab 'Übersicht': Punktübersicht + Excel-Export
│   │   └── members.html       # Tab 'Mitglieder': Rollen, Einladungen (PROF/Admin)
│   ├── tutor/
│   │   ├── task_detail.html # Aufgabe erstellen/bearbeiten (LLM-Aufgabe generieren)
│   │   └── submission_review.html # Einzelne Einreichung bewerten
│   └── student/
│       └── task_solve.html  # Aufgabe lösen (Editor + Markdown/LaTeX)
├── static/
│   ├── css/main.css         # Custom Styles
│   └── js/markdown-renderer.js # Markdown + LaTeX Rendering
├── prompts/
│   ├── grading_prompt.py    # LLM-Grading-Prompt-Templates
│   ├── creation_prompt.py   # LLM-Task-Creation-Prompt-Templates
│   └── solution_prompt.py   # LLM-Solution-Hint-Prompt-Templates
└── data/
    ├── tutor.db             # SQLite-Datenbank (dev)
    └── media/course_{id}/   # Kurs-Medien (UUID-Namen, per Upload)
```

## 🎓 LLM-Setup

Das System erwartet einen **OpenAI-kompatiblen** API-Endpoint. Die Konfiguration erfolgt in der Admin-Konsole (oder via `.env`).

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

Optional konfigurierbar — global in der Admin-Konsole oder via `.env`:

```bash
# Globale LDAP-Konfiguration (.env)
LDAP_ENABLED=true
LDAP_SERVER=ldap://ldap.uni.de
LDAP_BASE_DN=dc=informatik,dc=uni,dc=de
LDAP_BIND_DN=cn=ldapbrowse,dc=informatik,dc=uni,dc=de
LDAP_BIND_PW=...
LDAP_USER_SEARCH=(uid={username})
```

Die Admin-Konsole bietet eine vollständige LDAP-Konfiguration mit Verbindungstest. Bei Active Directory kann der Search Filter z. B. auf `(sAMAccountName={username})` gesetzt werden. Bei aktiviertem LDAP werden neue User automatisch angelegt, wenn die LDAP-Auth erfolgreich ist.

## 🤝 Contributing

1. Fork & Branch erstellen
2. Änderungen committen
3. Pull Request eröffnen

## 📄 License

MIT — 100% Open-Source, freier Einsatz an Universitäten.

---

*Entwickelt für den akademischen Einsatz — keine kommerzielle Lizenz erforderlich.*
