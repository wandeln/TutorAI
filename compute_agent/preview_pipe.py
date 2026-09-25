"""
Roh-HTTP-Primitives für die Preview-Proxys (Agent + Backend).

Browser ⇄ Backend-Proxy (ASGI, Backend-Haupt-Port) ⇄ Agent-Proxy
         (TCP :8701) ⇄ docker-exec-Relay (Pipes) ⇄ Ziel-Server im
         Container

Der Agent-Proxy tunnelt rohes HTTP/1.1 (inkl. 101-WS-Upgrade) durch
eine TCP-Verbindung: Uvicorn/ASGI können kein HTTP→WS-Upgrade
weiterreichen (WS-Requests werden in den ASGI-WebSocket-Fluss
umgewandelt), daher läuft er als roher asyncio-Server. Der
Backend-Proxy ist dagegen eine ASGI-Brücke auf dem Backend-Haupt-Port
(same-origin → kein Mixed Content hinter HTTPS-Reverse-Proxy) mit
eigenem RFC-6455-Frame-Codec für die Agent-Leiste (s. preview_proxy.py).
Geteilt werden: Head-Parsing, Hop-by-Hop-Filter, Error-Antworten und
das bidirektionale Pumpen.
"""

import asyncio


class HeadError(Exception):
    """Head unvollständig, zu groß oder defekt."""


# WICHTIG: bytes! Header-Namen sind überall bytes (s. parse_*_head) —
# ein str-Frozenset würde nie matchen und Hop-by-Hop-Header (u. a.
# connection) ungefiltert durchlassen (→ doppelte Connection-Header,
# Upgrade-Erkennung tot, App hält Connection offen → Relay-Leaks).
HOP_BY_HOP = frozenset({
    b"connection", b"keep-alive", b"proxy-authenticate",
    b"proxy-authorization", b"te", b"trailer", b"transfer-encoding",
    b"upgrade",
})


def parse_request_head(head: bytes) -> tuple[str, str, list[tuple[bytes, bytes]]]:
    """Request-Head → (method, target, headers). Target = Pfad+Query (raw,
    percent-encoding bleibt erhalten). Header-Namen klein, Werte gestrippt."""
    try:
        line, _, rest = head.partition(b"\r\n")
        parts = line.decode("latin-1").split(" ")
        if len(parts) != 3 or not parts[2].startswith("HTTP/"):
            raise ValueError
        method, target = parts[0], parts[1]
    except Exception:
        raise HeadError("Ungültiger Request-Head") from None
    headers: list[tuple[bytes, bytes]] = []
    for line in rest.split(b"\r\n"):
        if not line:
            continue
        name, sep, value = line.partition(b":")
        if not sep:
            continue
        headers.append((name.strip().lower(), value.strip()))
    return method.upper(), target, headers


def parse_response_head(head: bytes) -> tuple[int, list[tuple[bytes, bytes]]]:
    """Response-Head → (status, headers)."""
    try:
        line, _, rest = head.partition(b"\r\n")
        parts = line.decode("latin-1").split(" ", 2)
        if len(parts) < 2 or not parts[0].startswith("HTTP/"):
            raise ValueError
        status = int(parts[1])
    except Exception:
        raise HeadError("Ungültiger Response-Head") from None
    headers: list[tuple[bytes, bytes]] = []
    for line in rest.split(b"\r\n"):
        if not line:
            continue
        name, sep, value = line.partition(b":")
        if not sep:
            continue
        headers.append((name.strip().lower(), value.strip()))
    return status, headers


def filter_hop_by_hop(headers):
    """Hop-by-Hop-Header trennen.

    Liefert (rest, upgrade_wanted, upgrade_value): `upgrade_wanted` ist
    True, wenn die Connection-Header `upgrade` enthielt (dann wird der
    Upgrade im Upstream-Request beibehalten).
    """
    out: list[tuple[bytes, bytes]] = []
    upgrade_wanted = False
    upgrade_value = b""
    for name, value in headers:
        if name in HOP_BY_HOP:
            # WICHTIG: bytes-Vergleich (Name kommt von parse_*_head als bytes)
            if name == b"connection":
                tokens = [t.lower().strip() for t in value.split(b",")]
                upgrade_wanted = b"upgrade" in tokens
            elif name == b"upgrade" and value:
                upgrade_value = value
            continue
        out.append((name, value))
    return out, upgrade_wanted, upgrade_value


def build_request_head(method: str, target: str,
                       headers: list[tuple[bytes, bytes]],
                       extra: list[tuple[bytes, bytes]] | None = None) -> bytes:
    """Upstream-Request-Head bauen (Hop-by-Hop ersetzt durch
    `connection: close` bzw. `connection: upgrade`)."""
    rest, upgrade, upgrade_value = filter_hop_by_hop(headers)
    lines = [f"{method} {target} HTTP/1.1".encode("latin-1")]
    for name, value in rest:
        lines.append(name + b": " + value)
    for name, value in extra or []:
        lines.append(name + b": " + value)
    if upgrade:
        lines.append(b"upgrade: " + upgrade_value)
        lines.append(b"connection: upgrade")
    else:
        lines.append(b"connection: close")
    return b"\r\n".join(lines) + b"\r\n\r\n"


def build_response_head(status: int,
                        headers: list[tuple[bytes, bytes]]) -> bytes:
    """Response-Head fürs Gegenüber: Connection-Header normalisieren
    (101: upgrade beibehalten; sonst connection: close)."""
    lines = [f"HTTP/1.1 {status}".encode("latin-1")]
    for name, value in headers:
        if name in (b"connection", b"keep-alive"):
            continue
        lines.append(name + b": " + value)
    if status == 101:
        lines.append(b"connection: upgrade")
    else:
        lines.append(b"connection: close")
    return b"\r\n".join(lines) + b"\r\n\r\n"


def build_error(status: int, message: str = "") -> bytes:
    """Klare Fehler-Antwort (Head + Body, connection: close)."""
    text = {
        400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
        404: "Not Found", 411: "Length Required", 413: "Payload Too Large",
        502: "Bad Gateway", 503: "Service Unavailable", 504: "Gateway Timeout",
    }.get(status, "Error")
    body = f"TutorAI-Preview: {text}"
    if message:
        body += f" — {message[:300]}"
    body_b = body.encode("utf-8", "replace")
    return (
        f"HTTP/1.1 {status} {text}\r\n"
        "content-type: text/plain; charset=utf-8\r\n"
        f"content-length: {len(body_b)}\r\n"
        "connection: close\r\n\r\n"
    ).encode("latin-1") + body_b


async def read_head(reader, cap: int = 65536) -> tuple[bytes, bytes]:
    """TCP-Head lesen bis \r\n\r\n. Liefert (head, leftover-Body-Bytes)."""
    buf = bytearray()
    while b"\r\n\r\n" not in buf:
        chunk = await reader.read(65536)
        if not chunk:
            raise HeadError("Verbindung vor Head-Ende geschlossen")
        buf.extend(chunk)
        if len(buf) > cap:
            raise HeadError("Head zu groß")
    end = buf.index(b"\r\n\r\n") + 4
    return bytes(buf[:end]), bytes(buf[end:])


async def _pump(read, write, timeout: float | None = None) -> None:
    """read() → bytes (b'' = EOF) bis write(bytes); stoppt bei
    EOF/Fehler/Idle-Timeout (Gegenstelle totstill ohne FIN/RST)."""
    while True:
        try:
            if timeout is not None:
                data = await asyncio.wait_for(read(), timeout)
            else:
                data = await read()
        except Exception:
            return
        if not data:
            return
        try:
            await write(data)
        except Exception:
            return


async def pump_pair(read_a, write_a, read_b, write_b,
                    idle_timeout: float | None = None) -> None:
    """Zwei Richtungen parallel pumpen; endet, wenn eine Richtung
    EOF/Fehler/Idle-Timeout erreicht (dann wird die andere abgebrochen).

    read_x: async, liefert bytes (b'' = EOF) · write_x: async(bytes).
    """
    t1 = asyncio.create_task(_pump(read_a, write_a, idle_timeout))
    t2 = asyncio.create_task(_pump(read_b, write_b, idle_timeout))
    await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
    for t in (t1, t2):
        t.cancel()
    for t in (t1, t2):
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass  # Abbruch ist der normale Endzustand


class _ChunkedFraming:
    """Erkennt das Ende eines chunked-Transfers.

    Die Bytes werden UNVERÄNDERT durchgereicht (Pass-through) — hier
    wird nur das Framing gezählt (Size-zeile, Chunk-Daten, CRLF,
    Trailer bis Leerzeile).
    """

    def __init__(self) -> None:
        self.state = "SIZE"
        self.chunk = 0
        self.buf = b""

    def feed(self, data: bytes) -> bool:
        """Neue Bytes einreichen; True = Transfer komplett (EOD)."""
        self.buf += data
        while True:
            if self.state == "SIZE":
                idx = self.buf.find(b"\r\n")
                if idx < 0:
                    if len(self.buf) > 32:
                        raise HeadError("Chunk-Size defekt")
                    break
                line = self.buf[:idx].decode("latin-1")
                try:
                    self.chunk = int(line.split(";", 1)[0].strip() or "0", 16)
                except ValueError:
                    raise HeadError("Chunk-Size defekt") from None
                self.buf = self.buf[idx + 2:]
                self.state = "BODY" if self.chunk else "TRAIL"
            elif self.state == "BODY":
                n = min(self.chunk, len(self.buf))
                self.buf = self.buf[n:]  # bytes: kein Slice-Del möglich
                self.chunk -= n
                if self.chunk == 0:
                    self.state = "BODYCRLF"
            elif self.state == "BODYCRLF":
                if len(self.buf) < 2:
                    break
                self.buf = self.buf[2:]
                self.state = "SIZE"
            else:  # TRAIL: Trailer enden mit Leerzeile
                idx = self.buf.find(b"\r\n")
                if idx < 0:
                    if len(self.buf) > 8192:
                        raise HeadError("Chunk-Trailers zu groß")
                    break
                self.buf = self.buf[idx + 2:]
                return True
        return False


class ResponseBody:
    """Verfolgt das Ende des Response-Body (nur Zählen, kein Dekodieren).

    Modus: none (kein Body) | length (Content-Length) | chunked
    (Transfer-Encoding) | close (Body bis Pipe-EOF).
    """

    NONE = "none"
    LENGTH = "length"
    CHUNKED = "chunked"
    CLOSE = "close"

    def __init__(self, status: int, headers: list[tuple[bytes, bytes]],
                 head_method: bool = False):
        self._chunked: _ChunkedFraming | None = None
        self._remaining = 0
        lower = {name: value for name, value in headers}
        te = lower.get(b"transfer-encoding", b"")
        if head_method or status in (204, 304):
            self.mode = self.NONE
        elif b"chunked" in te:
            self._chunked = _ChunkedFraming()
            self.mode = self.CHUNKED
        elif b"content-length" in lower:
            try:
                self._remaining = int(lower[b"content-length"])
            except ValueError:
                self._remaining = 0
            self.mode = self.NONE if self._remaining <= 0 else self.LENGTH
        else:
            self.mode = self.CLOSE

    def feed(self, data: bytes) -> bool:
        """Durchgereichte Body-Bytes einreichen; True = EOD erreicht."""
        if not data:
            return False
        if self.mode == self.LENGTH:
            self._remaining -= len(data)
            return self._remaining <= 0
        if self.mode == self.CHUNKED:
            return self._chunked.feed(data)
        return False
