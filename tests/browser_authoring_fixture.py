"""Scoped nesting and bounded reactive variant acceptance fixture."""

from pysx import Fragment, Themes, html, signal, styled

Action = styled.button(
    t"""
    color: var(--ink);
    padding: 4px;
    &:hover:
      border-color: rgb(0, 128, 0);
    &:focus-visible:
      outline: 3px solid rgb(128, 0, 128);
    &:active:
      border-width: 5px;
    &[data-look="sample"]:
      border-style: solid;
    @media (max-width: 600px):
      padding: 12px;
""",
    variants={"primary": t"background: white", "ghost": t"background: black"},
)

Panel = styled.section(t"""
    span:
      color: rgb(0, 128, 0);
""")


def app() -> Fragment:
    themes = Themes()
    themes.register("light", {"ink": "rgb(255, 0, 0)"})
    themes.register("dark", {"ink": "rgb(0, 0, 255)"})
    themes.select("light")
    mode = signal("primary")

    def toggle(_event: object) -> None:
        mode.set("ghost" if mode() == "primary" else "primary")
        themes.select("dark")

    return html(
        t"""
        Panel:
          span: "Scoped descendant"
          Action(id="sample",data-look="sample",variant={mode},onClick={toggle}): "Switch"
        span(id="outside"): "Outside"
    """,
        themes=themes,
    )
