"""Actual browser/editor and resident-workspace observations on local hardware."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

from dev_measurements import disabled_overhead
from dev_workload import create_workspace, describe_workspace
from qualification_runtime import rss_bytes, summarize
from test_lsp import Client
from test_lsp_edits import applied

from pysx.tooling.lsp.workspace import position
from scripts.verify import Gate, run

if TYPE_CHECKING:
    from typing import Any

    from pysx.runtime.wire import JSONValue


def browser_samples(output: str) -> list[JSONValue]:
    prefix = "QUALIFICATION BROWSER SAMPLES: "
    reports: list[JSONValue] = []
    engines: set[str] = set()

    for line in output.splitlines():
        if not line.startswith(prefix):
            continue
        report = cast("dict[str, JSONValue]", json.loads(line[len(prefix) :]))
        engine, samples = report.get("engine"), report.get("samples_seconds")

        if not isinstance(engine, str) or engine in engines or report.get("rows") != 1000:
            raise ValueError("invalid browser engine/fixture report")

        if not isinstance(samples, list) or len(samples) != 5:
            raise ValueError("missing browser samples")

        if not all(
            isinstance(value, (int, float)) and not isinstance(value, bool) for value in samples
        ):
            raise ValueError("invalid browser samples")
        report["summary"] = summarize(cast("list[float]", samples))
        reports.append(report)
        engines.add(engine)

    if engines != {"chromium", "firefox", "webkit"}:
        raise ValueError("missing required browser engines")

    return reports


def validate_overhead(overhead: dict[str, JSONValue]) -> None:
    """Reject a missing verdict; the limit itself stays a retained observation."""

    samples = overhead.get("samples")

    if not isinstance(samples, list) or len(samples) != 5:
        raise ValueError("missing disabled tracing overhead samples")

    if not isinstance(overhead.get("passed"), bool) or overhead.get("limit_percent") != 5:
        raise ValueError("missing disabled tracing overhead verdict")


def run_command(gate: Gate, log: Path, banner: str) -> str:
    passed, _elapsed, output = run(gate, stream=False)
    log.write_text(output)

    if not passed or banner not in output:
        raise RuntimeError(f"command failed or missing assertions: {log.name}")

    return output


def resident(directory: Path) -> dict[str, JSONValue]:
    with tempfile.TemporaryDirectory(prefix="qualification-resident-") as temporary:
        root = Path(temporary).resolve()
        create_workspace(root)
        workload = describe_workspace(root)
        path = root / "user_app.py"
        source = path.read_text()
        started = time.perf_counter()
        client = Client(root)

        try:
            client.open(path, source)
            client.diagnostic(path, 1, None)
            ready = wait_ready(client)
            cold = time.perf_counter() - started
            at = source.index("C000_0", source.index("return pysx(")) + 2
            point = position(source, at)
            params = {
                "textDocument": {"uri": path.as_uri()},
                "position": {"line": point.line, "character": point.character},
            }
            timings: dict[str, JSONValue] = {}

            for method in ("completion", "hover", "definition", "references"):
                samples: list[float] = []
                request_params = dict(params)

                if method == "completion":
                    native_point = position(source, source.index("span:") + 2)
                    request_params["position"] = {
                        "line": native_point.line,
                        "character": native_point.character,
                    }

                for _ in range(5):
                    before = time.perf_counter()
                    result = client.request(
                        f"textDocument/{method}",
                        {
                            **request_params,
                            **(
                                {"context": {"includeDeclaration": True}}
                                if method == "references"
                                else {}
                            ),
                        },
                    )
                    samples.append(time.perf_counter() - before)
                    assert result, (method, result)
                timings[method] = summarize(samples)
            edits: list[float] = []
            updated = source

            for version in range(2, 7):
                updated = source.replace("build-0", f"build-{version}")
                before = time.perf_counter()
                client.change(path, updated, version)
                client.diagnostic(path, version, None)
                edits.append(time.perf_counter() - before)
            before = time.perf_counter()
            renamed = client.request("textDocument/rename", {**params, "newName": "RenamedCard"})
            rename = time.perf_counter() - before
            assert isinstance(renamed, dict)
            changes = cast(
                "dict[str, list[dict[str, Any]]]", cast("dict[str, object]", renamed)["changes"]
            )
            assert changes
            assert path.as_uri() in changes
            assert (root / "leaf_000.py").as_uri() in changes, list(changes)[:3]
            before = time.perf_counter()
            formatted = client.request(
                "textDocument/formatting",
                {
                    "textDocument": {"uri": path.as_uri()},
                    "options": {"tabSize": 4, "insertSpaces": True},
                },
            )
            formatting = time.perf_counter() - before
            assert isinstance(formatted, list)
            assert applied(updated, cast("list[dict[str, Any]]", formatted)) == updated
            variants: dict[str, JSONValue] = {}
            shared = root / "leaf_000.py"
            shared_source = shared.read_text()
            client.open(shared, shared_source)
            client.diagnostic(shared, 1, None)
            shared_updated = shared_source.replace(
                "def C000_0()", 'def C000_0(*, title: str = "shared")'
            ).replace('span: "build-0"', "span: {title}", 1)
            before = time.perf_counter()
            client.change(shared, shared_updated, 2)
            client.diagnostic(shared, 2, None)
            wait_ready(client)
            variants["imported_signature_seconds"] = time.perf_counter() - before
            unrelated = root / "leaf_150.py"
            unrelated_source = unrelated.read_text()
            client.open(unrelated, unrelated_source)
            client.diagnostic(unrelated, 1, None)
            unrelated_updated = unrelated_source.replace("build-0", "unrelated")
            before = time.perf_counter()
            client.change(unrelated, unrelated_updated, 2)
            client.diagnostic(unrelated, 2, None)
            wait_ready(client)
            variants["unrelated_edit_seconds"] = time.perf_counter() - before
            before = time.perf_counter()

            for version in range(7, 12):
                updated = source.replace("build-0", f"rapid-{version}")
                client.change(path, updated, version)
            settled_diagnostics = client.diagnostic(path, 11, None)
            wait_ready(client)
            variants["five_rapid_edits_seconds"] = time.perf_counter() - before
            final = client.status()
            backend_pid = final["workspaces"][0]["pid"]
            assert type(backend_pid) is int
            memory: dict[str, JSONValue] = {
                "server": rss_bytes(client.process.pid),
                "backend": rss_bytes(backend_pid),
            }
        finally:
            client.close()
        fresh = Client(root)

        try:
            fresh.open(shared, shared_updated)
            fresh.open(unrelated, unrelated_updated)
            fresh.open(path, updated)
            fresh_diagnostics = fresh.diagnostic(path, 1, None)
            fresh_status = wait_ready(fresh)
            assert fresh_diagnostics["diagnostics"] == settled_diagnostics["diagnostics"]
        finally:
            fresh.close()
        result_report: dict[str, JSONValue] = {
            "workload": workload,
            "cold_seconds": cold,
            "warm_requests": timings,
            "post_edit": summarize(edits),
            "rename_seconds": rename,
            "renamed_files": len(changes),
            "format_seconds": formatting,
            "initial_status": cast("JSONValue", ready),
            "final_status": cast("JSONValue", final),
            "edit_variants": variants,
            "fresh_status": cast("JSONValue", fresh_status),
            "fresh_diagnostics_match": True,
            "rss_bytes": memory,
            "budget_observations": {
                "cold_limit_seconds": 2,
                "cold_within_limit": cold <= 2,
                "rename_limit_seconds": 1,
                "rename_within_limit": rename <= 1,
                "format_limit_seconds": 1,
                "format_within_limit": formatting <= 1,
                "interpretation": "local observations; no reference qualification claimed",
            },
            "numeric_reference_qualification": "deferred; raw local observations only",
        }
        (directory / "resident.json").write_text(json.dumps(result_report, indent=2) + "\n")

        return result_report


def wait_ready(client: Client) -> dict[str, object]:
    deadline = time.monotonic() + 120
    state: dict[str, object] = {}

    while time.monotonic() < deadline:
        current = client.status()
        state = current
        workspaces = current["workspaces"]

        if workspaces and all(item["ready"] for item in workspaces):
            return state

        if any(item["error"] for item in workspaces):
            raise RuntimeError(f"workspace analysis failed: {state}")
        time.sleep(0.02)

    raise RuntimeError(f"workspace readiness timed out: {state}")


def tooling(directory: Path) -> dict[str, JSONValue]:
    directory.mkdir(parents=True, exist_ok=True)
    browser = run_command(
        Gate(
            "browser",
            "qualification-browser",
            ("npm", "--prefix", "tests", "run", "browser", "--", "--suite", "editor_browser"),
            timeout=600,
        ),
        directory / "browser.log",
        "BROWSER VERIFICATION PASSED: editor_browser",
    )
    browsers = browser_samples(browser)
    editor = run_command(
        Gate(
            "editor",
            "qualification-editor",
            ("npm", "--prefix", "editor", "test", "--", "--suite", "client_verify"),
            needs_display=True,
            timeout=600,
        ),
        directory / "editor.log",
        "EDITOR VERIFICATION PASSED: client_verify",
    )
    assert editor.count("PYSX EDITOR CASES PASSED: 12") == 2
    resident_report = resident(directory)
    overhead = cast("dict[str, JSONValue]", disabled_overhead())
    validate_overhead(overhead)
    (directory / "disabled-overhead.json").write_text(json.dumps(overhead, indent=2) + "\n")
    development = directory / "development.json"
    run_command(
        Gate(
            "python",
            "qualification-development",
            (
                sys.executable,
                "tests/dev_measurements.py",
                "--section",
                "reload",
                "--report",
                str(development),
            ),
            timeout=600,
        ),
        directory / "development.log",
        "DEV MEASUREMENTS PASSED",
    )

    return {
        "browsers": browsers,
        "editor": {"suite": "client_verify", "cases": 12, "activation_orders": 2},
        "resident": resident_report,
        "disabled_overhead": overhead,
        "disabled_overhead_within_limit": overhead["passed"],
        "development": cast("JSONValue", json.loads(development.read_text())),
        "reference_numeric_qualification": "deferred; local failures retained in raw reports",
    }
