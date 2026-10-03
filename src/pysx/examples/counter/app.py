from pysx import button, component, div, html, signal, styled

Page = styled(div, t"""
    font: 16px/1.5 system-ui, sans-serif;
    display: flex;
    flex-direction: column;
    gap: 1rem;
    align-items: flex-start;
    padding: 2rem;
""")

Action = styled(button, t"""
    border: none;
    border-radius: 999px;
    padding: 0.6rem 1.4rem;
    font: inherit;
    font-weight: 600;
    color: white;
    background: #6c63ff;
    cursor: pointer;
""")


@component
def app():
    # Created per session. A module-scope signal would be shared by every
    # connected client.
    count = signal(0)

    return html(t"""
        Page(id="container"):
            "Count: " {count}
            Action(type="button", onClick={(lambda e: count.set(count() + 1))}):
                "Increment"
    """)
