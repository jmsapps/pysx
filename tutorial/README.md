# Learn pysx

A standalone, server-rendered tutorial website: 28 lessons, a course overview,
live exercises and a final review. Each lesson has named Previous/Next links,
explanations, a runnable code walkthrough and an interactive demo. The course
owns its shared components, themes, routes and assets.

## Prepare and run

Python 3.14+ and uv are required. From the library checkout root:

```sh
uv run --project . python tutorial/bootstrap.py
uv run --project tutorial pysx-tutorial
```

Open http://127.0.0.1:8750. The bootstrap builds a real framework wheel, then creates
the tutorial's own environment and installs its application. It does not run the
course using imports from the library checkout or the examples registry.

With the tutorial environment activated, the app's command is simply:

```sh
pysx-tutorial
```

Use `--port 9100` for another port, `--watch` for development, or `--asgi` after
installing the `asgi` extra. Ctrl+C stops the course and its owned durable lab.
For editor support, prepare with `--editor`, then select `tutorial/.venv` in the
Python extension. The course explains the distinction between advisory diagnostics
and runtime verification.

## Run outside the checkout

Copy this entire directory to a writable application directory. Supply the exact
framework candidate wheel to the bootstrap; no parent checkout is required:

```sh
uv run --no-project --python 3.14 python bootstrap.py --wheel /path/to/pysx-0.1.0-py3-none-any.whl
uv run --project . pysx-tutorial
```

The candidate is stored in `.artifacts/` and its resolved hash is recorded in the
application lockfile. Keep the installed dependency version and candidate wheel
aligned when deliberately upgrading. Do not use an unpinned dependency range.

## Learning journeys

Start at the overview and follow the course in order. Each lesson has Reset demo
and an exercise describing an observable outcome. Marking a lesson practiced is a
self-assessment, not a claim that the application checked your answer.

Private lesson state is per session. A fresh tab and refresh start fresh ordinary
UI state. Reset demo recreates the local example while preserving the selected
course URL and site-wide theme. Reset workspace restores the capstone's declared
task data. Navigating to another lesson disposes the previous lesson's inline demo.
The routing exercises use real course-prefixed URLs and actual browser history.

For reconnect, temporarily block the page's WebSocket using browser network tools
and restore it without refreshing. For browser recovery, edit a draft/count, keep
the tab open, stop the server and restart the same command. The actual connection
indicator reports restoration; a refresh still starts fresh.

The explicit durable-state lesson embeds a separate real durable graph on the
same host. Its SQLite store and host key ring live under `.artifacts/tutorial-state`
in the launch directory by default. Set `TUTORIAL_STATE_DIR` to a private persistent
directory to retain them across deployment directories. Keep the embedded document
open through a restart: its declared draft/count reconstruct and its setup marker
changes. Only declared state is persisted; callbacks/resources are rebuilt.

The authorization lesson's reader/writer sign-in links are local teaching roles,
not production login. No real credentials are requested. The protected action uses
the host's actual current authorization policy. For real hosting, supply your
identity provider, HTTPS origins, secure cookies and deliberate access policy.

## Source guide

- `src/catalog.py` owns course order and adjacency.
- `src/lessons/` contains original explanations, snippets and exercises grounded
  in the library's maintained runnable examples.
- `src/demos/` contains live subject-specific behavior.
- `src/components/`, `src/themes.py` and `src/assets/` are shared
  application-owned presentation.
- `src/app.py` exports the host; `src/durable.py` owns the declared-state lab.

The interface is authored in multiline pysx templates, including shared navigation,
contents and recursive controls. Code panels use escaped spans generated with the
editor's own Python grammar and pysx injection. After editing teaching snippets in
the framework checkout, run `node scripts/highlight_tutorial.mjs`; the grammar gate
checks that the generated data matches. The deployed course needs no editor tools.

The application does not import the example or test packages at runtime. Publishing
is a separate operation from running this local teaching application.
