# Learn pysx

A standalone, server-rendered tutorial website: 28 lessons, a course overview,
live exercises and a final review. Each lesson has named Previous/Next links,
explanations, a runnable code walkthrough and either a live exercise or local playground
instructions. The course
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
uv run --no-project --python 3.14 python bootstrap.py --wheel /path/to/pysx_framework-0.1.0-py3-none-any.whl
uv run --project . pysx-tutorial
```

The candidate is stored in `.artifacts/` and its resolved hash is recorded in the
application lockfile. Keep the installed dependency version and candidate wheel
aligned when deliberately upgrading. Do not use an unpinned dependency range.

## Learning journeys

Start at the overview and follow the course in order. Live exercises have Reset demo;
playground lessons provide local run instructions. Both describe observable outcomes.
Marking a lesson practiced is a
self-assessment, not a claim that the application checked your answer.

Private lesson state is per session. A fresh tab and refresh start fresh ordinary
UI state. Reset demo recreates the local example while preserving the selected
course URL and site-wide theme. Reset workspace restores the capstone's declared
task data. Navigating to another lesson disposes the previous lesson's inline demo.
The routing exercises use real course-prefixed URLs and actual browser history.

For reconnect, use **Disconnect for 5 seconds** in the live exercise. The button
closes the real WebSocket, pauses retries, and reconnects automatically. Watch the
connection status, retained draft/count, and advancing server clock without
refreshing. Lessons that use playgrounds instead have a **Try it locally** section:
installation and run commands, **What you’ll do**, and **What you’ll see**.
Run the browser-recovery experiment in the recovery playground, keeping its tab
open while stopping and restarting its server. A refresh still starts fresh.

The explicit durable-state lesson uses the durable playground. Keep its document
open through a restart: its declared draft/count reconstruct and its setup marker
changes. Only declared state is persisted; callbacks/resources are rebuilt. Restart
with the run command rather than scaffolding again, which resets playground state.
The course's standalone qualification lab remains available at `/labs/durable`.
Its store and host key ring live under `.artifacts/tutorial-state` by default;
`TUTORIAL_STATE_DIR` selects a private persistent directory.

The development-workflow lesson uses `pysx-playground development` with
`pysx --app playgrounds.development --watch` for its diagnostic and reload edits.
The resilient capstone has its own `pysx-playground capstone` application, run with
`pysx --app playgrounds.capstone`: it retains task records, notes and ordering
through server reconstruction and provides real validation failure/retry controls.
Each lesson's code walkthrough matches its scaffolded playground source.

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
contents and recursive controls. Code panels are authored directly as pysx spans in
`src/snippets/`; edit their text and `data-syntax` colors there. Each lesson references
one named sample, and its runnable Python text is read from that same markup for the
walkthrough checks. There is no highlighting generation step or source hash lookup.
The interactive code displays select authored samples directly; arbitrary source is
escaped plain text. The deployed course needs no editor tools or highlighting data files.

The application does not import the example or test packages at runtime. Publishing
is a separate operation from running this local teaching application.
