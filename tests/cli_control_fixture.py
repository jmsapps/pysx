"""Let browser fixtures interrupt an isolated CLI using real console events."""

import subprocess
import sys
import threading

from acceptance_support import interrupt, stop_process


def main() -> int:
    child = subprocess.Popen(
        [sys.executable, "-m", "pysx.hosting.server", *sys.argv[1:]],
        stdin=subprocess.DEVNULL,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )

    def kill() -> None:
        if child.poll() is not None:
            return

        if sys.platform == "win32":
            stop_process(child, process_group=False)
        else:
            child.kill()

    def control() -> None:
        try:
            for line in sys.stdin:
                if child.poll() is not None:
                    return
                action = line.strip()

                if action == "SIGINT":
                    interrupt(child)
                elif action == "SIGKILL":
                    kill()
                else:
                    raise ValueError(f"unknown fixture control action: {action!r}")
        except Exception as error:
            sys.stderr.write(f"CLI control failed: {error}\n")
            sys.stderr.flush()
        finally:
            kill()

    reader = threading.Thread(target=control, daemon=True)
    reader.start()

    try:
        return child.wait()
    finally:
        kill()
        child.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
