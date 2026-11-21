# Changelog

## 0.7.1 Release

- Improve VS Code setup with managed developer tooling, reversible template-aware
  import settings, and command-line extension installation and removal.
- Correct nested-project analysis, interpreter selection, syntax highlighting and
  navigation. Make explicit editor refresh rebuild unchanged projections.
- Consolidate managed application state under a configurable, namespaced `.pysx/`
  directory, separating persistent data from temporary files and safely migrating
  existing recovery keys. Keep playground stores outside installed packages.
- Add a runnable nginx deployment example and improve mounted-application proxy
  configuration and cross-platform tooling support.
- Add Python browser behaviors for connection UI, nested-panel scrolling and
  viewport reveals, replacing custom JavaScript helpers in the tutorial.
- Improve reconnect, language-server and editor save diagnostics. Add public
  documentation, changelog and issue links to package metadata.

## 0.7.0 Release

Correct the PyPI publication retry check to use `pysx-framework`. Expand the
framework and VS Code extension installation instructions and library description.
Align the framework, extension and tutorial versions at 0.7.0.

## 0.1.0 Release

Initial release. pysx renders reactive user interfaces from Python on the server.
Templates are PEP 750 t-strings containing an indentation-based markup language.
Reactive state lives on the server, one graph per connection: browser events travel
up, and minimal DOM patches travel down over a websocket.

This release provides the reactive signal and structured-state surface, the template
language with control flow and keyed rendering, typed HTML elements and form bindings,
browser events and bounded DOM commands, styled components with themes, routing and
navigation, error boundaries and session recovery, server-side rendering with ASGI
integration, durable state with worker ownership, and hosting with health, draining
and session isolation.

Tooling includes a language server providing diagnostics, completion, hover and
navigation, an editor extension with syntax highlighting and advisory diagnostics,
and a development server with state-preserving hot reload and an optional inspector.

The standalone host needs only the base package. Optional extras install Uvicorn
(`asgi`), the language server (`lsp`), the pinned formatter (`dev`) and the bundled
example launcher (`examples`). Python 3.14 or newer is required. Distributed under
the MIT license.

See [compatibility and support](docs/COMPATIBILITY.md) for the supported surface,
runtime requirements and maintenance policy.
