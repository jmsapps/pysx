"""Course-owned semantic lesson rendering and named navigation."""

from typing import TYPE_CHECKING

from pysx import Fragment, Link, derived, each, pysx, signal
from pysx.reactive.reactive import untracked
from tutorial.catalog import LESSONS, neighbors
from tutorial.components import Actions

from . import Button
from .code import CodeBlock

if TYPE_CHECKING:
    from collections.abc import Callable

    from pysx import Router
    from tutorial.content import Lesson


def Paragraphs(*, text: str) -> Fragment:
    paragraphs = tuple(enumerate(text.split("\n\n")))

    def paragraph(part: tuple[int, str]) -> Fragment:
        return pysx(t"""
            p: {part[1]}
        """)

    return pysx(t"""
        {each(paragraphs, paragraph, key=lambda part: part[0])}
    """)


def Navigation(*, lesson: Lesson, router: Router, position: str) -> Fragment:
    previous, following = neighbors(lesson)
    previous_attrs = {"data-previous": position}
    following_attrs = {"data-next": position}

    return pysx(t"""
        nav(class="lesson-nav" aria-label={f"Lesson navigation ({position})"})
            Link(router={router} href={previous[0]} custom_attrs={previous_attrs})
                "Previous: " {previous[1]}
            Link(router={router} href={following[0]} custom_attrs={following_attrs})
                "Next: " {following[1]}
    """)


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
            CodeBlock(source={lesson.code}):
            Paragraphs(text={lesson.walkthrough}):
            section(class="demo" aria-label="Interactive lesson")
                h2: "Try it live"
                p(id="lesson-exercise"): {lesson.exercise}
                h3: "What to observe"
                p(id="lesson-expected"): {lesson.expected}
                {each(revision, build, key=lambda value: value)}
                Actions:
                    Button(id="reset-demo" onClick={reset}): "Reset demo"
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
        attributes = {"aria-current": current}

        return pysx(t"""
            li:
                Link(router={router} href={lesson.path} custom_attrs={attributes})
                    {f"{lesson.number:02}. {lesson.title}"}
        """)

    def group(unit: str) -> Fragment:
        lessons = derived(lambda: tuple(lesson for lesson in LESSONS if lesson.unit == unit))

        return pysx(t"""
            section:
                h3: {unit}
                ol: {each(lessons, item, key=lambda lesson: lesson.slug)}
        """)

    units = derived(lambda: tuple(dict.fromkeys(lesson.unit for lesson in LESSONS)))

    return pysx(t"""
        aside:
            details(class="contents" open={True})
                summary: "Course contents"
                nav(aria-label="Course contents"):
                    {each(units, group, key=str)}
    """)
