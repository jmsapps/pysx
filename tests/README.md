# Running tests

Run commands from the repository root. Python 3.14+ is required; use uv to select
the project's interpreter.

Install the locked development tools, then provision the Node, browser and Pylance
inputs described in [verification setup](VERIFICATION.md).

```sh
uv sync --locked
uv run --project . pytest -q
uv run --project . ruff check .
uv run --project . mypy
uv run --project . pyright
```

The full pytest suite includes unit tests, strict typing fixtures, CLI smoke tests,
headless acceptance and verification-harness checks. Headless acceptance needs
permitted loopback access. To exclude socket acceptance cases:

```sh
uv run --project . pytest -q -m 'not acceptance'
```

Harness checks still require the inputs in the verification guide. To run a
specific test file, pass its path to pytest:

```sh
uv run --project . pytest -q tests/test_forms.py
```

Direct headless acceptance entry points are also available:

```sh
uv run --project . python tests/acceptance.py
uv run --project . python tests/acceptance_todos.py
```

Run all registered browser, grammar and editor suites with:

```sh
npm --prefix tests run browser
npm --prefix tests run grammar
npm --prefix editor test
```

The browser runner checks Chromium, Firefox and WebKit. Grammar checks use Pylance,
and editor checks build a fresh extension and run it in a VSCode host.
[Verification setup](VERIFICATION.md) owns installation instructions, suite
selectors, supported environments and harness troubleshooting.
