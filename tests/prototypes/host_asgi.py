"""ASGI 3 HTTP/WS adapter using the exact same isolated Engine operations.

Grounded in https://asgi.readthedocs.io/en/latest/specs/www.html (subspec 2.5).
This proof drives standard events directly; production ASGI-host qualification,
lifespan integration and framework deployment are out of scope.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Required, TypedDict

from .host_engine import Engine, HTTPResult, Session
from .standalone_host import CLIENT, principal

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class Scope(TypedDict):
    type: str
    path: str
    headers: list[tuple[bytes, bytes]]


class Event(TypedDict, total=False):
    type: Required[str]
    text: str
    bytes: bytes
    body: bytes
    more_body: bool
    status: int
    headers: list[tuple[bytes, bytes]]
    code: int


class ASGI:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    async def __call__(
        self, scope: Scope, receive: Callable[[], Awaitable[Event]],
        send: Callable[[Event], Awaitable[None]],
    ) -> None:
        cookie = "; ".join(
            value.decode("latin-1") for name, value in scope["headers"] if name.lower() == b"cookie"
        )
        actor = principal(cookie)
        if scope["type"] == "http":
            request = await receive()
            if request.get("type") != "http.request" or request.get("more_body", False):
                raise ValueError("proof handles completed GET requests")
            if scope["path"] == "/":
                result = await self.engine.http(actor)
            elif scope["path"] == "/client.js":
                result = HTTPResult(
                    200,
                    (("content-type", "text/javascript; charset=utf-8"),),
                    CLIENT.encode(),
                )
            else:
                result = HTTPResult(404, (), b"not found")
            await send({
                "type": "http.response.start", "status": result.status,
                "headers": [
                    (name.encode("ascii"), value.encode("latin-1"))
                    for name, value in result.headers
                ],
            })
            await send({"type": "http.response.body", "body": result.body, "more_body": False})
            return
        if scope["type"] != "websocket" or scope["path"] != "/ws":
            raise ValueError("unsupported proof scope")
        if (await receive()).get("type") != "websocket.connect":
            raise ValueError("expected websocket.connect")
        await send({"type": "websocket.accept"})
        session: Session | None = None
        try:
            while True:
                event = await receive()
                if event.get("type") == "websocket.disconnect":
                    return
                if event.get("type") != "websocket.receive" or "text" not in event:
                    raise ValueError("proof receives text frames")
                session, reply = await self.engine.message(event["text"], actor, session)
                await send({"type": "websocket.send", "text": json.dumps(reply)})
        except ValueError:
            await send({"type": "websocket.close", "code": 1008})
        except OSError:
            await send({"type": "websocket.close", "code": 1011})
        finally:
            if session is not None:
                await self.engine.close(session)
