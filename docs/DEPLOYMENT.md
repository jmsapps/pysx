# Two-worker Linux deployment

The [reference Compose topology](../deploy/compose.yaml) runs standalone and ASGI
workers, nginx with TLS, and a same-host SQLite state/ownership volume. Images are
digest-pinned: Python 3.14.4, uv 0.11.8 and nginx 1.30.0. Dependencies use the frozen
lockfile. Prerequisites are a Linux Docker engine, Compose v2 and OpenSSL. Docker
Desktop supplies a Linux engine on other hosts. Worker ports are private.

Your packaged module exports a `DurableApplication`. Read `PYSX_STATE_PATH` for
`SQLiteOwnershipStore`, `PYSX_WORKER` for the unique worker identifier, and
`PYSX_ORIGIN` for the allowed HTTPS origin. Keep the application namespace stable.
Trust only the private proxy network, use secure cookies, and supply your own
principal resolver and authorization policy. Production mode requires these hooks;
see [security](SECURITY.md). Shared account invariants belong in authenticated
application-service transactions, rather than worker globals.

The image includes shipped examples. For your own module, add its directory to
`deploy/Dockerfile` and the `.dockerignore` allowlist. Never copy private keys,
credentials or a development environment. Included test fixtures are acceptance
inputs, not production authentication examples. Select your application explicitly.

## Start

From the repository root, set `PYSX_APP` to your packaged `module:host`, then:

```sh
export PYSX_ORIGIN=https://127.0.0.1:8443
export PYSX_PORT=8443
export PYSX_TLS_CERT=/absolute/path/to/cert.pem
export PYSX_TLS_KEY=/absolute/path/to/key.pem
docker compose -f deploy/compose.yaml up -d --build
docker compose -f deploy/compose.yaml ps
curl --cacert "$PYSX_TLS_CERT" "$PYSX_ORIGIN/health"
```

Use a certificate valid for the public host. For disposable local testing, generate
a one-day certificate at the configured paths (not a trusted production CA):

```sh
openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -subj /CN=localhost -addext 'subjectAltName=IP:127.0.0.1,DNS:localhost' \
  -keyout "$PYSX_TLS_KEY" -out "$PYSX_TLS_CERT"
```

The published listener binds to loopback. Exposing a server requires deliberately
changing that binding, configuring its certificate/origin, and restricting access
to the Docker socket and private worker network. Only the proxy should reach worker
ports. It replaces supplied forwarding headers with trusted host/scheme values.

Nginx maps only `worker-a` and `worker-b` hints to configured upstream groups;
unknown hints use the default pool. A failed/draining primary has one backup.
Connect timeout is one second; retries are capped at two attempts/two seconds.
HTTP 503 before upgrade can select the backup. Once upgraded, the browser owns
reconnect/reconstruction; nginx does not replay events. See nginx's
[WebSocket](https://nginx.org/en/docs/http/websocket.html) and
[retry](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_next_upstream)
contracts. Read timeout is 65 seconds, above the worker's 20-second ping interval;
write timeout is five seconds. Buffering, caching and access logging are disabled.
Recovery credentials remain in hello bodies, never routing URLs.

## Health and metrics

`/health` reports liveness. Worker `/_pysx/ready` performs one bounded ownership-store
probe and returns 503 for store failure, draining, closed lifecycle or exhausted
graph capacity. Probes never revive expired leases. Compose checks worker readiness
internally; an unhealthy container is not automatically restarted or removed.
Nginx uses bounded passive failover, and your supervisor must inspect health.

`/_pysx/metrics` returns fixed numeric samples, all prefixed `pysx_durable_`:
`ready`, `store_available`, `draining`, `graphs`, `capacity`, `pending`, `detached`,
`reservations`, `connections`, `handshakes`, `fenced_total`, `store_failures_total`.
Counters cover renewal/probe failures and fenced graphs, resetting on process
restart. No principal/session/worker identifier, path, credential or payload appears.
`host.operational_status()` supplies the same numeric observations without a fresh
store probe. The `/_pysx/` prefix is reserved; nginx returns 404 for it. Scrape through
the private network or an authenticated admin service. Do not expose worker ports.

Readiness is an unauthenticated probe, like `/health`: the container health check
reaches it over plain HTTP on the loopback. Metrics are not. They run the host's
`prepare()` and `access` hook, so a `SecurityPolicy` that denies the `http` action,
or a `production` host reached over plain HTTP, returns 403 instead of counters.
Both probes are observed and carry `x-request-id`.

```sh
docker compose -f deploy/compose.yaml exec -T worker-a uv run --project . --no-dev python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8750/_pysx/ready', timeout=2).read().decode())"
```

## Capacity and store policy

Default capacity is 100 graphs per worker, including pending SSR and detached owners;
the detached cap is 50. Keep headroom: 50 attached sessions on each worker lets the
survivor reconstruct 100 sessions. At 100+100, worker loss requires replacement
capacity; admission refuses excess sessions. Storage does not create RAM capacity.
Recovery acceptance includes 100 keyed editable rows, routes, themes, owned effects
and an async resource, with a ten-second recovery bound for 50 lost sessions with
headroom. Sustained performance and resource measurements require qualification on
your deployment hardware; this is not a promise for arbitrary applications.

Per-worker defaults bound events/mutation queues at 64, socket queues at 16, owned
tasks at 1024, frames at 64 KiB and outbox at 2 MiB. Hello/send/close deadlines are
five/two/two seconds. [Browser retries](PROTOCOL.md) are bounded. Codec payloads and
SQLite busy timeout (default 0.1s, maximum 2s) bound checkpoints; there is no infinite
event retry. Failed checkpoints withhold publication and fence graphs. The default
three-second lease renews every second; measure scheduling/store latency before
shortening it. External business effects need transactions and idempotency.

SQLite is a same-host reference adapter requiring a local filesystem with working
locks and a shared clock. Do not use NFS or infer multi-host replication. Distributed
deployments need their own atomic `OwnershipStore` implementation and qualification.

## Drain, restart and backup

`--drain-signal` enables Linux SIGUSR1 to call `begin_drain()`: readiness becomes 503,
new graphs are refused, and existing owners keep renewing and processing events.
Then replace the worker within your chosen drain window. `close()` bounds socket/task
shutdown and releases leases; abrupt death leaves leases to expire.

```sh
docker compose -f deploy/compose.yaml kill -s SIGUSR1 worker-a
# Inspect readiness and allow your drain window, then replace this worker.
docker compose -f deploy/compose.yaml restart worker-a
# Wait for readiness before draining worker-b.
```

Deploy compatible readers before changing schemas; supply migrations and retain a
tested backup. Follow the [incompatibility policy](OWNERSHIP.md). Never run two
processes with one worker identifier. Rollback may require a compatible reader or
an offline restore, rather than automatic state downgrade.

Use SQLite's online backup API for a consistent copy while workers run:

```sh
docker compose -f deploy/compose.yaml exec -T worker-a uv run --project . --no-dev python -c \
  "import sqlite3; s=sqlite3.connect('/state/state.sqlite3'); d=sqlite3.connect('/state/backup.sqlite3'); s.backup(d); d.close(); s.close()"
docker compose -f deploy/compose.yaml cp worker-a:/state/backup.sqlite3 ./backup.sqlite3
```

Backups contain private app data. Apply retention to snapshots and revocation
tombstones; test restore offline and stop all writers before replacing the database.
Preserve epochs and revocations. `docker compose down` retains the volume;
`down --volumes` intentionally deletes it.

## Executable qualification

With the prerequisites above:

```sh
uv run --project . pytest -q tests/test_deployment.py -k operational_topology
npm --prefix tests run browser -- --suite operational_topology
```

The harness builds the shipped configuration, supplies disposable TLS materials,
starts/cleans both Linux adapters and nginx, and removes its own containers, network
and volume. Existing deployment volumes are untouched. Chromium, Firefox and WebKit
cover deep-link SSR/adoption, resources/async updates, live resume, drain, owner loss
and restart. Ownership tests also exercise headroom/full-capacity profiles. Record
capacity evidence with the full manifest running alone: concurrent load generators
can change scheduling and lease results.
