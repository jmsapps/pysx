"""Port of ntml/examples/todos.nim."""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from pysx import (
    Fragment,
    Signal,
    component,
    derived,
    each,
    global_style,
    html,
    signal,
    styled,
)

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class Todo:
    id: int
    text: str
    done: bool


global_style("""
    :root {
      background: #0f172a;
      font-family: 'Inter', system-ui, sans-serif;
    }
    body {
      margin: 0;
      min-height: 100vh;
      display: flex;
      align-items: flex-start;
      justify-content: center;
      padding: 48px 16px;
      background: #0f172a;
    }
    .is-active { background: rgba(56, 189, 248, 0.25) !important; color: #e0f2fe; }
    .is-done .todo-text { text-decoration: line-through; color: rgba(148,163,184,0.6); }
""")

App = styled(
    "div",
    t"""
    width: min(480px, 100%);
    background: #0b1120;
    color: #e2e8f0;
    border-radius: 18px;
    box-shadow: 0 20px 55px rgba(15, 23, 42, 0.35);
    padding: 28px 32px;
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
""",
)

Title = styled(
    "h1",
    t"""
    margin: 0;
    font-size: 1.85rem;
    letter-spacing: -0.02em;
""",
)

Form = styled(
    "form",
    t"""
    display: flex;
    gap: 0.75rem;
    align-items: center;
""",
)

Field = styled(
    "input",
    t"""
    flex: 1;
    border-radius: 999px;
    border: none;
    padding: 0.8rem 1.1rem;
    background: rgba(148, 163, 184, 0.16);
    color: inherit;
    font-size: 1rem;
""",
)

Submit = styled(
    "button",
    t"""
    border: none;
    border-radius: 999px;
    padding: 0.75rem 1.4rem;
    font-weight: 600;
    background: linear-gradient(135deg, #2563eb, #38bdf8);
    color: #fff;
    cursor: pointer;
""",
)

Meta = styled(
    "p",
    t"""
    margin: 0;
    color: rgba(148, 163, 184, 0.85);
""",
)

Filters = styled(
    "nav",
    t"""
    display: flex;
    gap: 0.5rem;
""",
)

FilterButton = styled(
    "button",
    t"""
    flex: 1;
    border: none;
    border-radius: 999px;
    padding: 0.55rem 0.75rem;
    background: rgba(148, 163, 184, 0.15);
    color: inherit;
    cursor: pointer;
""",
)

List = styled(
    "ul",
    t"""
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
""",
)

Item = styled(
    "li",
    t"""
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: rgba(15, 23, 42, 0.6);
    border-radius: 14px;
    padding: 0.65rem 0.85rem 0.65rem 1rem;
    gap: 0.75rem;
""",
)

Row = styled(
    "label",
    t"""
    display: flex;
    align-items: center;
    gap: 0.75rem;
    flex: 1;
""",
)

Checkbox = styled(
    "input",
    t"""
    width: 18px;
    height: 18px;
    accent-color: #38bdf8;
""",
)

Text = styled(
    "span",
    t"""
    flex: 1;
""",
)

Remove = styled(
    "button",
    t"""
    border: none;
    cursor: pointer;
    background: transparent;
    color: rgba(148, 163, 184, 0.8);
    font-size: 0.9rem;
    padding: 0.2rem 0.3rem;
""",
)

Clear = styled(
    "button",
    t"""
    align-self: flex-end;
    border: none;
    background: rgba(248, 113, 113, 0.2);
    color: #fecaca;
    padding: 0.55rem 0.95rem;
    border-radius: 999px;
    cursor: pointer;
""",
)


@component
def app() -> Fragment:
    todos = signal(
        [
            Todo(1, "Wire up signals", False),
            Todo(2, "Compose DOM with templates", False),
            Todo(3, "Ship something reactive", True),
        ]
    )
    draft = signal("")
    mode: Signal[Literal["all", "active", "completed"]] = signal("all")
    next_id = signal(4)

    remaining = derived(lambda: sum(1 for todo in todos() if not todo.done))
    plural = derived(lambda: remaining() != 1)
    has_completed = derived(lambda: any(todo.done for todo in todos()))

    def visible() -> list[Todo]:
        items = todos()
        if mode() == "active":
            return [todo for todo in items if not todo.done]
        if mode() == "completed":
            return [todo for todo in items if todo.done]
        return items

    filtered = derived(visible)

    def filter_class(name: Literal["all", "active", "completed"]) -> Signal[str]:
        return derived(lambda: "is-active" if mode() == name else "")

    def add(_e: object) -> None:
        text = draft().strip()
        if not text:
            return
        todos.set([*todos(), Todo(next_id(), text, False)])
        draft.set("")
        next_id.set(next_id() + 1)

    def toggle(todo_id: int) -> None:
        todos.set(
            [
                replace(todo, done=not todo.done) if todo.id == todo_id else todo
                for todo in todos()
            ]
        )

    def remove(todo_id: int) -> None:
        todos.set([todo for todo in todos() if todo.id != todo_id])

    def clear_completed(_e: object) -> None:
        todos.set([todo for todo in todos() if not todo.done])

    def set_mode(name: Literal["all", "active", "completed"]) -> Callable[[object], None]:
        def handler(_e: object) -> None:
            mode.set(name)
        return handler

    def TodoItem(todo: Todo) -> Fragment:
        def toggle_item(_e: object) -> None:
            toggle(todo.id)

        def remove_item(_e: object) -> None:
            remove(todo.id)

        return html(t"""
            Item(class={"is-done" if todo.done else ""}, data-done={str(todo.done).lower()}):
                Row:
                    Checkbox(type="checkbox", checked={todo.done}, onChange={toggle_item})
                    Text(class="todo-text"): {todo.text}
                Remove(type="button", onClick={remove_item}):
                    "Remove"
        """)

    return html(t"""
        App:
            Title: "pysx Todos"

            Form(onSubmit={add}):
                Field(type="text", placeholder="What needs doing?", bindValue={draft})
                Submit(type="submit"): "Add"

            Meta:
                strong: {remaining}
                " item"
                if {plural}:
                    "s"
                " left"

            Filters:
                FilterButton(type="button", class={filter_class("all")}, onClick={set_mode("all")}):
                    "All"
                FilterButton(type="button", class={filter_class("active")}, onClick={set_mode("active")}):
                    "Active"
                FilterButton(type="button", class={filter_class("completed")}, onClick={set_mode("completed")}):
                    "Completed"

            List(id="todo-list"):
                {each(filtered, TodoItem, key=(lambda todo: todo.id))}

            if {has_completed}:
                Clear(type="button", onClick={clear_completed}):
                    "Clear completed"
    """)
