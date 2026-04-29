from __future__ import annotations

import socket
from collections.abc import Iterable


class EventSocketError(RuntimeError):
    pass


class EventSocketClient:
    """Small inbound ESL client for FreeSWITCH api/bgapi commands."""

    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0) -> None:
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout

    def api(self, command: str) -> str:
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.settimeout(self.timeout)
            greeting = self._read_event(sock)
            if "auth/request" not in greeting.lower():
                raise EventSocketError(f"Unexpected FreeSWITCH greeting: {greeting[:120]}")

            self._send(sock, f"auth {self.password}\n\n")
            auth_reply = self._read_event(sock)
            if "+OK accepted" not in auth_reply:
                raise EventSocketError("FreeSWITCH event socket authentication failed")

            self._send(sock, f"api {command}\n\n")
            return self._read_event(sock)

    def bgapi(self, command: str) -> str:
        return self.api(f"bgapi {command}")

    def events(self, event_names: Iterable[str]):
        """Yield FreeSWITCH events from a long-lived inbound ESL connection."""
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.settimeout(self.timeout)
            greeting = self._read_event(sock)
            if "auth/request" not in greeting.lower():
                raise EventSocketError(f"Unexpected FreeSWITCH greeting: {greeting[:120]}")

            self._send(sock, f"auth {self.password}\n\n")
            auth_reply = self._read_event(sock)
            if "+OK accepted" not in auth_reply:
                raise EventSocketError("FreeSWITCH event socket authentication failed")

            self._send(sock, f"event plain {' '.join(event_names)}\n\n")
            reply = self._read_event(sock)
            if "+OK" not in reply:
                raise EventSocketError(f"FreeSWITCH event subscription failed: {reply[:120]}")

            sock.settimeout(None)
            while True:
                event = self._read_event(sock)
                if event:
                    yield event

    @staticmethod
    def _send(sock: socket.socket, payload: str) -> None:
        sock.sendall(payload.encode("utf-8"))

    def _read_event(self, sock: socket.socket) -> str:
        headers = b""
        while b"\n\n" not in headers:
            chunk = sock.recv(1)
            if not chunk:
                break
            headers += chunk

        text_headers = headers.decode("utf-8", errors="replace")
        content_length = 0
        for line in text_headers.splitlines():
            if line.lower().startswith("content-length:"):
                content_length = int(line.split(":", 1)[1].strip())
                break

        body = b""
        while len(body) < content_length:
            chunk = sock.recv(content_length - len(body))
            if not chunk:
                break
            body += chunk

        return text_headers + body.decode("utf-8", errors="replace")
