# Compatibility and support

The current package is a pre-1.0 candidate. The following contract applies from
1.0.0; the version is available through `importlib.metadata.version("pysx")`.

## Public surface

The [API index](API.md) defines supported root exports, including typed native
elements through `pysx.native`. The linked authoring, hosting, configuration,
resource, state, wire and editor contracts define their supported behavior.
Documented commands and configuration are public. Other implementation modules,
private names and generated compiler output are internal. Applications should
import public APIs from `pysx` and use the documented entry points.

Patch releases preserve public behavior and signatures while fixing defects.
Minor releases may add backward-compatible features. Breaking public changes
require a major release. Supported deprecations receive warnings and migration
documentation for at least one minor release and six months before removal;
removal also requires a major release. See the [migration guide](MIGRATION.md).

## Runtime and tools

Python 3.14 is the minimum runtime because templates use PEP 750 t-strings.
Python 3.14.10 is the currently tested interpreter. Later Python versions require
qualification before being described as tested. Durable state in WAL mode needs an
interpreter whose bundled SQLite carries the WAL-reset fix; an older 3.14 patch
release meets the Python floor and still refuses WAL. The [ownership
guide](OWNERSHIP.md) states the exact SQLite versions. The quality matrix has passed on
Ubuntu, macOS and Windows runners. This is functional evidence, not a claim that
every OS release or production deployment topology has been tested. Linux container
deployment needs a Linux container runtime; see [deployment](DEPLOYMENT.md).

The standalone host needs the base package. Install `asgi` for Uvicorn, `dev` for
the pinned Ruff formatter, `lsp` for the language server and its analysis tools,
and `examples` for the bundled Typer launcher. Extras are independent; a base
installation does not install Ruff, Pyright, Pygls, Uvicorn or Typer.
The editor's supported VSCode range is declared by `engines.vscode` in its
manifest; [editor setup](../editor/README.md) describes the required Python and
Pylance extensions. Tool versions used by the repository are pinned in its lockfiles.

## Maintenance window

The latest minor release receives fixes. After a new minor release, the preceding
minor receives security fixes for six months; users should use its latest patch.
A major release's final minor receives security fixes for six months after the
next major release. Older releases are unsupported. This is a maintenance policy,
not a response-time or availability guarantee.

Report ordinary defects through the repository issue tracker. Report suspected
vulnerabilities privately using the [security reporting instructions](SECURITY.md#reporting).
The project is distributed under the [MIT license](../LICENSE), copyright JMS Apps.
