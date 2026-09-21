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

    def items(self) -> dict[str, dict]:
        with self._lock:
            return {k: dict(v) for k, v in self._items.items()}

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
    """Ein Reaper-Durchlauf: idle Container stoppen (Volume bleibt)."""
    now = time.time()
    active = runs.active_keys()
    for key, info in REGISTRY.items():
        if key in active:
            continue
        if now - info["last_activity"] <= config.IDLE_TIMEOUT:
            continue
        try:
            docker_ops.stop_container_only(key)
            REGISTRY.touch(key)  # Container weg → kein erneuter Kill nötig
        except Exception:
            pass


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
