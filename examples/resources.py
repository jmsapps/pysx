"""HTTP content, graph adoption and independent asynchronous updates."""

import asyncio

from pysx import (
    Fragment,
    derived,
    on_mount,
    on_setup,
    own_timer,
    push,
    pysx,
    resource,
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
    SecondaryAction,
    Title,
    example_app,
    example_title,
)


def app() -> Fragment:
    example_title("Async resources")
    draft = signal("Keep this draft")
    setups = signal(0)
    mounts = signal(0)
    delivered = signal(0)
    notice = signal("Waiting for a server update")
    requests = 0
    on_setup(lambda: setups.update(lambda count: count + 1))
    on_mount(lambda: mounts.update(lambda count: count + 1))
    own_timer("notice", 0.25, lambda: notice.set("Server update received"))

    async def load() -> str:
        nonlocal requests
        requests += 1
        generation = requests
        await asyncio.sleep(0.3)
        await push(lambda: delivered.update(lambda count: count + 1))

        return f"Result {generation}"

    value = resource("message", load)
    status = derived(lambda: value().status)
    message = derived(lambda: value().value or "No result yet")
    cancellable = derived(lambda: value().status != "pending")

    def reload(_: object) -> None:
        value.reload()

    def cancel(_: object) -> None:
        value.cancel()

    cancel_button = SecondaryAction(
        "Cancel load", id="cancel-resource", type="button", disabled=cancellable, on_click=cancel
    )

    return pysx(t"""
        Page:
            header:
                Eyebrow: "pysx / examples"
                Title: "Async resources"
                Description:
                    "Wait without clicking: the message and server update arrive by themselves."
            Card:
                h2: "Your draft"
                label(htmlFor="resource-draft"): "Draft text"
                Field(id="resource-draft", bindValue={draft})
                p(id="resource-echo"): {draft}
                Description: "Edits stay with this tab while background work completes."
            Card:
                h2: "Asynchronous message"
                p:
                    "State: "
                    span(id="resource-status"): {status}
                p(id="resource-value", ariaLive="polite"): {message}
                Actions:
                    Action(id="reload-resource", type="button", onClick={reload}): "Reload message"
                    {cancel_button}
                Description: "Reload starts fresh work. Cancel stops a pending load."
            Card:
                h2: "Server updates"
                p(id="server-notice", ariaLive="polite"): {notice}
                p:
                    "Delivered messages: "
                    span(id="resource-delivered"): {delivered}
                p:
                    "Server setup: "
                    span(id="resource-setups"): {setups}
                    " | Browser mount: "
                    span(id="resource-mounts"): {mounts}
                Description:
                    "The browser attaches to the page already rendered on the server. "
                    "Setup runs once."
    """)


example = example_app(app)
