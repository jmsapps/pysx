"""Course-owned semantic lesson rendering and named navigation."""

from typing import TYPE_CHECKING

from pysx import Fragment, Link, derived, each, native, pysx, signal
from pysx.reactive.reactive import untracked
from tutorial.catalog import LESSONS, neighbors

from . import Button

if TYPE_CHECKING:
    from collections.abc import Callable

    from pysx import Router
    from tutorial.content import Lesson


def Paragraphs(*, text: str) -> Fragment:
    return native.Fragment(*(native.P(paragraph) for paragraph in text.split("\n\n")))


def Navigation(*, lesson: Lesson, router: Router, position: str) -> Fragment:
    previous, following = neighbors(lesson)

    return native.Nav(
        Link(
            f"Previous: {previous[1]}",
            router=router,
            href=previous[0],
            custom_attrs={"data-previous": position},
        ),
        Link(
            f"Next: {following[1]}",
            router=router,
            href=following[0],
            custom_attrs={"data-next": position},
        ),
        class_name="lesson-nav",
        custom_attrs={"aria-label": f"Lesson navigation ({position})"},
    )


def Article(*, lesson: Lesson, router: Router, demo: Callable[[], Fragment]) -> Fragment:
    revision = signal([0])
    practiced = signal(False)
    generation = derived(lambda: revision()[0])

    def reset(_: object) -> None:
        revision.set([revision()[0] + 1])
        practiced.set(False)

    def mark(_: object) -> None:
        practiced.set(not practiced())

    def build(_revision: int) -> Fragment:
        # A demo's deliberate eager snapshots must not subscribe the reset row's
        # builder to private state created during construction. Its returned live
        # holes still install their ordinary independently tracked watchers.
        with untracked():
            return demo()

    return pysx(t"""
        article(class="lesson" data-lesson={lesson.slug} data-demo-generation={generation})
            Navigation(lesson={lesson} router={router} position="top"):
            p(class="eyebrow"): {lesson.unit} " · Lesson " {lesson.number} " of 28"
            h1: {lesson.title}
            p(class="lede"): {lesson.objective}
            Paragraphs(text={lesson.introduction}):
            if {bool(lesson.commands)}:
                h2: "Prepare and run"
                p: "Run the first commands from the framework checkout. Then try your own module."
                pre: {lesson.commands}
            h2: "Understand the moving parts"
            Paragraphs(text={lesson.explanation}):
            h2: "Walk through the code"
            pre:
                code: {lesson.code}
            Paragraphs(text={lesson.walkthrough}):
            section(class="demo" aria-label="Interactive lesson")
                h2: "Try it live"
                {each(revision, build, key=lambda value: value)}
                Button(id="reset-demo" onClick={reset}): "Reset demo"
            section(class="exercise")
                h2: "Your turn"
                p(id="lesson-exercise"): {lesson.exercise}
                h3: "What to observe"
                p(id="lesson-expected"): {lesson.expected}
                Button(id="mark-practiced" ariaPressed={practiced} onClick={mark}):
                    "Mark / unmark as practiced"
                p: "Practice marked: " {practiced}
            h2: "Take this with you"
            p: {lesson.recap}
            Navigation(lesson={lesson} router={router} position="bottom"):
            footer(class="course-footer"):
                "Original working material grounded in the runnable examples: "
                {", ".join(lesson.examples)}
    """)


def Contents(*, router: Router) -> Fragment:
    def item(lesson: Lesson) -> Fragment:
        current = derived(lambda: "page" if router.path().startswith(lesson.path) else None)

        return native.Li(
            Link(
                f"{lesson.number:02}. {lesson.title}",
                router=router,
                href=lesson.path,
                custom_attrs={"aria-current": current},
            )
        )

    def group(unit: str) -> Fragment:
        lessons = tuple(lesson for lesson in LESSONS if lesson.unit == unit)

        return native.Section(
            native.H3(unit), native.Ol(each(lessons, item, key=lambda lesson: lesson.slug))
        )

    units = tuple(dict.fromkeys(lesson.unit for lesson in LESSONS))

    return pysx(t"""
        aside:
            details(class="contents" open={True})
                summary: "Course contents"
                nav(aria-label="Course contents"):
                    {each(units, group, key=str)}
    """)
