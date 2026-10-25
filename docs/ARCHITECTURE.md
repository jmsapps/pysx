# Repository structure

The library uses a root package at `pysx/`. Implementation lives in nine subsystem
packages. Start with the owner below when navigating or extending the SDK.

```text
pysx/
├── __init__.py                 # public application API
├── reactive/
│   ├── __init__.py
│   ├── reactive.py             # signals, effects and dependency tracking
│   ├── operators.py
│   └── structured.py
├── compiler/
│   ├── __init__.py
│   ├── compiler.py             # static compilation and projection
│   ├── scope.py
│   ├── imports.py
│   ├── parser.py
│   ├── template.py
│   ├── bindings.py
│   ├── source_map.py
│   ├── loader.py
│   └── runtime.py              # emitted-code helpers
├── components/
│   ├── __init__.py
│   ├── composition.py
│   ├── lifecycle.py
│   ├── boundaries.py
│   ├── errors.py
│   ├── elements.py
│   ├── schema.py
│   ├── native.py               # generated native signatures
│   └── native_support.py
├── styling/
│   ├── __init__.py
│   ├── styled.py
│   ├── styled_native.py        # generated styled signatures
│   ├── styles.py
│   └── scoped_css.py
├── rendering/
│   ├── __init__.py
│   ├── render.py               # HTML and reactive watchers
│   ├── tree.py
│   ├── identity.py
│   └── output.py
├── runtime/
│   ├── __init__.py
│   ├── session.py
│   ├── connection.py
│   ├── async_runtime.py
│   ├── resources.py
│   ├── events.py
│   ├── dom.py
│   ├── forms.py
│   ├── routing.py
│   ├── wire.py
│   ├── transport.py
│   ├── capsule.py              # bounded authenticated browser-held state
│   ├── persistence.py
│   ├── durable.py
│   └── ownership.py
├── hosting/
│   ├── __init__.py
│   ├── hosting.py              # application, configuration and standalone host
│   ├── server.py               # application CLI
│   ├── asgi.py
│   ├── http.py
│   ├── request.py
│   ├── security.py
│   ├── browser_recovery.py     # publication and bounded graph reconstruction
│   ├── recovery_keys.py        # host-owned recovery key ring
│   ├── durable_host.py
│   ├── telemetry.py
│   ├── _http_connection.py      # shared HTTP parser, loaded by active adapters
│   └── _uvicorn.py
├── development/
│   ├── __init__.py
│   ├── development.py          # watch supervisor
│   ├── control.py
│   ├── diagnostics.py
│   └── tracing.py
├── tooling/
│   ├── __init__.py
│   ├── tools.py                # selected-environment tool resolution
│   ├── sources.py              # shared source discovery and decoding
│   ├── check.py
│   ├── diagnostic_rules.py
│   ├── analysis.py
│   ├── lint.py
│   ├── quality.py
│   ├── build.py
│   ├── editor/
│   │   ├── __init__.py
│   │   ├── model.py
│   │   ├── types.py
│   │   ├── config.py
│   │   └── ownership.py
│   └── lsp/
│       ├── __init__.py
│       ├── lsp.py
│       ├── server.py
│       ├── backend.py
│       ├── workspace.py
│       ├── features.py
│       └── edits.py
└── static/                     # browser client and development inspector
```

Use a subsystem's named implementation module for its coordinating behavior, such
as `compiler/compiler.py` or `development/development.py`. Supporting files describe
their responsibility. A package with several independent owners, such as
`components/`, needs no additional coordinator merely to repeat the folder name.

`pysx/__init__.py` is the only Python file directly under `pysx/` and the only
initializer that exports anything. It assembles the public application API from the
canonical owners above, so `from pysx import ...` stays the application import.
Subsystem initializers declare responsibility and export nothing, which leaves every
other symbol reachable by exactly one path: code and commands name the owning module,
as in `pysx.rendering.render`, `pysx.components.native`,
`python -m pysx.hosting.server` and `python -m pysx.tooling.tools`. Functions, classes
and mutable state have one implementation owner, and no module re-exports another
module's behavior. The shared HTTP parser loads with active host adapters; importing
the rendering API does not load the network stack.

Shared behavior belongs with its existing responsible owner. For example, source
enumeration and encoding detection live in `tooling/sources.py`, while coordinate
mapping lives in `compiler/source_map.py`. Use focused names instead of collecting
unrelated behavior in a generic helpers module.

`scripts/generate_native.py` owns both generated signature files. Browser resources
remain under `pysx/static/` and are loaded from the installed package root. The VSCode
extension stays in `editor/`; examples and verification stay in `examples/` and
`tests/`. Generated output and fixtures do not determine new implementation
ownership.
