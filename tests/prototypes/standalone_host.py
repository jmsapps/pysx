"""Actual websockets-17.1 adapter for the isolated shared-engine proof.

Run ``uv run --project . python -m tests.prototypes.standalone_host --port PORT``.
The proof_principal cookie is fixture identity injection, not authentication.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from http import HTTPStatus
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING

from websockets.asyncio.server import ServerConnection, serve

from .host_engine import Engine, HTTPResult, Session

if TYPE_CHECKING:
    from websockets.http11 import Request, Response


CLIENT = """'use strict';
(() => {
  let socket;
  let epoch;
  let event = 0;
  window.proofAttach = () => new Promise((resolve, reject) => {
    const root = document.getElementById('proof-root');
    const input = document.getElementById('proof-input');
    socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
    socket.onopen = () => socket.send(JSON.stringify({
      t: 'hello', token: root.dataset.adoption, edits: {input: input.value}
    }));
    socket.onerror = reject;
    socket.onmessage = ({data}) => {
      const message = JSON.parse(data);
      if (message.t === 'adopted') {
        epoch = message.epoch;
        root.dataset.adopted = 'yes';
        root.dataset.setupSerial = String(message.setup_serial);
        resolve(message);
      } else if (message.t === 'patch') {
        for (const op of message.ops) {
          if (op.op === 'text') document.getElementById(op.id).textContent = op.value;
        }
      } else if (message.t === 'error') reject(new Error(message.code));
    };
  });
  document.getElementById('proof-increment').addEventListener('click', () => {
    if (!socket || socket.readyState !== WebSocket.OPEN || epoch === undefined) return;
    socket.send(JSON.stringify({t:'event', epoch, event:++event, name:'increment', value:''}));
  });
})();
"""


def principal(cookie_header: str) -> str:
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    value = cookie.get("proof_principal")

    return value.value if value is not None else "anonymous"


def response(connection: ServerConnection, result: HTTPResult) -> Response:
    result_response = connection.respond(HTTPStatus(result.status), result.body.decode("utf-8"))

    if "Content-Type" in result_response.headers:
        del result_response.headers["Content-Type"]

    for name, value in result.headers:
        result_response.headers[name] = value

    return result_response


class Standalone:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    async def process_request(
        self,
        connection: ServerConnection,
        request: Request,
    ) -> Response | None:
        if request.path == "/":
            result = await self.engine.http(principal(request.headers.get("Cookie", "")))

            return response(connection, result)

        if request.path == "/client.js":
            result = HTTPResult(
                200, (("content-type", "text/javascript; charset=utf-8"),), CLIENT.encode("utf-8")
            )

            return response(connection, result)

        if request.path == "/ws":
            return None

        return connection.respond(HTTPStatus.NOT_FOUND, "not found")

    async def handler(self, connection: ServerConnection) -> None:
        request = connection.request

        if request is None:
            await connection.close(1011, "missing connection request")

            return
        actor = principal(request.headers.get("Cookie", ""))
        session: Session | None = None

        try:
            async for raw in connection:
                try:
                    if not isinstance(raw, str):
                        raise ValueError("text JSON frames required")
                    session, reply = await self.engine.message(raw, actor, session)
                    await connection.send(json.dumps(reply))
                except ValueError, OSError:
                    await connection.send(json.dumps({"t": "error", "code": "rejected"}))
                    await connection.close(1008, "proof message rejected")

                    break
        finally:
            if session is not None:
                await self.engine.close(session)


async def run(port: int) -> None:
    engine = Engine()
    host = Standalone(engine)

    try:
        async with serve(
            host.handler,
            "127.0.0.1",
            port,
            process_request=host.process_request,
            max_size=4096,
            max_queue=8,
        ):
            sys.stdout.write(f"pysx ready -> http://127.0.0.1:{port}\n")
            sys.stdout.flush()
            await asyncio.get_running_loop().create_future()
    finally:
        await engine.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    arguments = parser.parse_args()
    asyncio.run(run(arguments.port))


if __name__ == "__main__":
    main()
