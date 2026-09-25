"""
Workspace-Preview-Server des Agents (raw asyncio, zweiter Agent-Port).

Warum kein FastAPI/ASGI: Uvicorn interceptet `Upgrade: websocket`-
Requests und wandelt sie in den ASGI-WebSocket-Fluss um — der für den
Relay-Tunnel nötige raw HTTP→101-Passthrough ist über ASGI nicht
möglich. Der Server ist daher ein eigener asyncio-Server auf
PREVIEW_PORT (nur Compose-Netz, KEIN Host-Publish; einziger Client ist
der Backend-Preview-Proxy, ASGI-Routes auf dem Backend-Haupt-Port,
s. services/preview_proxy.py).

Request (vom Backend):  <METHOD> /ws-preview/{key}/preview/{port}{target}
Auth: X-Agent-Token (HMAC, op = ws:{key}) — manuell via verify_token_raw.

Flow je Request:
  Container-Running-Check → Relay sicherstellen (docker cp) →
  docker exec -i /tmp/relay {port} → Upstream-Request (Hop-by-Hop aus)
  → Response pass-through (101: bidirektionales Pumpen; sonst Body-Pump
  mit Content-Length/Chunked-Ende-Erkennung).
"""

import asyncio
import fcntl
import logging
import os
import re
import signal
import subprocess
import threading

from . import auth, config, docker_ops
from .preview_pipe import (HeadError, ResponseBody, build_error,
                           build_request_head, build_response_head,
                           parse_request_head, parse_response_head,
                           pump_pair, read_head)
from .reaper import REGISTRY

logger = logging.getLogger("compute_agent")

# /ws-preview/{key}/preview/{port}{target} — target leer oder "/…"
_ROUTE_RE = re.compile(
    r"^/ws-preview/(?P<key>[^/]+)/preview/(?P<port>\d{1,5})(?P<target>/\S*)?$")

_HEAD_TIMEOUT = 60.0  # Antwort-Head des Ziels (Relay-Connect inkl.)


async def _write_all(writer, data: bytes) -> None:
    writer.write(data)
    await writer.drain()


class _FdStreamReader:
    """Pipes-Fd via loop.add_reader (nonblocking, Event-Loop-Thread).

    WICHTIG: Kein `run_in_executor(None, os.read, …)` — jede Langzeit-
    Connection (SSE/WS-Preview) würde damit einen Executor-Thread
    für immer blockieren. Der Default-Pool hat nur min(32, CPU+4)
    Threads; ein Jupyter-UI-Ladeburst (~15 parallele Relays) erschöpft
    ihn komplett → danach friert der GANZE Agent (Terminal-Input,
    neue Terminals, Relay-Spawns — alles Executor-basiert).
    add_reader hält 0 Threads pro Connection (selbes Muster wie der
    PTY-Read in terminal.py).
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, fd: int) -> None:
        self._loop = loop
        self._fd = fd
        self._buf = bytearray()
        self._waker = asyncio.Event()
        self._done = False
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        loop.add_reader(fd, self._on_readable)

    def _on_readable(self) -> None:
        try:
            while True:
                chunk = os.read(self._fd, 65536)
                if not chunk:
                    self._done = True
                    self._waker.set()
                    return
                self._buf.extend(chunk)
        except (BlockingIOError, InterruptedError):
            pass  # kein (weiteres) Daten mehr → Callback war's
        except OSError:
            self._done = True
            self._waker.set()
            return
        if self._buf:
            self._waker.set()

    async def read(self, n: int) -> bytes:
        """Bis zu n Bytes; b'' = EOF (Pipe geschlossen)."""
        while not self._buf:
            if self._done:
                return b""
            self._waker.clear()
            await self._waker.wait()
        n = min(n, len(self._buf))
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def close(self) -> None:
        try:
            self._loop.remove_reader(self._fd)
        except Exception:
            pass


class PreviewServer:
    """Raw-TCP-Server für den Preview-Tunnel (einzige Instanz: `server`)."""

    def __init__(self) -> None:
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, "0.0.0.0", config.PREVIEW_PORT)
        logger.info("Preview-Server lauscht auf Port %s (nur Agent-Netz)",
                    config.PREVIEW_PORT)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # ── Connection ────────────────────────────────────────────────

    async def _handle(self, reader, writer) -> None:
        try:
            await self._handle_conn(reader, writer)
        except Exception:
            logger.debug("Preview-Connection-Fehler", exc_info=True)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_conn(self, reader, writer) -> None:
        loop = asyncio.get_running_loop()
        try:
            head_b, leftover = await read_head(reader)
        except HeadError:
            await _write_all(writer, build_error(400))
            return
        try:
            method, target, headers = parse_request_head(head_b)
        except HeadError:
            await _write_all(writer, build_error(400))
            return

        m = _ROUTE_RE.match(target.split("?", 1)[0])
        if not m:
            await _write_all(writer, build_error(404))
            return
        key = m.group("key")
        port = int(m.group("port"))
        rest = m.group("target") or "/"
        if "?" in target:
            rest += "?" + target.split("?", 1)[1]

        try:
            docker_ops.key_parts(key)
        except docker_ops.DockerError:
            await _write_all(writer, build_error(404))
            return
        token = dict(headers).get(b"x-agent-token", b"").decode("latin-1")
        try:
            payload = auth.verify_token_raw(token)
        except ValueError:
            await _write_all(writer, build_error(401))
            return
        if payload.get("op") not in ("*", f"ws:{key}"):
            await _write_all(writer, build_error(403))
            return

        hdrs = dict(headers)
        if b"transfer-encoding" in hdrs:  # Chunked-Uploads → 411
            await _write_all(writer, build_error(411))
            return
        try:
            content_length = int(hdrs.get(b"content-length", b"0") or 0)
        except ValueError:
            await _write_all(writer, build_error(400))
            return

        # Container + Relay (sync docker-CLI → Executor)
        try:
            proc = await loop.run_in_executor(
                None, self._spawn_relay, key, port)
        except docker_ops.DockerError as e:
            await _write_all(writer, build_error(e.status))
            return
        if proc is None:
            await _write_all(writer, build_error(409))
            return

        err_buf = bytearray()

        def _drain_err() -> None:
            # stderr-Drain (Pipe sonst voll → Relay blockiert)
            try:
                while True:
                    chunk = os.read(proc.stderr.fileno(), 65536)
                    if not chunk:
                        break
                    if len(err_buf) < 16384:
                        err_buf.extend(chunk[:16384 - len(err_buf)])
            except Exception:
                pass
        threading.Thread(target=_drain_err, daemon=True).start()

        # Relay-STDOUT via add_reader (0 Executor-Threads pro Connection,
        # s. _FdStreamReader). Vor dem Request senden anlegen: frühe
        # Antwort-Bytes liegen sicher im Kernel-Pipe-Puffer.
        out = _FdStreamReader(loop, proc.stdout.fileno())

        try:
            # Upstream-Request an den Relay (→ Ziel im Container).
            # WICHTIG: flushen — Popen.stdin ist ein BufferedWriter;
            # ohne Flush bliebe der (kleine) Head im Puffer und der
            # Relay würde nie den Request sehen (→ Head-Timeout/504).
            async def _send_to_relay(data: bytes) -> None:
                def _write_flush() -> None:
                    proc.stdin.write(data)
                    proc.stdin.flush()
                await loop.run_in_executor(None, _write_flush)

            up_head = build_request_head(method, rest, headers)
            await _send_to_relay(up_head)
            if leftover:
                take = min(len(leftover), content_length)
                if take:
                    await _send_to_relay(leftover[:take])
                    content_length -= take
            while content_length > 0:
                chunk = await reader.read(min(65536, content_length))
                if not chunk:
                    break  # Client fertig vorzeitig → Best-Effort
                await _send_to_relay(chunk)
                content_length -= len(chunk)

            # Antwort-Head vom Relay (Ziel-Connect-Fehler → EOF → 502)
            head_buf = bytearray()
            try:
                while b"\r\n\r\n" not in head_buf:
                    chunk = await asyncio.wait_for(
                        out.read(65536), timeout=_HEAD_TIMEOUT)
                    if not chunk:
                        err_text = err_buf.decode(errors="replace").strip()
                        logger.warning("Preview %s Port %s ohne Head: %s",
                                       key, port, err_text[:300])
                        await _write_all(writer, build_error(
                            502, err_text or "Ziel-Server nicht erreichbar"))
                        return
                    head_buf.extend(chunk)
                    # Limit gilt für den ANSWER-HEAD — ein einzelnes Read
                    # kann aber bis zu 64 KB HEAD+BODY liefern (lokale
                    # Pipes liefern große Antworten oft in einem Stück).
                    # 128 KB = 2× Max-Read, damit nur ein wirklich riesiger
                    # Head (z. B. 100-KB-Cookies) abgebrochen wird.
                    if len(head_buf) > 131072:
                        await _write_all(writer, build_error(502))
                        return
            except asyncio.TimeoutError:
                await _write_all(writer, build_error(504))
                return
            end = head_buf.index(b"\r\n\r\n") + 4
            try:
                status, resp_headers = parse_response_head(bytes(head_buf[:end]))
            except HeadError:
                await _write_all(writer, build_error(502))
                return
            leftover = bytes(head_buf[end:])
            await _write_all(writer, build_response_head(status, resp_headers))

            if status == 101:
                # Upgrade (WebSocket o. Ä.): leftover kann schon Frame-
                # Bytes enthalten → zuerst weiterreichen, dann pumpen.
                if leftover:
                    await _write_all(writer, leftover)

                async def _w_client(data: bytes) -> None:
                    await _write_all(writer, data)

                await pump_pair(
                    lambda: reader.read(65536), _send_to_relay,
                    lambda: out.read(65536), _w_client)
            else:
                body = ResponseBody(status, resp_headers,
                                    head_method=(method == "HEAD"))
                eod = False
                if leftover:
                    await _write_all(writer, leftover)
                    eod = body.feed(leftover)
                while not eod:
                    chunk = await out.read(65536)
                    if not chunk:
                        break
                    await _write_all(writer, chunk)
                    eod = body.feed(chunk)
        finally:
            # Relay-Session beenden (neuer Prozess je Request — s. Plan)
            out.close()
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            try:
                proc.stdout.close()
            except Exception:
                pass

    # ── Relay-Spawn (sync, läuft im Executor) ─────────────────────

    def _spawn_relay(self, key: str, port: int) -> subprocess.Popen | None:
        """Container prüfen + Relay sicherstellen + Exec starten."""
        REGISTRY.touch(key)
        if docker_ops.container_state(key) != "running":
            docker_ops._ensure_running(key)  # fehlt → DockerError(409)
        docker_ops.ensure_relay(key)
        try:
            return subprocess.Popen(
                ["docker", "exec", "-i", docker_ops.container_name(key),
                 "/tmp/relay", str(port)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, start_new_session=True)
        except OSError as e:
            raise docker_ops.DockerError(
                f"Relay-Start fehlgeschlagen: {e}", 500) from e


server = PreviewServer()
