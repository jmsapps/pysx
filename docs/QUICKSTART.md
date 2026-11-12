# Start an application

Save this complete application as `my_app.py`:

```python
from pysx import Fragment, pysx, signal

def app() -> Fragment:
    count = signal(0)

    def increment(_event: object) -> None:
        count.update(lambda value: value + 1)

    return pysx(t'''
        main:
            h1: "My first live page"
            p: "Count: " {count}
            button(type="button" onClick={increment}): "Increment"
    ''')
```

Run it:

```sh
pysx --app my_app:app
```

Open http://127.0.0.1:8750, increment twice, then open another tab: the second
tab starts at zero. Refresh starts fresh too. Ctrl+C stops the server. Add
`--watch` for compatible state preservation while editing. The default host binds
to loopback.

That is the whole project. There is no scaffold to generate, no build step and no
JavaScript toolchain.

## Install

Python 3.14 or newer is required for PEP 750 t-strings.

```sh
uv init --python 3.14
uv add 'pysx==0.1.0'
```

Prefix the run command with `uv run --project .` when working inside that project.

For ASGI and editor tooling, install with the extras `pysx[asgi,lsp]==0.1.0`.

For Python formatting alone, install `pysx[dev]==0.1.0` in the same environment
and run `python -m ruff format my_app.py`. This uses the pinned Ruff shipped by
the extra; editor formatting uses the selected interpreter's tools too.

### Installing a candidate build

A candidate that has not been published is installed from a local artifact
directory instead:

```sh
uv add /path/to/artifacts/pysx-0.1.0-py3-none-any.whl
```

Build those artifacts from a checkout with `uv build`. Do not assume that an
unpublished candidate is on the package index; install an exact version when using
a published one.

### Run a bundled example outside the checkout

Create a fresh environment in an empty directory, then install an exact artifact:

```sh
uv venv --python 3.14
uv pip install '/path/to/artifacts/pysx-0.1.0-py3-none-any.whl[examples]'
uv run --no-project pysx-example run counter
```

Open http://127.0.0.1:8750 and click Increment: the count changes from 0 to 1.
Ctrl+C stops the server. The wheel includes the examples' shared components and
assets. The same command works after building a wheel from the source archive.
The checkout launcher `uv run --project . python run_example.py run counter`
remains available. See [compatibility](COMPATIBILITY.md) for optional tool extras.

## Next

The [editor setup](../editor/README.md) covers installation of the extension,
Python selection and advisory diagnostics. The [tutorial](TUTORIAL.md) provides
checked exercises; the [example guide](../examples/README.md) provides checkout
launch commands and interactions. See [hosting](HOSTING.md) for assets, base
paths, HTTP and ASGI configuration, and [deployment](DEPLOYMENT.md) before remote
exposure.
