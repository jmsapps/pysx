from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from pysx import (
    Fragment,
    Signal,
    component,
    derived,
    each,
    html,
    signal,
)

from .components import Checkbox as Checkbox
from .components import Clear as Clear
from .components import CompactPage as CompactPage
from .components import Description as Description
from .components import Eyebrow as Eyebrow
from .components import Field as Field
from .components import FilterButton as FilterButton
from .components import Filters as Filters
from .components import Form as Form
from .components import Item as Item
from .components import List as List
from .components import Meta as Meta
from .components import Remove as Remove
from .components import Row as Row
from .components import Submit as Submit
from .components import Text as Text
from .components import Title as Title

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class Todo:
    id: int
    text: str
    done: bool


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
        CompactPage:
            header:
                Eyebrow: "pysx / examples"
                Title: "pysx Todos"
                Description: "A little space to organize what comes next."

            Form(onSubmit={add}):
                Field(type="text", placeholder="What needs doing?", bindValue={draft})
                Submit(type="submit"): "Add"

            Meta(id="todo-summary"):
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
