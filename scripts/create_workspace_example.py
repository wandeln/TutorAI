#!/usr/bin/env python3
"""
Erzeugt eine Beispiel-Workspace-Aufgabe (Preset "python-ml") zum Testen.

Aufgabe: Kleiner Neuronaler-Netze-Klassifikator auf einem synthetischen
3-Klassen-Dataset (20 Features). Die Studenten trainieren ein MLP,
evaluieren es und schreiben Artefakte (results.json, loss.png).

Dateien (Zugriffsklassen):
  data/  (Ordner 🔒)           → Dataset (read-only, im Container /workspace/data/)
  main.py, train.py            → ✏️ starter (editierbar)
  run.sh (🔒)                 → „▶ Ausführen“-Skript (führt main.py aus)
  test.py + test.sh (🔒)      → öffentliche Self-Check-Tests („🧪 Test“, Wurzel)
  .tests/test_solution.py (👤)  → privat (pytest-Tests für die Bewertung)
  .solution/solution.py (👤)    → privat (Musterlösung, spiegelt die editierbaren Pfade)
  .test_private.sh (👤)         → Judge (wird bei der Bewertung ausgeführt)
  .run_solution.sh (👤)         → Tutor-Testlauf (Musterlösung + beide Tests)
  .init_hidden.sh (👤)          → private Init-Phase (hier: no-op)

Aufruf (vom Projekt-Root):
    python scripts/create_workspace_example.py <course_id> [--title TITEL] [--dry-run]

Nach dem Anlegen wird on_task_saved() angestoßen (Asset-Sync zu den
Compute-Agenten). Liegt kein Agent/Server online, wird das gemeldet —
die Aufgabe ist trotzdem erstellt; der Sync kann später über die
Tutor-UI (🔄 Sync) nachgeholt werden.
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import numpy as np  # noqa: E402

from database.base import create_db_and_tables, engine  # noqa: E402
from database.models import (  # noqa: E402
    Course,
    Task,
    TaskType,
)
from services.workspace_service import workspace_service  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

# ─── Deterministisches Synthetic-Dataset ────────────────────────

DATASET_SEED = 42
DATASET_SAMPLES = 1500
DATASET_FEATURES = 20
DATASET_CLASSES = 3


def generate_dataset_csv() -> str:
    """3 gut getrennte Gaußsche Wolken → kleines MLP reicht für >90 %."""
    rng = np.random.default_rng(DATASET_SEED)
    means = rng.normal(0.0, 1.5, size=(DATASET_CLASSES, DATASET_FEATURES))
    per_class = DATASET_SAMPLES // DATASET_CLASSES
    rows = []
    for cls in range(DATASET_CLASSES):
        n = per_class if cls < DATASET_CLASSES - 1 else DATASET_SAMPLES - per_class * (DATASET_CLASSES - 1)
        X = means[cls] + rng.normal(0.0, 0.8, size=(n, DATASET_FEATURES))
        for i in range(n):
            rows.append(",".join(f"{v:.6f}" for v in X[i]) + f",{cls}")
    header = ",".join(f"x{i+1}" for i in range(DATASET_FEATURES)) + ",y"
    return header + "\n" + "\n".join(rows) + "\n"


# ─── Datei-Inhalte ──────────────────────────────────────────────

MAIN_PY = '''"""Starter: Klassifikor auf dem Dataset trainieren.

Deine Aufgabe:
  1. Dataset laden:  /workspace/data/dataset.csv  (read-only, 20 Features, 3 Klassen)
  2. Modell trainieren (siehe train.py — dort liegen die TODOs)
  3. In /workspace schreiben:
       results.json  → JSON mit den Keys:
                        "accuracy"       (Test-Accuracy, float 0..1)
                        "train_accuracy" (Train-Accuracy, float 0..1)
                        "epochs"         (int)
                        "final_loss"     (float)
       loss.png      → Loss-Verlauf über die Epochen (matplotlib)

Testen kannst du mit dem ▶ Ausführen-Button (führt run.sh = python3 main.py aus).
"""
import json

import numpy as np
import pandas as pd

DATASET_PATH = "/workspace/data/dataset.csv"


def load_dataset():
    df = pd.read_csv(DATASET_PATH)
    feature_cols = [c for c in df.columns if c.startswith("x")]
    X = df[feature_cols].to_numpy(dtype=float)
    y = df["y"].to_numpy(dtype=int)
    return X, y


def main():
    X, y = load_dataset()
    print(f"Dataset: {X.shape[0]} Samples, {X.shape[1]} Features, "
          f"{len(np.unique(y))} Klassen")
    print(f"Klassen-Verteilung: {np.bincount(y)}")

    # TODO: Modell bauen (train.build_model), trainieren (train.train),
    #       auf dem Test-Teil evaluieren und Artefakte schreiben:
    #       - results.json  (Keys: accuracy, train_accuracy, epochs, final_loss)
    #       - loss.png      (Loss-Kurve, plt.savefig("loss.png"))
    raise NotImplementedError(
        "TODO: Implementiere den Training-Loop in train.py und den "
        "Aufruf hier in main.py (siehe train.py für die Gerüste).")


if __name__ == "__main__":
    main()
'''

TRAIN_PY = '''"""Modell-Gerüst: Fülle die TODOs.

Empfohlener Aufbau (funktioniert gut für dieses Dataset):
  - Daten in numpy → torch.Tensor konvertieren
  - Train/Test-Split (z.B. 80/20, fixer Zufallssamen)
  - Features standardisieren (z-Score) — hilft bei der Konvergenz
"""
import torch
import torch.nn as nn


class MLP(nn.Module):
    """Kleines 2-versteckte-Schichten-MLP (Architektur schon vorgegeben)."""

    def __init__(self, in_dim: int, hidden: int = 64, out_dim: int = 3):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
            nn.Linear(hidden, out_dim),
        )

    def forward(self, x):
        # TODO: Logits = self.net(x) ausgeben
        raise NotImplementedError("TODO: forward() implementieren")


def build_model(in_dim: int, out_dim: int = 3) -> MLP:
    return MLP(in_dim, out_dim=out_dim)


def train(model, X_train, y_train, epochs: int = 60, lr: float = 1e-2,
          batch_size: int = 64):
    """Trainiere das Modell.

    TODO: z.B. nn.CrossEntropyLoss + Adam; mini-batches über das
    Train-Set; nach jeder Epoche den mittleren Loss erfassen.

    Returns:
        history: Liste [(epoch, loss), ...] — wird für loss.png gebraucht.
    """
    raise NotImplementedError("TODO: train() implementieren")
'''

TESTS_PY = '''"""Private Verifikation (Grading): prüft NUR die Artefakte in /workspace.

Wird über .test_private.sh im Grading-Container ausgeführt
(working_dir=/workspace). results.json und loss.png müssen also bereits
erstellt sein (vom Student-Run bzw. vor dem Grading neu ausgeführt).
"""
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # /workspace
RESULTS = os.path.join(BASE, "results.json")
LOSS_PNG = os.path.join(BASE, "loss.png")


def _load_results():
    with open(RESULTS, "r", encoding="utf-8") as f:
        return json.load(f)


def test_results_json_exists():
    assert os.path.isfile(RESULTS), (
        "results.json fehlt in /workspace — "
        "führe `python3 main.py` aus, bevor du abgibst.")


def test_results_json_structure():
    res = _load_results()
    for key in ("accuracy", "train_accuracy", "epochs", "final_loss"):
        assert key in res, f"results.json fehlt Key: {key}"
    assert isinstance(res["epochs"], int) and res["epochs"] >= 1
    assert 0.0 <= res["accuracy"] <= 1.0
    assert 0.0 <= res["train_accuracy"] <= 1.0


def test_accuracy_sufficient():
    res = _load_results()
    acc = float(res["accuracy"])
    assert acc >= 0.90, (
        f"Test-Accuracy zu niedrig: {acc:.3f} (mindestens 0.90 erforderlich). "
        "Tipp: mehr Epochen, Learning-Rate anpassen, Features standardisieren.")


def test_loss_png_valid():
    assert os.path.isfile(LOSS_PNG), "loss.png fehlt in /workspace"
    with open(LOSS_PNG, "rb") as f:
        magic = f.read(8)
    assert magic == b"\\x89PNG\\r\\n\\x1a\\n", "loss.png ist kein gültiges PNG"
'''

RUN_SH = """#!/bin/sh
# ▶ Ausführen: trainiert/evaluiert das Modell und schreibt die Artefakte.
set -e
cd /workspace
python3 main.py
"""

PUBLIC_TESTS_SH = """#!/bin/sh
# 🧪 Öffentliche Self-Check-Tests: Struktur der Artefakte (ohne Schwellwerte).
set -e
cd /workspace
python3 -m pytest test.py -v
"""

PUBLIC_TESTS_PY = '''"""Öffentliche Self-Check-Tests: Vorhandensein + Struktur der Artefakte.

Die Schwellwerte (Accuracy) werden NICHT hier geprüft — das ist Aufgabe
der privaten Tests bei der Bewertung.
"""
import json
import os

BASE = "/workspace"
RESULTS = os.path.join(BASE, "results.json")
LOSS_PNG = os.path.join(BASE, "loss.png")


def test_results_json_exists():
    assert os.path.isfile(RESULTS), (
        "results.json fehlt in /workspace — "
        "führe zuerst `python3 main.py` (▶ Ausführen) aus.")


def test_results_json_structure():
    with open(RESULTS, "r", encoding="utf-8") as f:
        res = json.load(f)
    for key in ("accuracy", "train_accuracy", "epochs", "final_loss"):
        assert key in res, f"results.json fehlt Key: {key}"
    assert isinstance(res["epochs"], int) and res["epochs"] >= 1
    assert 0.0 <= float(res["accuracy"]) <= 1.0
    assert 0.0 <= float(res["train_accuracy"]) <= 1.0


def test_loss_png_valid():
    assert os.path.isfile(LOSS_PNG), "loss.png fehlt in /workspace"
    with open(LOSS_PNG, "rb") as f:
        magic = f.read(8)
    assert magic == b"\\x89PNG\\r\\n\\x1a\\n", "loss.png ist kein gültiges PNG"
'''

PRIVATE_TESTS_SH = """#!/bin/sh
# Private Tests (Bewertung): wird serverseitig nach der Abgabe ausgeführt.
set -e
cd /workspace
python3 -m pytest .tests/ -v
"""

RUN_SOLUTION_SH = """#!/bin/bash
# 🧪 Tutor-Testlauf: Musterlösung aus .solution/ über die editierbaren
# Dateien legen und gegen die öffentlichen + privaten Tests prüfen.
set -e
cd /workspace
cp -rf .solution/. ./
bash run.sh
[ -f test.sh ] && bash test.sh
[ -f .test_private.sh ] && bash .test_private.sh
"""

INIT_HIDDEN_SH = """#!/bin/bash
# Private Initialisierung (Phase 2): hier nichts zu tun — das private
# Testmaterial liegt statisch in .tests/.
exit 0
"""

SOLUTION_PY = '''"""Musterlösung: MLP auf dem synthetischen 3-Klassen-Dataset.

Selbstständig lauffähig: python3 .solution/solution.py
(schreibt results.json + loss.png nach /workspace)
"""
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
import torch.nn as nn

DATASET_PATH = "/workspace/data/dataset.csv"


def load_dataset():
    df = pd.read_csv(DATASET_PATH)
    feature_cols = [c for c in df.columns if c.startswith("x")]
    X = df[feature_cols].to_numpy(dtype=float)
    y = df["y"].to_numpy(dtype=int)
    return X, y


class MLP(nn.Module):
    def __init__(self, in_dim, hidden=64, out_dim=3):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden), nn.ReLU(),
            nn.Linear(hidden, hidden), nn.ReLU(),
            nn.Linear(hidden, out_dim),
        )

    def forward(self, x):
        return self.net(x)


def main():
    torch.manual_seed(0)
    X, y = load_dataset()

    # 80/20 Split
    rng = np.random.default_rng(0)
    idx = rng.permutation(len(y))
    split = int(0.8 * len(y))
    tr, te = idx[:split], idx[split:]

    # Standardisierung (Parameter nur vom Train-Teil)
    mu, sigma = X[tr].mean(0), X[tr].std(0)
    Xs = (X - mu) / (sigma + 1e-8)

    Xtr, ytr = torch.tensor(Xs[tr], dtype=torch.float32), torch.tensor(y[tr], dtype=torch.long)
    Xte, yte = torch.tensor(Xs[te], dtype=torch.float32), torch.tensor(y[te], dtype=torch.long)

    model = MLP(X.shape[1])
    opt = torch.optim.Adam(model.parameters(), lr=1e-2)
    loss_fn = nn.CrossEntropyLoss()

    history = []
    model.train()
    for epoch in range(60):
        perm = torch.randperm(len(Xtr))
        total, n_batch = 0.0, 0
        for i in range(0, len(Xtr), 64):
            b = perm[i:i + 64]
            opt.zero_grad()
            loss = loss_fn(model(Xtr[b]), ytr[b])
            loss.backward()
            opt.step()
            total += loss.item()
            n_batch += 1
        history.append((epoch + 1, total / max(n_batch, 1)))
        if (epoch + 1) % 10 == 0:
            print(f"Epoch {epoch + 1:3d}  loss={history[-1][1]:.4f}")

    model.eval()
    with torch.no_grad():
        train_acc = (model(Xtr).argmax(1) == ytr).float().mean().item()
        test_acc = (model(Xte).argmax(1) == yte).float().mean().item()
    final_loss = history[-1][1]
    print(f"Train-Accuracy: {train_acc:.4f}  Test-Accuracy: {test_acc:.4f}")

    with open("results.json", "w", encoding="utf-8") as f:
        json.dump({
            "accuracy": round(test_acc, 4),
            "train_accuracy": round(train_acc, 4),
            "epochs": len(history),
            "final_loss": round(final_loss, 6),
        }, f, indent=2)

    plt.figure(figsize=(8, 4.5))
    plt.plot([h[0] for h in history], [h[1] for h in history])
    plt.xlabel("Epoch")
    plt.ylabel("Loss")
    plt.title("Training Loss")
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig("loss.png", dpi=100)
    print("Artefakte geschrieben: results.json, loss.png")


if __name__ == "__main__":
    main()
'''

DESCRIPTION = """**Ziel:** Trainiere einen kleinen neuronalen Klassifikator (MLP) auf einem synthetischen 3-Klassen-Dataset.

**Aufbau deiner Umgebung:**
- `/workspace` — deine Arbeitsdateien (editierbar): `main.py`, `train.py`
- `/workspace/data/dataset.csv` — das Dataset (read-only): 1500 Samples, 20 Features (`x1`–`x20`), 3 Klassen (`y` ∈ {0,1,2})
- In `/workspace` liegen die installierten Pakete (numpy, pandas, torch, sklearn, matplotlib, pytest).

**Deine Aufgabe:**
1. Fülle die TODOs in `train.py` (`forward()` + `train()`).
2. Ergänze `main.py`: Daten vorbereiten (Split + Standardisierung), Modell trainieren, evaluieren.
3. Schreibe nach dem Training nach `/workspace`:
   - `results.json` mit den Keys `accuracy` (Test), `train_accuracy`, `epochs`, `final_loss`
   - `loss.png` — Loss-Verlauf über die Epochen (matplotlib)

**Testen:** Klicke auf ▶ Ausführen (führt `run.sh` = `python3 main.py` aus). Die Ausgabe erscheint im Terminal unten; fertige Artefakte (`.json`, `.png`) erscheinen im Dateibaum links (Bilder lassen sich dort per Klick direkt ansehen). Mit 🧪 Test (öffentliche Tests) kannst du die Struktur deiner Artefakte prüfen, bevor du abgibst.

**Abgabe:** Führe `python3 main.py` noch einmal aus, damit `results.json` und `loss.png` aktuell sind, und reiche dann ab. Die Bewertung führt die privaten Tests aus: Vorhandensein und Struktur von `results.json`, **Test-Accuracy ≥ 0.90**, und ein gültiges `loss.png`.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("course_id", type=int, help="Ziel-Kurs (id aus /courses)")
    parser.add_argument("--title", default="Workspace: Kleines MLP auf 3-Klassen-Dataset",
                        help="Aufgabentitel")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nur die Datei-Inhalte auf Disk schreiben, keine DB/Agent-Logik")
    args = parser.parse_args()

    create_db_and_tables()

    files = {
        "main.py": MAIN_PY,
        "train.py": TRAIN_PY,
        "run.sh": RUN_SH,
        "data/dataset.csv": generate_dataset_csv(),
        "test.py": PUBLIC_TESTS_PY,
        "test.sh": PUBLIC_TESTS_SH,
        ".tests/test_solution.py": TESTS_PY,
        ".solution/solution.py": SOLUTION_PY,
        ".test_private.sh": PRIVATE_TESTS_SH,
        ".run_solution.sh": RUN_SOLUTION_SH,
        ".init_hidden.sh": INIT_HIDDEN_SH,
    }
    # Zugriffsklassen: Ordner (Erbung auf alle Dateien darunter) +
    # test.py (System-Skripte wie run.sh/test.sh bekommen ihre Klasse
    # automatisch per system_file_access)
    file_access = {"test.py": "readonly"}
    folder_access = {
        "data": "readonly",
        ".tests": "hidden",
        ".solution": "hidden",
    }

    if args.dry_run:
        # Nur lokal ausgeben, DB/Task bleiben unangetastet
        from services.workspace_service import task_workspace_dir
        # dry-run nutzt die Task-ID 0 als Scratch-Verzeichnis
        base = task_workspace_dir(0)
        for path, content in files.items():
            p = base / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            print(f"[dry-run] {path} ({len(content)} Bytes) → {p}")
        print("[dry-run] Fertig. DB/Task wurden NICHT angelegt.")
        return 0

    with Session(engine) as session:
        course = session.get(Course, args.course_id)
        if course is None:
            print(f"Kurs {args.course_id} existiert nicht.", file=sys.stderr)
            return 1

        task = Task(
            course_id=course.id,
            title=args.title,
            task_type=TaskType.WORKSPACE,
            description=DESCRIPTION,
            max_points=10,
            created_by=course.created_by,
            display_order=999,
            workspace_timeout=900,
            workspace_cpu=2.0,
            workspace_memory="4g",
            workspace_internet=False,
            workspace_image="python-ml",
            workspace_engines='["local"]',
            workspace_main_file="main.py",
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        print(f"Task {task.id} angelegt: {task.title!r}")

        for path, content in files.items():
            workspace_service.save_task_file(
                session, task, path, content.encode("utf-8"))
            print(f"  + {path}  ({len(content)} Bytes)")
        for path, acc in file_access.items():
            workspace_service.set_file_access(session, task, path, acc)
            print(f"  {path} → {acc}")
        for path, acc in folder_access.items():
            workspace_service.set_folder_access(session, task, path, acc)
            print(f"  📁 {path} → {acc}")

        # Image + Assets + Init-Build zu den Agenten synchronisieren (best effort)
        try:
            result = workspace_service.on_task_saved(session, task)
            for a in result.get("assets", []):
                print(
                    f"  {a.get('agent')}: "
                    f"image={a.get('image', {}).get('status', '?')} "
                    f"assets={a.get('assets', {}).get('status', '?')} "
                    f"task_image={a.get('task_image', {}).get('status', '?')}")
        except Exception as e:  # noqa: BLE001
            print(f"  Hinweis: Asset-Sync fehlgeschlagen ({e}). "
                  f"Agent offline? Später in der Tutor-UI: 🔄 Sync.",
                  file=sys.stderr)

        status = json.loads(task.workspace_assets_status or "{}")
        print(f"  workspace_assets_status: {json.dumps(status, ensure_ascii=False)}")
        print(f"\nFertig. Task-ID: {task.id}")
        print("Testen: Tutor-UI → Aufgabe öffnen (Workspace-Ansicht), "
              "danach Student-Ansicht der Aufgabe (as_student).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
