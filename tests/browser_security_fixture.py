"""Local identities for exercising both network adapters; no credential provider."""

from http.cookies import SimpleCookie

from pysx import (
    Application,
    AuthenticatedIdentity,
    Fragment,
    HostConfig,
    RequestContext,
    SecurityPolicy,
    current_request,
    native,
    signal,
)


def principal(request: RequestContext) -> AuthenticatedIdentity | None:
    cookies = SimpleCookie()
    cookies.load(request.headers.get("cookie", ""))
    value = cookies.get("fixture_user")

    if value is None or value.value not in ("alice", "bob"):
        return None
    generation = cookies["fixture_login"].value if "fixture_login" in cookies else ""

    return AuthenticatedIdentity(value.value, generation or "initial")


def authorize(request: RequestContext, action: str, target: str) -> bool:
    return not (
        request.principal == "bob" and action == "event" and target not in ("mounted", "location")
    )


def app() -> Fragment:
    count = signal(0)

    return native.Main(
        native.P(current_request().principal, id="principal"),
        native.P(count, id="count"),
        native.Button("Increment", id="increment", on_click=lambda _event: count.set(count() + 1)),
    )


host = Application(
    app,
    config=HostConfig(
        principal=principal, security=SecurityPolicy(require_principal=True, authorize=authorize)
    ),
)
