"""Local fixture identities, private session state and explicit write authorization."""

from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from pysx import (
    AccessDenied,
    Application,
    Fragment,
    HostConfig,
    SecurityPolicy,
    current_request,
    pysx,
    require_access,
    signal,
)

from .components import (
    Action,
    Actions,
    Card,
    Description,
    Eyebrow,
    Field,
    Page,
    Title,
    Value,
    example_app,
    example_title,
)

if TYPE_CHECKING:
    from pysx import RequestContext


def fixture_principal(request: RequestContext) -> str | None:
    query = parse_qs(urlsplit(request.path).query)
    location = query.get("location", [request.path])[0]
    identity = parse_qs(urlsplit(location).query).get("principal", ["alice"])[0]

    return identity if identity in ("alice", "reader") else None


def authorize(request: RequestContext, action: str, target: str) -> bool:
    return action != "write" or (request.principal == "alice" and target == "counter")


def view() -> Fragment:
    identity = current_request().principal
    example_title("Session security")
    count = signal(0)
    draft = signal("My private draft")
    status = signal("Ready")

    def write(_event: object) -> None:
        try:
            require_access("write", "counter")
        except AccessDenied:
            status.set("Denied: this identity has read-only access")

            return
        count.update(lambda value: value + 1)
        status.set("Allowed: your counter was updated")

    return pysx(t"""
        Page:
            header:
                Eyebrow: "pysx / examples"
                Title: "Session security"
                Description: "Choose a local demonstration identity and try a write."
            Card:
                h2: "Local fixture identity"
                p(id="security-principal"): {identity}
                nav(ariaLabel="Choose demonstration identity"):
                    a(href="/?principal=alice", id="choose-writer"): "Writer"
                    " · "
                    a(href="/?principal=reader", id="choose-reader"): "Read-only"
            Card:
                h2: "Your private session"
                label(htmlFor="security-draft"): "Draft"
                Field(id="security-draft", bindValue={draft})
                p(id="security-echo"): {draft}
                p:
                    "Counter: "
                    Value(id="security-count"): {count}
                Actions:
                    Action(id="security-write", type="button", onClick={write}): "Attempt write"
                p(id="security-result", role="status"): {status}
                Description:
                    "Open another tab: its draft and counter start independently. "
                    "The read-only identity can edit its own draft "
                    "and receives a visible denial for writes."
    """)


app = Application(
    view,
    config=HostConfig(
        principal=fixture_principal,
        security=SecurityPolicy(require_principal=True, authorize=authorize),
    ),
)


example = example_app(app)
