"""Disposable, bounded TLS termination fixture with an explicit trusted peer."""

from __future__ import annotations

import asyncio
import shutil
import ssl
import subprocess
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path


def certificate(directory: Path) -> tuple[Path, Path]:
    executable = shutil.which("openssl")

    if executable is None:
        raise RuntimeError("TLS verification requires openssl")
    cert, key = directory / "certificate.pem", directory / "key.pem"
    subprocess.run(
        [
            executable,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )

    return cert, key


@asynccontextmanager
async def proxy(
    directory: Path, backend: int, port: int = 0
) -> AsyncGenerator[tuple[int, ssl.SSLContext]]:
    cert, key = certificate(directory)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    tasks: set[asyncio.Task[None]] = set()
    writers: set[asyncio.StreamWriter] = set()

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        tasks.add(task)
        writers.add(writer)
        upstream: asyncio.StreamWriter | None = None

        try:
            async with asyncio.timeout(5):
                head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
                lines = head.split("\r\n")
                headers = [
                    line
                    for line in lines[1:]
                    if line and not line.lower().startswith(("forwarded:", "x-forwarded-"))
                ]
                host = next(
                    line.split(":", 1)[1].strip()
                    for line in headers
                    if line.lower().startswith("host:")
                )
                incoming, upstream = await asyncio.open_connection("127.0.0.1", backend)
                writers.add(upstream)
                forwarded = "\r\n".join(
                    [
                        lines[0],
                        *headers,
                        "X-Forwarded-Proto: https",
                        f"X-Forwarded-Host: {host}",
                        "",
                        "",
                    ]
                )
                upstream.write(forwarded.encode("latin-1"))
                await upstream.drain()

            async def copy(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
                while chunk := await source.read(16384):
                    target.write(chunk)
                    async with asyncio.timeout(5):
                        await target.drain()

            pumps = [
                asyncio.create_task(copy(reader, upstream)),
                asyncio.create_task(copy(incoming, writer)),
            ]

            try:
                await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for pump in pumps:
                    pump.cancel()
                await asyncio.gather(*pumps, return_exceptions=True)
        except (
            OSError,
            ValueError,
            StopIteration,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            TimeoutError,
        ):
            pass
        finally:
            try:
                for owned in (writer, upstream):
                    if owned is not None:
                        writers.discard(owned)
                        owned.close()
                        with suppress(OSError, TimeoutError):
                            async with asyncio.timeout(1):
                                await owned.wait_closed()
            finally:
                tasks.discard(task)

    server = await asyncio.start_server(
        serve, "127.0.0.1", port, ssl=tls, limit=16384, ssl_handshake_timeout=5
    )
    client = ssl.create_default_context(cafile=str(cert))

    try:
        yield server.sockets[0].getsockname()[1], client
    finally:
        server.close()
        await server.wait_closed()

        for writer in writers:
            writer.close()

        for task in tuple(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert not tasks


async def get(
    port: int, tls: ssl.SSLContext, path: str, cookie: str = "fixture_user=alice"
) -> tuple[int, dict[str, str], str]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=tls)

    try:
        writer.write(
            (
                f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                f"Cookie: {cookie}\r\nConnection: close\r\n\r\n"
            ).encode()
        )
        await writer.drain()
        async with asyncio.timeout(5):
            payload = (await reader.read()).decode()
        head, _, body = payload.partition("\r\n\r\n")
        lines = head.split("\r\n")
        headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)

        return (
            int(lines[0].split()[1]),
            {name.lower(): value for name, value in headers.items()},
            body,
        )
    finally:
        writer.close()
        await writer.wait_closed()
