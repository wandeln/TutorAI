"""
Terminal-Sessions für Workspace-Container (PTY um `docker exec`).

TerminalSession: pty.openpty() + TIOCSWINSZ (Default 160×40) →
`docker exec -i -t … sh` im Container. Die docker-CLI erkennt die
PTY und legt das Container-TTY in Client-TTY-Größe an. Resizes
forwardet die CLI nur auf SIGWINCH — das Signal bekommt sie nie von
alleine (kein controlling TTY, start_new_session) → resize() macht
TIOCSWINSZ und feuert dann manuell SIGWINCH an die CLI.

Der Master-Fd ist nonblocking und hängt an loop.add_reader: Ausgabe
→ on_output(bytes) (Binary-WS-Frames). EIO am Master = Shell beendet
→ on_exit(code). Input/Resize laufen im Executor (os.write/ioctl).

Konstruktion MUSS im Event-Loop-Thread erfolgen (add_reader).

Orphan-Schutz: stirbt der docker-exec-Client, überlebt der Shell-Baum
IM Container (bekanntes Docker-Verhalten). Die Shell notiert deshalb
ihre PID in eine PID-Datei im Container-tmpfs; close() fällt den
Prozessbaum im Container per /proc-Walk, bevor die CLI gestoppt wird
(gleiches Muster wie runs.py).
"""

import asyncio
import fcntl
import os
import pty
import signal
import struct
import subprocess
import termios
import uuid

from . import docker_ops


class TerminalSession:
    def __init__(self, key: str, loop: asyncio.AbstractEventLoop,
                 on_output, on_exit) -> None:
        """on_output(data: bytes) / on_exit(code: int | None) — sync
        Callbacks; dürfen Tasks anlegen, blockieren aber nicht."""
        self._key = key
        self._sid = uuid.uuid4().hex[:12]
        self._loop = loop
        self._closed = False
        self._on_output = on_output
        self._on_exit = on_exit

        master, slave = pty.openpty()
        self._master = master
        fcntl.ioctl(master, termios.TIOCSWINSZ,
                    struct.pack("HHHH", 40, 160, 0, 0))
        # PID-Datei: $$ ist die exec'de sh; „exec <shell>“ behält die PID,
        # der interaktiven Shell gehört sie danach (der Close kann den
        # ganzen Baum damit fällen).
        # Vollwertige Session: bash, falls im Image vorhanden —
        # interaktives bash liefert Tab-Completion (Readline), expandiert
        # \u/\h/\w im Prompt und liest ~/.bashrc (HOME=/tmp, tmpfs).
        # PS1 wird im Wrapper gesetzt, weil die Shell-Art erst dort
        # bekannt ist: dash (Fallback) expandiert \w-Fluchtsequenzen
        # NICHT (wuerde wortlaechlich angezeigt) → dort statisches
        # Prompt. ${CONDA_DEFAULT_ENV:+…} zeigt eine aktive Conda-
        # Env im Prompt an (wird vom bash je Prompt neu ausgewertet).
        pidfile = f"/tmp/.tutorai_term_{self._sid}.pid"
        shell_cmd = (
            f"echo $$ > {pidfile}; "
            "if command -v bash >/dev/null 2>&1; then "
            r"PS1='\u@\h:\w${CONDA_DEFAULT_ENV:+($CONDA_DEFAULT_ENV)}\$ '; "
            "export PS1; exec bash; "
            r"else PS1='$ '; export PS1; exec sh; fi"
        )
        self._proc = subprocess.Popen(
            ["docker", "exec", "-i", "-t",
             "-e", "TERM=xterm-256color",
             "-w", "/workspace",
             docker_ops.container_name(key), "sh", "-c", shell_cmd],
            stdin=slave, stdout=slave, stderr=slave,
            start_new_session=True)
        os.close(slave)
        flags = fcntl.fcntl(master, fcntl.F_GETFL)
        fcntl.fcntl(master, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        loop.add_reader(master, self._on_readable)

    # ── add_reader-Callback (Event-Loop-Thread) ───────────────────

    def _on_readable(self) -> None:
        try:
            data = os.read(self._master, 65536)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b""
        if not data:  # EIO: Kind-Prozess (docker exec) beendet
            self.close()
            return
        try:
            self._on_output(data)
        except Exception:
            pass

    # ── Public API (async, Executor-basiert) ──────────────────────

    async def write_input(self, data: bytes) -> None:
        if self._closed or not data:
            return
        try:
            await self._loop.run_in_executor(
                None, os.write, self._master, data)
        except OSError:
            pass

    async def resize(self, cols: int, rows: int) -> None:
        if self._closed:
            return
        cols = max(2, min(int(cols), 500))
        rows = max(1, min(int(rows), 200))

        def _apply() -> None:
            fcntl.ioctl(self._master, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0))
            # Die CLI hat KEIN controlling TTY (start_new_session) → der
            # Kernel liefert nie SIGWINCH. Wir feuern es manuell NACH dem
            # TIOCSWINSZ: Der CLI-Handler liest dann die neue Größe von
            # ihrem stdin (Client-TTY) und forwardet sie an den Daemon
            # (exec-resize). Ohne diesen Schritt bleibt das Container-TTY
            # auf der Attach-Zeit-Größe → bash/readline rechnet mit anderer
            # Breite als xterm rendert → Wrap-/Cursor-Desync.
            if self._proc.poll() is None:
                try:
                    os.kill(self._proc.pid, signal.SIGWINCH)
                except (ProcessLookupError, PermissionError, OSError):
                    pass

        try:
            await self._loop.run_in_executor(None, _apply)
        except (OSError, ValueError):
            pass

    def _kill_tree_in_container(self) -> None:
        """Shell-Prozessbaum der Session im Container killen (best effort).

        Das Kommando liest die PID, räumt die Datei SOFORT ab und prüft
        zusätzlich /proc/<pid>/comm — ein wiederverwendeter PID (Shell
        ist normal raus + PID-Recycling, bevor der Close kam) trifft
        so keinen fremden Prozess mehr.
        """
        from .runs import _TREE_KILL
        pf = f"/tmp/.tutorai_term_{self._sid}.pid"
        cmd = (
            f"{_TREE_KILL}; pid=$(cat {pf} 2>/dev/null); rm -f {pf}; "
            "[ -n \"$pid\" ] && [ -d /proc/$pid ] && "
            'case $(tr -d "[:space:]" < /proc/$pid/comm 2>/dev/null) in '
            'sh|dash|bash|ash) kt "$pid";; esac'
        )
        try:
            docker_ops._docker("exec", docker_ops.container_name(self._key),
                               "sh", "-c", cmd, timeout=30, check=False)
        except Exception:
            pass  # Container weg/Daemon-Problem → CLI-Kill greift noch

    def close(self) -> None:
        """Idempotent: Reader abhaken, Shell-Baum im Container killen,
        PTY zu, CLI-Prozess killen, on_exit feuern (mit Exit-Code,
        falls bekannt)."""
        if self._closed:
            return
        self._closed = True
        try:
            self._loop.remove_reader(self._master)
        except Exception:
            pass
        # Shell-Baum IM Container erst fällen (Executor, non-blocking),
        # dann die CLI killen — umgekehrt würde der Baum verorphanen.
        try:
            self._loop.run_in_executor(None, self._kill_tree_in_container)
        except RuntimeError:
            pass  # Loop bereits beendet → nur CLI-Kill
        try:
            os.close(self._master)
        except OSError:
            pass
        if self._proc.poll() is None:
            try:
                os.killpg(self._proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                try:
                    self._proc.kill()
                except Exception:
                    pass
        code = self._proc.returncode
        if code is None:
            try:
                code = self._proc.wait(timeout=3)
            except Exception:
                code = None
        try:
            self._on_exit(code)
        except Exception:
            pass
