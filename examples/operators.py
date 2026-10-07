"""Every supported operator spelling, grouped by what Python lets an operator return."""

from pysx import (
    Fragment,
    all_of,
    any_of,
    batch,
    component,
    concat,
    contains,
    derived,
    dict_key,
    eq,
    ge,
    gt,
    html,
    inclusive_range,
    le,
    length,
    list_index,
    lt,
    ne,
    not_,
    signal,
    structured,
)

from .components import (
    Action,
    Actions,
    Card,
    Description,
    Eyebrow,
    Page,
    Reading,
    Result,
    Title,
)


@component
def app() -> Fragment:
    count = signal(2)
    name = signal("ada")
    # Separate signals keep each payload type concrete, so projections stay typed.
    profile = structured({"scores": {"first": 10}})
    tags = structured(["alpha", "beta"])

    scores = dict_key(profile, "scores")
    first = dict_key(scores, "first")
    head = list_index(tags, 0)

    # --- Tier 1: Python permits a reactive return, so these are real operators.
    doubled = count * 2
    nested = profile["scores"]["first"]
    positional = tags[0]

    # --- Tier 2: Python forces a primitive from these protocols. Named helpers only;
    # there is no faithful `and`/`or`/`not`/`in`/`len` overload to write.
    big = gt(count, 2)
    named = eq(name, "ada")
    both = all_of(big, named)
    either = any_of(big, not_(named))
    negated = not_(both)
    tagged = contains(tags, "alpha")
    in_range = contains(inclusive_range(1, 5), count)
    tag_count = length(tags)
    greeting = concat("hello ", name)

    # `==` stays Python identity so Signals remain usable as dict/set keys and in
    # ordinary asserts; eq()/ne() are the reactive payload comparisons.
    differs = ne(name, "grace")

    # --- Tier 3: ordering is overloaded because it has no competing meaning.
    over = count > 1
    under = count < 9
    at_least = count >= 2
    at_most = count <= 2
    # The same comparisons are available as named functions.
    over_named = gt(count, 1)
    under_named = lt(count, 9)
    at_least_named = ge(count, 2)
    at_most_named = le(count, 2)

    # --- The default for anything else: a lambda gives real Python semantics,
    # including genuine short-circuiting. Only the sources actually read are tracked.
    short_circuit = derived(lambda: count() > 1 and name() == "ada")
    lambda_not = derived(lambda: not (count() > 2))

    def advance(_e: object) -> None:
        count.set(count() + 1)

    def rename(_e: object) -> None:
        name.set("grace" if name() == "ada" else "ada")

    def bump_nested(_e: object) -> None:
        first.set(first() + 1)

    def rotate(_e: object) -> None:
        values = tags()
        tags.set([*values[1:], values[0]])

    def toggle_tag(_e: object) -> None:
        # Moves contains() and length() together; rotation alone changes neither.
        tags.set(["beta"] if "alpha" in tags() else ["alpha", "beta"])

    def reset(_e: object) -> None:
        with batch():
            count.set(2)
            name.set("ada")
            profile.set({"scores": {"first": 10}})
            tags.set(["alpha", "beta"])

    return html(
        t"""
        Page(id="container"):
            header:
                Eyebrow: "pysx / examples"
                Title: "Operators"
                Description: "Each row shows one supported spelling and its live value."

            Card:
                Title: "Tier 1 - real operators"
                Description: "Python lets these return a Signal, so they are overloaded."
                Reading:
                    "count -> "
                    Result(id="t1-count-value"): {count}
                Reading:
                    "count * 2 -> "
                    Result(id="doubled"): {doubled}
                Reading:
                    "profile['scores']['first'] -> "
                    Result(id="nested"): {nested}
                Reading:
                    "tags[0] -> "
                    Result(id="positional"): {positional}
                Reading:
                    "list_index(tags, 0) -> "
                    Result(id="head"): {head}
                Actions:
                    Action(type="button", id="t1-count", onClick={advance}): "count + 1"
                    Action(type="button", id="t1-nested", onClick={bump_nested}): "scores.first + 1"
                    Action(type="button", id="t1-rotate", onClick={rotate}): "rotate tags"
                    Action(type="button", id="t1-reset", onClick={reset}): "reset state"

            Card:
                Title: "Tier 2 - named functions only"
                Description: "and/or/not/in/len must return primitives, so cannot be overloaded."
                Reading:
                    "count -> "
                    Result(id="t2-count-value"): {count}
                Reading:
                    "all_of(big, named) -> "
                    Result(id="both"): {both}
                Reading:
                    "any_of(big, not_(named)) -> "
                    Result(id="either"): {either}
                Reading:
                    "not_(both) -> "
                    Result(id="negated"): {negated}
                Reading:
                    "contains(tags, 'alpha') -> "
                    Result(id="tagged"): {tagged}
                Reading:
                    "contains(inclusive_range(1, 5), count) -> "
                    Result(id="in-range"): {in_range}
                Reading:
                    "length(tags) -> "
                    Result(id="tag-count"): {tag_count}
                Reading:
                    "concat('hello ', name) -> "
                    Result(id="greeting"): {greeting}
                Reading:
                    "eq(name, 'ada') -> "
                    Result(id="named-eq"): {named}
                Reading:
                    "ne(name, 'grace') -> "
                    Result(id="differs"): {differs}
                Actions:
                    Action(type="button", id="t2-count", onClick={advance}): "count + 1"
                    Action(type="button", id="t2-name", onClick={rename}): "toggle name"
                    Action(type="button", id="t2-tag", onClick={toggle_tag}): "drop/restore alpha"
                    Action(type="button", id="t2-reset", onClick={reset}): "reset state"

            Card:
                Title: "Tier 3 - ordering"
                Description: "Overloaded because ordering has no competing Python meaning."
                Reading:
                    "count -> "
                    Result(id="t3-count-value"): {count}
                Reading:
                    "count > 1 -> "
                    Result(id="over"): {over}
                Reading:
                    "count < 9 -> "
                    Result(id="under"): {under}
                Reading:
                    "count >= 2 -> "
                    Result(id="at-least"): {at_least}
                Reading:
                    "count <= 2 -> "
                    Result(id="at-most"): {at_most}
                Reading:
                    "gt(count, 2) -> "
                    Result(id="over-named"): {over_named}
                Reading:
                    "lt(count, 9) -> "
                    Result(id="under-named"): {under_named}
                Reading:
                    "ge(count, 2) -> "
                    Result(id="at-least-named"): {at_least_named}
                Reading:
                    "le(count, 2) -> "
                    Result(id="at-most-named"): {at_most_named}
                Actions:
                    Action(type="button", id="t3-count", onClick={advance}): "count + 1"
                    Action(type="button", id="t3-reset", onClick={reset}): "reset state"

            Card:
                Title: "Default - derived(lambda: ...)"
                Description: "Real and/or/not with short-circuiting; prefer this for anything else."
                Reading:
                    "count -> "
                    Result(id="t4-count-value"): {count}
                Reading:
                    "count() > 1 and name() == 'ada' -> "
                    Result(id="short-circuit"): {short_circuit}
                Reading:
                    "not (count() > 2) -> "
                    Result(id="lambda-not"): {lambda_not}

                Actions:
                    Action(type="button", id="t4-count", onClick={advance}): "count + 1"
                    Action(type="button", id="t4-name", onClick={rename}): "toggle name"
                    Action(type="button", id="t4-reset", onClick={reset}): "reset state"
    """,
    )
