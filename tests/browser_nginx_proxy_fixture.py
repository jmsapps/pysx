"""Serve the nginx deployment example behind real nginx with disposable TLS."""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nginx_proxy import application, nginx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    arguments = parser.parse_args()
    public = f"https://localhost:{arguments.port}"

    with (
        tempfile.TemporaryDirectory() as directory,
        application("examples.nginx:example", environment={"PYSX_ORIGIN": public}) as upstream,
        nginx(Path(directory), upstream, port=arguments.port),
    ):
        print("pysx ready", flush=True)

        try:
            while True:
                time.sleep(0.2)
        except KeyboardInterrupt:
            return


if __name__ == "__main__":
    main()
