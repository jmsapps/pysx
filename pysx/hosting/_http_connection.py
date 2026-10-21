"""Shared websocket HTTP parser, imported only by active host adapters."""

from __future__ import annotations

from websockets.asyncio.server import ServerConnection


class HTTPMethodConnection(ServerConnection):
    """Preserve HTTP methods before the WebSocket library's GET-only parser."""

    method: str = "GET"
    first_line: bytes = b""
    reading_line: bool = True

    def data_received(self, data: bytes) -> None:
        if not self.reading_line:
            super().data_received(data)

            return
        self.first_line += data

        if len(self.first_line) > 8192 and b"\r\n" not in self.first_line:
            self.transport.close()

            return

        if b"\r\n" not in self.first_line:
            return
        method, separator, rest = self.first_line.partition(b" ")
        self.method = method.decode("ascii", errors="replace")
        self.reading_line = False
        self.first_line = b""
        super().data_received(b"GET" + separator + rest)
