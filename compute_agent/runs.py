"""
Asynchrone Run-Jobs (langlaufende Trainings etc.).

Jeder Job: docker-exec-Prozess im Hintergrund-Thread, Log-Tail in
Speicher (letzte N Zeilen je Stream). Stop/Timeout killen zuerst den
Prozessbaum IM Container (PID-Datei + /proc-Walk) und dann den
docker-exec-Client — Client-kill allein würde die Container-Prozesse
verorphanen (ohne TTY räumt der Daemon sie nicht auf). GPU-Jobs gehen
durch eine Semaphore (FIFO-Queue, max. GPU_MAX_JOBS parallel — die GPU
soll bewusst nicht voll ausgelastet werden).

Die Jobs leben in-memory; nach Agent-Neustart sind laufende Jobs verloren
(TutorAI zeigt dann „Lauf nicht mehr verfügbar" — Workspace/State bleibt
im Volume erhalten).
"""

import os
import shlex
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from . import config, docker_ops


@dataclass
class RunJob:
    run_id: str
    key: str
    command: str
    working_dir: str
    timeout: int
    gpu: bool
    proc: subprocess.Popen | None = None
    stdout: deque = field(default_factory=lambda: deque(maxlen=config.LOG_TAIL_LINES))
    stderr: deque = field(default_factory=lambda: deque(maxlen=config.LOG_TAIL_LINES))
    status: str = "queued"          # queued | running | done | timeout | killed
    exit_code: int | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _gpu_sem: threading.Semaphore | None = None

    def to_status(self) -> dict:
        with self._lock:
            out = {
                "run_id": self.run_id,
                "status": self.status,
                "exit_code": self.exit_code,
                "stdout": list(self.stdout),
                "stderr": list(self.stderr),
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "duration": (self.finished_at or time.time()) - self.started_at,
            }
        # GPU-Queue: wie viele GPU-Jobs stehen vor mir? (FIFO, s. Plan §10)
        if self.gpu and out["status"] == "queued":
            out["queue_position"] = queue_position_ahead(self)
        return out


RUNS: dict[str, RunJob] = {}
RUNS_LOCK = threading.Lock()
GPU_SEM = threading.Semaphore(config.GPU_MAX_JOBS)


def list_runs(key: str) -> list[dict]:
    """Runs eines Workspaces, neueste zuerst (max. 50)."""
    with RUNS_LOCK:
        jobs = [j for j in RUNS.values() if j.key == key]
    jobs.sort(key=lambda j: j.started_at, reverse=True)
    return [j.to_status() for j in jobs[:50]]


def queue_position_ahead(job: RunJob) -> int:
    """Anzahl der GPU-Jobs vor `job` in der Queue (laufende + früher gestartete)."""
    with RUNS_LOCK:
        return sum(
            1 for j in RUNS.values()
            if j is not job and j.gpu
            and j.status in ("queued", "running")
            and (j.started_at < job.started_at
                 or (j.started_at == job.started_at and j.run_id < job.run_id))
        )


def get_run(key: str, run_id: str) -> RunJob | None:
    with RUNS_LOCK:
        job = RUNS.get(run_id)
    if job and job.key == key:
        return job
    return None


def active_keys() -> set[str]:
    """Workspaces mit aktivem Job (Reaper-Immunität)."""
    with RUNS_LOCK:
        return {j.key for j in RUNS.values() if j.status in ("queued", "running")}


# ── In-Container-Kill ─────────────────────────────────────────────
# docker exec -i (ohne TTY): stirbt der Client, überlebt der
# Container-Prozess, bis er selbst auf stdout schreibt (SIGPIPE) —
# z. B. ein Trainingsloop läuft sonst ewig weiter (CPU-Leak je
# gestopptem Run). → Beim Start notiert der exec-Prozess seine PID in
# eine PID-Datei; Stop/Timeout killen den kompletten Prozessbaum im
# Container per /proc-Walk (kein procps/setsid nötig).
_TREE_KILL = (
    'kt(){ r=$1; for d in /proc/[0-9]*; do p=${d#/proc/}; '
    '[ "$p" = "$r" ] && continue; s=$(cat "$d/stat" 2>/dev/null) || continue; '
    's=${s##*) }; set -- $s; [ "$2" = "$r" ] && kt $p; done; '
    'kill -9 "$r" 2>/dev/null; }'
)


def _pidfile(run_id: str) -> str:
    return f"/tmp/.tutorai_run_{run_id}.pid"


def _kill_in_container(job: RunJob) -> None:
    """Prozessbaum des Runs im Container killen (best effort)."""
    pidfile = _pidfile(job.run_id)
    cmd = (f"{_TREE_KILL}; pid=$(cat {pidfile} 2>/dev/null); "
           f"rm -f {pidfile}; [ -n \"$pid\" ] && kt \"$pid\"")
    try:
        docker_ops._docker("exec", docker_ops.container_name(job.key),
                           "sh", "-c", cmd, timeout=30, check=False)
    except Exception:
        pass  # Container weg/Daemon-Problem → Client-Kill greift noch


def _drain(pipe, lines: deque, lock: threading.Lock) -> None:
    buf = ""
    try:
        while True:
            line = pipe.readline()
            if not line:
                break
            buf += line.decode(errors="replace")
            if len(buf) > 4096:
                buf = buf[-4096:]
            for part in buf.split("\n"):
                if part:
                    with lock:
                        lines.append(part)
            buf = buf.rsplit("\n", 1)[-1]
    except Exception:
        pass


def _watchdog(job: RunJob) -> None:
    """Wartet auf den Prozess; Timeout → Kill."""
    proc = job.proc
    assert proc is not None
    timed_out = False
    try:
        proc.wait(timeout=job.timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_in_container(job)
        try:
            os.killpg(proc.pid, 9)
        except (ProcessLookupError, PermissionError, OSError):
            proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    with job._lock:
        job.status = "timeout" if timed_out else "done"
        job.exit_code = None if timed_out else proc.returncode
        job.finished_at = time.time()
    if job._gpu_sem is not None:
        job._gpu_sem.release()
    job._gpu_sem = None


def start_run(key: str, command: str, working_dir: str = "/workspace",
              timeout: int | None = None, gpu: bool = False) -> RunJob:
    """Job starten (GPU-Jobs warten ggf. auf die Semaphore)."""
    docker_ops._ensure_running(key)
    run_id = uuid.uuid4().hex
    job = RunJob(
        run_id=run_id, key=key, command=command, working_dir=working_dir,
        timeout=min(int(timeout or config.DEFAULT_TIMEOUT), config.MAX_TIMEOUT),
        gpu=gpu,
    )
    with RUNS_LOCK:
        RUNS[run_id] = job

    def _worker():
        try:
            if gpu:
                job._gpu_sem = GPU_SEM
                GPU_SEM.acquire()
                with job._lock:
                    job.status = "running"
            # PID-Datei: der exec-Prozess (sh) notiert seine eigene PID →
            # Stop/Timeout können den kompletten Baum IM Container killen.
            wrapped = f"echo $$ > {_pidfile(run_id)}; {job.command}"
            cmd = ["docker", "exec", "-i", "-w", job.working_dir,
                   docker_ops.container_name(key), "sh", "-c", wrapped]
            job.proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, start_new_session=True,
            )
            threading.Thread(target=_drain, args=(job.proc.stdout, job.stdout, job._lock),
                             daemon=True).start()
            threading.Thread(target=_drain, args=(job.proc.stderr, job.stderr, job._lock),
                             daemon=True).start()
            with job._lock:
                job.status = "running"
            _watchdog(job)
        except Exception as e:  # noqa: BLE001 — Job-Thread darf nie sterben
            with job._lock:
                if job.status in ("queued", "running"):
                    job.status = "killed"
                    job.stderr.append(f"[agent] {e}")
                    job.finished_at = time.time()
            if job._gpu_sem is not None:
                job._gpu_sem.release()
                job._gpu_sem = None

    threading.Thread(target=_worker, daemon=True).start()
    return job


def stop_run(key: str, run_id: str) -> bool:
    """Laufenden Job killen (Prozessbaum im Container + exec-Client)."""
    job = get_run(key, run_id)
    if job is None or job.proc is None:
        return False
    if job.status not in ("queued", "running"):
        return False
    _kill_in_container(job)
    try:
        os.killpg(job.proc.pid, 9)
    except (ProcessLookupError, PermissionError, OSError):
        job.proc.kill()
    with job._lock:
        if job.status in ("queued", "running"):
            job.status = "killed"
    return True


def maybe_garbage_collect(max_age: float = 3600 * 6) -> None:
    """Fertige Jobs nach 6 h aus dem Speicher entfernen (Logs bleiben in DB)."""
    now = time.time()
    with RUNS_LOCK:
        dead = [rid for rid, j in RUNS.items()
                if j.status not in ("queued", "running")
                and (now - (j.finished_at or now)) > max_age]
        for rid in dead:
            del RUNS[rid]
