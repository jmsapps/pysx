"""Bounded protocol for the pinned optional Uvicorn host."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from uvicorn.protocols.websockets.websockets_sansio_impl import WebSocketsSansIOProtocol

if TYPE_CHECKING:
    from uvicorn._types import ASGISendEvent

    from pysx.runtime.transport import TransportLimits


def websocket_protocol(limits: TransportLimits) -> type[WebSocketsSansIOProtocol]:
    class BoundedProtocol(WebSocketsSansIOProtocol):
        def connection_made(self, transport: asyncio.BaseTransport) -> None:
            super().connection_made(transport)
            self.transport.set_write_buffer_limits(high=limits.write_limit)
            self.queue = asyncio.Queue(maxsize=limits.socket_queue)
            self.close_timeout = limits.close_timeout

        def handle_events(self) -> None:
            try:
                super().handle_events()
            except asyncio.QueueFull:
                self.transport.abort()

        def connection_lost(self, exc: Exception | None) -> None:
            if self.queue.full():
                self.queue.get_nowait()
            super().connection_lost(exc)

        def shutdown(self) -> None:
            if self.queue.full():
                self.transport.abort()
            else:
                super().shutdown()

        async def send(self, message: ASGISendEvent) -> None:
            try:
                await super().send(message)
            except asyncio.QueueFull:
                self.transport.abort()

        async def run_asgi(self) -> None:
            extensions = self.scope.setdefault("extensions", {})
            extensions["pysx.transport"] = {"abort": self.transport.abort}
            await super().run_asgi()

    return BoundedProtocol
