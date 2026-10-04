# Browser, grammar and editor verification

Run from the repository root with Node 24, npm, uv and the locked Python 3.14.4
environment. All dependencies are repository-local and exact versions are locked.

```sh
uv sync --locked --python 3.14.4
npm --prefix tests ci
npm --prefix editor ci
npm --prefix tests exec -- playwright install chromium firefox webkit
```

On Linux, install browser system libraries with
`npm --prefix tests exec -- playwright install --with-deps chromium firefox webkit`.
VSCode needs a display (use `xvfb-run -a` in headless Linux), GTK/NSS/audio libraries
and the standard Electron runtime dependencies. macOS and Windows need their native
desktop session. The setup is platform-specific; only local macOS results have been
observed. Do not infer Linux/Windows passes from configuration.

The grammar harness resolves Pylance through the installed VSCode extension registry.
For managed CI, provision the exact official host in the ignored repository cache:

```sh
npm --prefix editor run setup:host
export PYSX_EXTENSIONS_DIR="$PWD/.vscode-test/extensions"
```

This downloads VSCode 1.140.0 and installs Pylance 2026.4.1 with its required extensions
using the official VSCode CLI, without modifying the normal user extension installation.
Alternatively set `PYSX_PYLANCE_EXTENSION` to a complete, provisioned Pylance extension
directory. Its manifest must contribute a compatible PEP 750 `source.python` grammar;
a copied grammar alone or MagicPython is rejected. Output reports the selected version,
path and SHA256. Retain that provenance with remote CI results. An unavailable Marketplace
or required host input fails setup; it cannot become a skipped passing test.

```sh
npm --prefix tests run browser -- --suite examples
npm --prefix tests run grammar -- --suite injection
npm --prefix editor test -- --suite diagnostics
uv run --project . pytest -q tests/test_verification_harness.py
```

Isolated feasibility cases are registered separately from the shipped runtime/client:

```sh
uv run --project . pytest -q tests/test_architecture_proofs.py tests/test_distribution.py
uv run --project . pytest -q tests/test_typing_contracts.py -k operator_typing
npm --prefix tests run browser -- --suite cascade
npm --prefix tests run browser -- --suite adoption
npm --prefix editor test -- --suite packaging
```

The browser proofs assert computed CSS lineage and HTTP-to-WebSocket adoption in
all three engines. The editor proof builds a scratch wheel/VSIX and launches the
installed isolated stdio module through a real host outside the checkout. Distribution
proofs build both a wheel and an sdist-derived wheel and install them into fresh consumer
environments. These gates need uv's pinned backend plus cached exact runtime/example
dependencies; the fixture can use existing uv caches without modifying them. Artifacts,
consumer environments and profiles live in temporary directories. No prototype module
becomes a shipped production API or LSP feature.

Each runner accepts `--suite`; unknown or empty selections fail. Unfiltered commands run
all currently registered suites. Pass banners are emitted only after all required cases pass.

Browser verification selects the interpreter through `uv run --project .`, reserves
ephemeral ports, starts both example servers and runs the original assertion groups
in Chromium, Firefox and WebKit. Startup and suite deadlines are bounded; child groups
are cleaned on failure or termination. `PYSX_BROWSER_PORT` sets a fixed first port for
fault tests; occupied listeners are never taken over. Browser launches clear macOS
`DYLD_LIBRARY_PATH` so bundled NSS libraries are used.

Editor tests freshly build the stdlib VSIX, unpack it into a disposable directory and
load that artifact in an isolated profile. The runner discovers local VSCode or downloads
the pinned version; `PYSX_VSCODE_EXECUTABLE` explicitly selects a managed executable.
Inherited VSCode/Node-host environment variables are removed. Activation, saved-file
diagnostic contents/range and Python-document close cleanup are asserted in the actual
host; the launcher has a 120-second deadline and removes its profile and process group.
The checkout/interpreter requirement remains the baseline client contract. Windows
currently requires the baseline client's compatible `.venv/bin/python` layout; native
Scripts-path support remains unverified and must not be represented as a portable
installed-client pass.

The quality workflow provisions these inputs before full pytest. Release qualification must
collect actual remote platform output, selected versions, assertions, exit codes and
artifacts. Neither the workflow nor this local harness constitutes remote execution evidence.

Fault injection variables (`PYSX_BROWSER_FORCE_FAILURE`, `PYSX_INJECTION_GRAMMAR`,
`PYSX_EDITOR_PROBE`, `PYSX_EDITOR_FORCE_TIMEOUT`, `PYSX_EDITOR_STATE_FILE`) are harness
test controls. Probe mode builds and discovers inputs but never emits a pass banner.
