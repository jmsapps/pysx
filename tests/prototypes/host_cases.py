"""Host, adoption and state proof registrations."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import FrozenInstanceError, replace
from typing import TYPE_CHECKING, cast

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from .host_asgi import ASGI, Event, Scope
from .host_engine import (
    APP_ID,
    MAX_STATE_BYTES,
    AppState,
    Engine,
    Snapshot,
    StaleOwnerError,
    Store,
    decode,
    encode,
)
from .standalone_host import Standalone

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _token(body: bytes) -> str:
    match = re.search(rb'data-adoption="([a-zA-Z0-9_-]+)"', body)
    assert match is not None

    return match.group(1).decode("ascii")


def http_adoption_graph(_tmp_path: Path) -> None:
    async def scenario() -> None:
        engine = Engine()
        rendered = await engine.http("alice")
        assert rendered.status == 200
        assert ("content-type", "text/html; charset=utf-8") in rendered.headers
        assert ("cache-control", "private, no-store") in rendered.headers
        assert b"Server rendered proof" in rendered.body
        assert b'<span id="proof-count" class="proof-count">0</span>' in rendered.body
        assert b"rgb(20, 70, 120)" in rendered.body
        assert b'<input id="proof-input" value="server">' in rendered.body
        original = engine.pending[rendered.token]
        graph = original.graph
        assert engine.setup_count == 1
        session, acknowledgement = await engine.adopt(
            rendered.token,
            "alice",
            "typed before socket",
        )
        assert session is original
        assert session.graph is graph
        assert acknowledgement["t"] == "adopted"
        assert "setup_serial" in acknowledgement
        assert acknowledgement["setup_serial"] == graph.serial == 1
        assert "html" not in acknowledgement
        assert "ops" not in acknowledgement
        assert session.graph.state.get().text == "typed before socket"
        assert engine.setup_count == 1
        reply = await engine.dispatch(session, epoch=1, event=1, name="increment", value="")
        assert "ops" in reply
        assert reply["ops"] == [{"op": "text", "id": "proof-count", "value": "1"}]
        assert session.graph.state.get().text == "typed before socket"
        await engine.close(session)
        await engine.close(session)
        assert graph.disposals == 1
        assert engine.active == {}
        await engine.shutdown()

    asyncio.run(scenario())


def principal_tokens_expiry_capacity(_tmp_path: Path) -> None:
    async def scenario() -> None:
        now = [10.0]
        engine = Engine(capacity=1, ttl=5, clock=lambda: now[0])
        first, full = await asyncio.gather(engine.http("alice"), engine.http("bob"))
        assert first.status == 200
        assert full.status == 503
        assert engine.setup_count == 1
        original = engine.pending[first.token]
        with pytest.raises(ValueError, match="principal"):
            await engine.adopt(first.token, "bob", "hijacked")
        assert original.graph.state.get().text == "server"
        session, _ = await engine.adopt(first.token, "alice", "edited")
        with pytest.raises(ValueError, match="unknown"):
            await engine.adopt(first.token, "alice", "duplicate")
        await engine.close(session)
        pending = await engine.http("bob")
        abandoned = engine.pending[pending.token].graph
        now[0] += 5
        with pytest.raises(ValueError, match="unknown"):
            await engine.adopt(pending.token, "bob", "late")
        assert abandoned.disposals == 1
        assert pending.token not in engine.pending
        assert engine.active == {}
        next_render = await engine.http("carol")
        assert next_render.status == 200
        await engine.shutdown()
        assert abandoned.disposals == 1

    asyncio.run(scenario())


def explicit_codec_and_immutable_snapshot(_tmp_path: Path) -> None:
    snapshot = Snapshot("session-1", "alice", 4, 3, AppState(7, "é😀 <input>"))
    assert decode(encode(snapshot)) == snapshot
    with pytest.raises(FrozenInstanceError):
        snapshot.state.__setattr__("count", 99)
    encoded: object = json.loads(encode(snapshot))
    assert isinstance(encoded, dict)
    data = cast("dict[str, object]", encoded)
    assert set(data) == {"version", "app", "session", "principal", "revision", "highwater", "state"}
    assert data["app"] == APP_ID
    assert "handler" not in data
    assert "graph" not in data
    migrated = dict(data)
    migrated["version"] = 0
    migrated["state"] = {"count": 7}
    assert decode(json.dumps(migrated).encode()).state == AppState(7, "server")
    mutations: tuple[tuple[dict[str, object], str], ...] = (
        ({"version": 999}, "unsupported snapshot version"),
        ({"app": "other-app"}, "incompatible snapshot envelope"),
        ({"state": {"count": True, "text": "x"}}, "count must be a nonnegative integer"),
        (
            {"state": {"count": 1, "text": "x", "callback": "not state"}},
            "incompatible state fields",
        ),
        ({"highwater": -1}, "highwater must be a nonnegative integer"),
    )

    for mutation, reason in mutations:
        bad = data | mutation
        with pytest.raises(ValueError, match=reason):
            decode(json.dumps(bad).encode())
    with pytest.raises(ValueError, match="byte limit"):
        decode(b" " * (MAX_STATE_BYTES + 1))
    with pytest.raises(ValueError, match="snapshot must be an object"):
        decode(b"[]")
    with pytest.raises(ValueError, match="count must be a nonnegative integer"):
        encode(replace(snapshot, state=AppState(True, "bool is not int")))


def reconstruction_epochs_checkpoint_order(_tmp_path: Path) -> None:
    async def scenario() -> None:
        store = Store()
        first = Engine(owner="worker-a", store=store)
        rendered = await first.http("alice")
        original, _ = await first.adopt(rendered.token, "alice", "persisted")
        old_handler = original.graph.handler
        committed = await first.dispatch(original, epoch=1, event=1, name="increment", value="")
        assert "revision" in committed
        assert committed["revision"] == 2
        persisted = decode(store.records[original.snapshot.session].payload)
        assert persisted.state == AppState(1, "persisted")
        second = Engine(owner="worker-b", store=store)
        with pytest.raises(ValueError, match="principal"):
            await second.recover(original.snapshot.session, "bob", expected_epoch=1)
        recovered = await second.recover(original.snapshot.session, "alice", expected_epoch=1)
        assert recovered.graph is not original.graph
        assert recovered.graph.handler is not old_handler
        assert recovered.graph.state.get() == persisted.state
        assert recovered.lease.epoch == 2
        with pytest.raises(StaleOwnerError):
            await first.dispatch(original, epoch=1, event=2, name="increment", value="")
        with pytest.raises(StaleOwnerError):
            store.claim(original.snapshot.session, "worker-c", expected_epoch=1)
        before = store.records[recovered.snapshot.session].payload
        with pytest.raises(StaleOwnerError):
            store.commit(
                original.lease,
                replace(original.snapshot, revision=3),
                expected_revision=2,
            )
        assert store.records[recovered.snapshot.session].payload == before
        duplicate = await second.dispatch(recovered, epoch=2, event=1, name="increment", value="")
        assert duplicate["t"] == "duplicate"
        assert recovered.graph.state.get().count == 1
        observed = list(recovered.graph.observed)
        store.fail_next_commit = True
        with pytest.raises(OSError, match="checkpoint"):
            await second.dispatch(recovered, epoch=2, event=2, name="increment", value="")
        assert recovered.graph.state.get().count == 1
        assert recovered.graph.observed == observed
        assert store.records[recovered.snapshot.session].payload == before
        reply = await second.dispatch(recovered, epoch=2, event=2, name="increment", value="")
        assert "ops" in reply
        assert reply["ops"][0]["value"] == "2"
        assert recovered.graph.state.get().text == "persisted"
        # Crash after checkpoint but before client sees reply: highwater prevents replay.
        third = Engine(owner="worker-c", store=store)
        after_commit = await third.recover(recovered.snapshot.session, "alice", expected_epoch=2)
        duplicate = await third.dispatch(after_commit, epoch=3, event=2, name="increment", value="")
        assert duplicate["t"] == "duplicate"
        assert after_commit.graph.state.get().count == 2
        next_reply = await third.dispatch(
            after_commit,
            epoch=3,
            event=3,
            name="increment",
            value="",
        )
        assert "ops" in next_reply
        assert next_reply["ops"][0]["value"] == "3"
        await first.shutdown()
        await second.shutdown()
        await third.shutdown()

    asyncio.run(scenario())


def standalone_real_http_websocket(_tmp_path: Path) -> None:
    async def scenario() -> None:
        engine = Engine()
        host = Standalone(engine)
        async with serve(
            host.handler,
            "127.0.0.1",
            0,
            process_request=host.process_request,
        ) as server:
            address = cast("tuple[str, int]", server.sockets[0].getsockname())
            port = address[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(
                f"GET / HTTP/1.1\r\nHost: localhost:{port}\r\n"
                f"Cookie: proof_principal=alice\r\nConnection: close\r\n\r\n".encode()
            )
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), 5)
            writer.close()
            await writer.wait_closed()
            headers, _, body = response.partition(b"\r\n\r\n")
            assert b"200 OK" in headers
            assert b"text/html; charset=utf-8" in headers
            token = _token(body)
            original = engine.pending[token]
            assert engine.setup_count == 1
            async with connect(
                f"ws://127.0.0.1:{port}/ws",
                additional_headers={"Cookie": "proof_principal=alice"},
            ) as websocket:
                await websocket.send(
                    json.dumps({"t": "hello", "token": token, "edits": {"input": "typed"}}),
                )
                raw_ack: object = json.loads(await websocket.recv())
                assert isinstance(raw_ack, dict)
                acknowledgement = cast("dict[str, object]", raw_ack)
                assert acknowledgement["t"] == "adopted"
                assert acknowledgement["setup_serial"] == 1
                assert engine.active[original.snapshot.session] is original
                assert original.graph.state.get().text == "typed"
                await websocket.send(
                    json.dumps(
                        {"t": "event", "epoch": 1, "event": 1, "name": "increment", "value": ""},
                    )
                )
                raw_patch: object = json.loads(await websocket.recv())
                assert isinstance(raw_patch, dict)
                patch = cast("dict[str, object]", raw_patch)
                assert patch["t"] == "patch"
                assert patch["ops"] == [{"op": "text", "id": "proof-count", "value": "1"}]
                assert engine.setup_count == 1
        assert original.graph.disposals == 1
        assert engine.active == {}
        await engine.shutdown()

    asyncio.run(scenario())


def asgi_shared_http_websocket_engine(_tmp_path: Path) -> None:
    async def scenario() -> None:
        engine = Engine()
        app = ASGI(engine)
        sent: list[Event] = []
        incoming: asyncio.Queue[Event] = asyncio.Queue()

        async def receive() -> Event:
            return await incoming.get()

        async def send(event: Event) -> None:
            sent.append(event)

        scope: Scope = {
            "type": "http",
            "path": "/",
            "headers": [(b"cookie", b"proof_principal=alice")],
        }
        incoming.put_nowait({"type": "http.request", "body": b"", "more_body": False})
        await app(scope, receive, send)
        start, body = sent[0], sent[1]
        assert start["type"] == "http.response.start"
        assert "status" in start
        assert "headers" in start
        assert start["status"] == 200
        assert (b"content-type", b"text/html; charset=utf-8") in start["headers"]
        assert body["type"] == "http.response.body"
        assert "body" in body
        assert b"Server rendered proof" in body["body"]
        assert b"rgb(20, 70, 120)" in body["body"]
        token = _token(body["body"])
        original = engine.pending[token]
        incoming.put_nowait({"type": "websocket.connect"})
        incoming.put_nowait(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {"t": "hello", "token": token, "edits": {"input": "asgi edit"}},
                ),
            }
        )
        incoming.put_nowait(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {"t": "event", "epoch": 1, "event": 1, "name": "increment", "value": ""},
                ),
            }
        )
        incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})
        sent.clear()
        scope["type"] = "websocket"
        scope["path"] = "/ws"
        await app(scope, receive, send)
        assert [event["type"] for event in sent] == [
            "websocket.accept",
            "websocket.send",
            "websocket.send",
        ]
        ack_frame, patch_frame = sent[1], sent[2]
        assert "text" in ack_frame
        assert "text" in patch_frame
        raw_ack: object = json.loads(ack_frame["text"])
        assert isinstance(raw_ack, dict)
        acknowledgement = cast("dict[str, object]", raw_ack)
        assert acknowledgement["setup_serial"] == 1
        raw_patch: object = json.loads(patch_frame["text"])
        assert isinstance(raw_patch, dict)
        patch = cast("dict[str, object]", raw_patch)
        assert patch["ops"] == [{"op": "text", "id": "proof-count", "value": "1"}]
        assert original.graph.state.get().text == "asgi edit"
        assert original.graph.disposals == 1
        assert engine.setup_count == 1
        assert engine.active == {}
        await engine.shutdown()

    asyncio.run(scenario())


CASES: dict[str, Callable[[Path], None]] = {
    "http_same_graph_adoption": http_adoption_graph,
    "principal_tokens_ttl_capacity": principal_tokens_expiry_capacity,
    "explicit_versioned_immutable_codec": explicit_codec_and_immutable_snapshot,
    "fresh_closures_epochs_checkpoint": reconstruction_epochs_checkpoint_order,
    "standalone_http_websocket": standalone_real_http_websocket,
    "asgi_shared_http_websocket": asgi_shared_http_websocket_engine,
}
