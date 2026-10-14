"""CSP-compatible SSR, adoption and live private styles with hostile text."""

from pysx import Application, Fragment, HostConfig, StaticAsset, native, signal

PAYLOAD = '"</script><img src=x onerror=window.injected=true> & 雪'


def app() -> Fragment:
    text = signal(PAYLOAD)
    color = signal("color:rgb(200, 0, 0)")
    variable = signal("5px")

    def update(_event: object) -> None:
        text.set(PAYLOAD + " updated")
        color.set("color:rgb(0, 0, 200)")
        variable.set("11px")

    return native.Main(
        native.Link(rel="stylesheet", href="/site.css"),
        native.Script("window.injected = true;"),
        native.Iframe(srcdoc=PAYLOAD, id="inert-frame"),
        native.P(text, title=text, css=color, style_vars={"gap": variable}, id="private"),
        native.Button("Update", on_click=update, id="update"),
    )


host = Application(
    app,
    config=HostConfig(
        assets={
            "/site.css": StaticAsset(b"body{background-color:rgb(240, 240, 240)}", "text/css"),
        }
    ),
)
