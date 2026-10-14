"""Process-owned graph and task evidence for real signal shutdown tests."""

import asyncio
import json
import os
from pathlib import Path

from pysx import Application, Fragment, HostConfig, native, on_cleanup, resource

created = 0
cleaned = 0


def app() -> Fragment:
    global created
    created += 1

    def cleanup() -> None:
        global cleaned
        cleaned += 1

    on_cleanup(cleanup)

    async def load() -> str:
        await asyncio.sleep(60)

        return "done"

    resource("waiting", load)

    return native.P("bounded transport")


def report() -> None:
    target = Path(os.environ["PYSX_SHUTDOWN_REPORT"])
    target.write_text(
        json.dumps(
            {
                "graphs": len(host.runtime.graphs),
                "tasks": len([task for task in host.runtime.tasks() if not task.done()]),
                "connections": len(host.connections),
                "created": created,
                "cleaned": cleaned,
            }
        )
    )


host = Application(app, config=HostConfig(shutdown=(report,)))
