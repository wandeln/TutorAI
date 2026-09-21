"""
Prompt für die LLM-Generierung von Image-Specs (s. docs/plan-compute-engines-images.md).

Format seit 2026-09-18: Das LLM liefert ein komplettes DOCKERFILE als
Entwurf (kein YAML). Der Name der Spec ist vorgegeben (UI-Feld) und wird
separat in der DB gespeichert — das Dockerfile enthält keinen Namen.
Konventionen werden NUR im Prompt festgelegt (kein serverseitiges
Output-Nacharbeiten); das Backend validiert (FROM, kein COPY/ADD) und
der Tutor/Prof bestätigt den Entwurf vor dem Build.
"""

IMAGE_SPEC_PROMPT_TEMPLATE = """\
Du bist Experte für Docker-Images und Programmierumgebungen (Python/ML, C++, Assembler, Server) für Uni-Kurse.

{% if current_dockerfile %}
Aufgabe: Du sollst das VORHANDENE Dockerfile (unten) anpassen.
Änderungswunsch: {{ description }}

Regeln für die Anpassung:
- ÄNDERE NUR, was der Änderungswunsch erfordert — FROM, WORKDIR und alle
  sonstigen Instruktionen, die nicht betroffen sind, bleiben unverändert.
- Gib am Ende das KOMPLETTE, aktualisierte Dockerfile aus.

Vorhandenes Dockerfile:
```dockerfile
{{ current_dockerfile }}
```
{% else %}
Aufgabe: Erstelle aus der Beschreibung unten ein komplettes DOCKERFILE für
eine Programmierumgebung, in der Studierende ihre Aufgaben in
Docker-Containern lösen.

Beschreibung der gewünschten Umgebung:
{{ description }}
{% endif %}
{% if name %}
Hinweis: Der Name der Image-Spec ist vorgegeben (`{{ name }}`) und wird
separat gespeichert — das Dockerfile selbst enthält KEINEN Namen.
{% endif %}

Regeln für das Dockerfile:
1. Gib NUR das eine komplette Dockerfile aus — ohne Code-Zaun (```), ohne
   Erklärungen davor oder danach.
2. Die ERSTE Instruktion muss `FROM` mit einem ÖFFENTLICHEN Standard-
   Basis-Image sein (z. B. python:3.11-slim, debian:bookworm-slim,
   ubuntu:22.04, gcc:12) — niemals ein privates oder tutorai/*-Image.
3. KEINE COPY- oder ADD-Instructionen (der Build-Kontext ist leer).
4. Halte das Dockerfile MINIMAL — nur Pakete, die für die Beschreibung
   wirklich nötig sind. Pinne Versionen, wo Stabilität wichtig ist.
5. Python-Pakete: eine `RUN pip install --no-cache-dir <paket …>`-Schicht
   (mit ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 vorher, wenn Python-Basis).
6. Debian/Apt-Pakete: eine einzelne Schicht
   `RUN apt-get update && apt-get install -y --no-install-recommends <pakete …> && rm -rf /var/lib/apt/lists/*`
   (mit ENV DEBIAN_FRONTEND=noninteractive vorher).
7. Am Ende: `WORKDIR /workspace`.
8. Keine GPU-/CUDA-spezifischen Pakete, außer die Beschreibung verlangt
   sie explizit (CUDA-Laufzeit kommt meist vom Host bzw. mit den
   PyTorch-Wheels von PyPI).

Gib jetzt das Dockerfile aus:
"""
