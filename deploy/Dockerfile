FROM ghcr.io/astral-sh/uv:0.11.8@sha256:3b7b60a81d3c57ef471703e5c83fd4aaa33abcd403596fb22ab07db85ae91347 AS uv
FROM python:3.14.4-slim-bookworm@sha256:fc74d22ffd0d5ac395a4b7bdda75a4539758862c49ebf3005647084631e63789
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra asgi --no-install-project
COPY pysx ./pysx
COPY examples ./examples
COPY tests ./tests
COPY run_example.py ./
RUN uv sync --frozen --no-dev --extra asgi
ENV UV_NO_SYNC=1 UV_PYTHON_DOWNLOADS=never
ENTRYPOINT ["uv", "run", "--project", ".", "--no-dev", "python", "-m", "pysx.hosting.server"]
