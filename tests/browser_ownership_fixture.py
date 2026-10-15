"""Disposable two-worker gateway with local-only failure controls."""

from __future__ import annotations

import argparse
import asyncio
import signal
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from ownership_topology import Workers

if TYPE_CHECKING:
    from ownership_topology import Worker


class Gateway:
    def __init__(self, workers: Workers) -> None:
        self.workers = workers
        self.known = {worker.identifier: worker for worker in workers.workers}
        self.preferred: Worker | None = None
        self.next = 0
        self.tasks: set[asyncio.Task[None]] = set()
        self.writers: set[asyncio.StreamWriter] = set()

    def select(self, path: str) -> Worker:
        hint = parse_qs(urlsplit(path).query).get("owner", [""])[0]
        worker = self.known.get(hint)

        if worker is not None and worker.process.poll() is None:
            return worker

        if self.preferred is not None and self.preferred.process.poll() is None:
            return self.preferred
        alive = [worker for worker in self.known.values() if worker.process.poll() is None]

        if not alive:
            raise OSError("no live workers")
        selected = alive[self.next % len(alive)]
        self.next += 1

        return selected

    async def control(self, path: str) -> None:
        parsed = urlsplit(path)
        query = parse_qs(parsed.query)

        if parsed.path == "/_owner_control/reset":
            await asyncio.to_thread(self.workers.close)
            self.workers.store.unlink(missing_ok=True)
            first = await asyncio.to_thread(self.workers.start)
            second = await asyncio.to_thread(self.workers.start)
            self.known = {worker.identifier: worker for worker in (first, second)}
            self.preferred = None

            return
        worker = self.known[query["worker"][0]]
        await asyncio.to_thread(worker.stop, kill=parsed.path.endswith("/kill"))

        if parsed.path.endswith("/restart"):
            replacement = await asyncio.to_thread(
                self.workers.start,
                port=worker.port,
                schema=int(query.get("schema", ["1"])[0]),
                migrate=query.get("migrate") == ["1"],
                fresh=query.get("fresh") == ["1"],
            )
            self.known[replacement.identifier] = replacement
            self.preferred = replacement
        else:
            self.preferred = next(
                (
                    candidate
                    for candidate in self.known.values()
                    if candidate.process.poll() is None
                ),
                None,
            )

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.tasks.add(task)
        self.writers.add(writer)
        upstream: asyncio.StreamWriter | None = None

        try:
            async with asyncio.timeout(10):
                head = await reader.readuntil(b"\r\n\r\n")
                path = head.split(b"\r\n", 1)[0].decode("latin-1").split()[1]

                if path.startswith("/_owner_control/"):
                    await self.control(path)
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
                    )
                    await writer.drain()

                    return
                worker = self.select(path)
                incoming, upstream = await asyncio.open_connection("127.0.0.1", worker.port)
                self.writers.add(upstream)
                upstream.write(head)
                await upstream.drain()

            async def copy(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
                while chunk := await source.read(16384):
                    target.write(chunk)
                    async with asyncio.timeout(2):
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
            KeyError,
            IndexError,
            TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            with suppress(OSError):
                writer.write(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                )
                await writer.drain()
        finally:
            owned_writers = [owned for owned in (writer, upstream) if owned is not None]

            for owned in owned_writers:
                self.writers.discard(owned)
                owned.close()

            try:
                for owned in owned_writers:
                    with suppress(OSError, TimeoutError):
                        async with asyncio.timeout(1):
                            await owned.wait_closed()
            finally:
                self.tasks.discard(task)

    async def close(self) -> None:
        for writer in tuple(self.writers):
            writer.close()
        tasks = tuple(self.tasks)

        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.to_thread(self.workers.close)
        assert not self.tasks
        assert not self.writers
        assert all(worker.process.poll() is not None for worker in self.workers.workers)


async def run(port: int) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stop.set)
    with tempfile.TemporaryDirectory(prefix="pysx-ownership-") as directory:
        workers = Workers(Path(directory)).__enter__()
        gateway = Gateway(workers)

        try:
            server = await asyncio.start_server(gateway.serve, "127.0.0.1", port, limit=16384)

            try:
                print(f"pysx ready -> http://127.0.0.1:{port}", flush=True)
                await stop.wait()
            finally:
                server.close()
                await server.wait_closed()
        finally:
            await gateway.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    asyncio.run(run(parser.parse_args().port))
