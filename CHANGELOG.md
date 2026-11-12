# Changelog

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
