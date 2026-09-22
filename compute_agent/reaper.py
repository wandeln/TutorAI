"""
Reaper: Container nach Idle-Timeout entfernen (Volume bleibt erhalten).

Registry (in-memory): key → {spec, last_activity}. Bei Agent-Neustart wird
die Registry aus den Docker-Labels rekonstruiert (Spec kommt per
idempotentem POST /workspaces nachgereicht). Workspaces mit aktivem
Run-Job sind immun (kein Kill mitten im Training).
"""

import threading
import time

from . import config, docker_ops, runs


class WorkspaceRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}

    def register(self, key: str, spec: dict) -> None:
        with self._lock:
            self._items[key] = {"spec": spec, "last_activity": time.time()}

    def touch(self, key: str) -> None:
        with self._lock:
            if key in self._items:
                self._items[key]["last_activity"] = time.time()

    def get(self, key: str) -> dict | None:
        with self._lock:
            item = self._items.get(key)
            return dict(item) if item else None

    def remove(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)

    def all_items(self) -> list[tuple[str, dict]]:
        """Alle Einträge als (key, info)-Paare (Kopien — Mutation geht
        nur über die Registry-Methoden)."""
        with self._lock:
            return [(k, dict(v)) for k, v in self._items.items()]

    def set_disk(self, key: str, disk: dict | None, over: bool) -> None:
        """Disk-Quota-Zustand je Workspace (Cache für UI + exec-Blockade).

        `all_items()`/`get()` liefern Kopien — Mutationen gehen nur so.
        """
        with self._lock:
            if key in self._items:
                self._items[key]["disk"] = disk
                self._items[key]["over_quota"] = over

    def rebuild_from_docker(self) -> int:
        """Nach Neustart: bekannte Container mit Grace-Periode auffüllen."""
        n = 0
        try:
            for ws in docker_ops.list_workspaces():
                with self._lock:
                    if ws["key"] not in self._items:
                        self._items[ws["key"]] = {
                            "spec": None,  # kommt via POST /workspaces
                            "last_activity": time.time(),  # volle Idle-Periode
                        }
                        n += 1
        except Exception:
            pass
        return n


REGISTRY = WorkspaceRegistry()


def _sweep() -> None:
    """Ein Reaper-Durchlauf: Disk-Quota prüfen + idle Container stoppen."""
    now = time.time()
    active = runs.active_keys()
    for key, info in REGISTRY.all_items():
        _check_disk_quota(key, info)
        if key in active:
            continue
        if now - info["last_activity"] <= config.IDLE_TIMEOUT:
            continue
        try:
            docker_ops.stop_container_only(key)
            REGISTRY.touch(key)  # Container weg → kein erneuter Kill nötig
        except Exception:
            pass


def _check_disk_quota(key: str, info: dict) -> None:
    """Soft-Disk-Quota: Volume-Usage messen (🔒-ro-Mounts ausgenommen).

    Beim Übergang in die Überschreitung werden alle aktiven Runs gestoppt;
    neue Runs blockiert main.exec_cmd per `over_quota`. Fehlende Quota
    (None/0) = kein Limit. Fehler bleiben stumm (best effort).
    """
    try:
        spec = info.get("spec") or {}
        quota_mb = spec.get("disk_quota_mb")
        if not quota_mb:
            REGISTRY.set_disk(key, None, False)
            return
        if docker_ops.container_state(key) != "running":
            return  # letzter gemessener Wert bleibt erhalten
        usage = docker_ops.workspace_disk_usage(
            key, spec.get("readonly_paths") or [])
        over = usage > quota_mb * 1024 * 1024
        REGISTRY.set_disk(key, {"usage": usage, "quota_mb": quota_mb,
                                "over": over, "at": time.time()}, over)
        if over and not info.get("over_quota"):
            _stop_runs_for_quota(key, usage, quota_mb)
    except Exception:
        pass


def _stop_runs_for_quota(key: str, usage: int, quota_mb: int) -> None:
    """Alle aktiven Runs eines Workspaces stoppen (Quota-Überschreitung)."""
    usage_mb = usage // (1024 * 1024)
    for rid in runs.active_run_ids(key):
        job = runs.get_run(key, rid)
        if job is not None:
            with job._lock:
                if job.status in ("queued", "running"):
                    job.stderr.append(
                        f"[agent] Gestoppt: Speicherlimit erreicht "
                        f"({usage_mb} MB von {quota_mb} MB).")
        runs.stop_run(key, rid)


def reaper_loop(stop_event: threading.Event) -> None:
    """Background-Loop (alle 30 s), läuft in einem eigenen Thread."""
    last_prune = 0.0
    while not stop_event.is_set():
        try:
            _sweep()
            runs.maybe_garbage_collect()
            docker_ops.cull_deadline_images()  # Task-Images nach Deadline
            now = time.time()
            if now - last_prune >= 3600:  # stündlich, moderat (s. Helper)
                last_prune = now
                docker_ops.prune_build_artifacts()  # Build-Cache + Dangling
        except Exception:
            pass
        stop_event.wait(30)
