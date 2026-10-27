"""One standalone course entry point with owned lessons and a real durable lab."""

import os
from dataclasses import replace
from http.cookies import SimpleCookie
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from pysx import (
    Application,
    AuthenticatedIdentity,
    Fragment,
    HostConfig,
    Link,
    Route,
    Router,
    SecurityPolicy,
    StaticAsset,
    current_request,
    derived,
    each,
    pysx,
)

from .catalog import LESSONS
from .components import Button
from .components.pages import Article, Contents
from .demos import composition, foundations, navigation, operations, runtime
from .durable import create_lab
from .themes import course_themes, selected_name

if TYPE_CHECKING:
    from collections.abc import Callable

    from pysx import RequestContext, RouteState, Themes
    from pysx.hosting.http import HTTPResult
    from pysx.runtime.connection import BoundedConnection, Connection

    from .content import Lesson


def demo_identity(request: RequestContext) -> AuthenticatedIdentity:
    cookie = SimpleCookie()
    cookie.load(request.headers.get("cookie", ""))
    role = cookie["course_role"].value if "course_role" in cookie else "learner"
    generation = cookie["course_login"].value if "course_login" in cookie else "anonymous-course"

    return AuthenticatedIdentity(
        role if role in ("learner", "reader", "writer") else "learner",
        generation or "anonymous-course",
    )


def authorize(request: RequestContext, action: str, target: str) -> bool:
    return action != "write" or (request.principal == "writer" and target == "course-demo")


def demonstration(slug: str, router: Router, themes: Themes, state: RouteState) -> Fragment:
    simple: dict[str, Callable[[], Fragment]] = {
        "getting-started": foundations.greeting,
        "templates-and-elements": foundations.template_card,
        "live-signals": foundations.counter,
        "derived-values": foundations.calculator,
        "browser-events": foundations.events,
        "forms-and-bindings": foundations.forms,
        "structured-state": foundations.structured_state,
        "conditional-views": composition.branches,
        "lists-and-loops": composition.loops,
        "keyed-identity": composition.keyed_rows,
        "components-and-children": composition.components,
        "effects-and-lifetimes": composition.effects,
        "keyboard-and-focus": navigation.keyboard,
        "errors-and-retry": runtime.errors,
        "session-isolation": runtime.sessions,
        "authentication": operations.authentication,
        "development-workflow": operations.diagnostics,
        "hosting-and-deployment": operations.hosting,
        "capstone-workspace": operations.capstone,
    }

    if slug in simple:
        return simple[slug]()

    if slug == "styles-and-themes":
        return navigation.styles(themes)

    if slug in ("routes-and-parameters", "links-and-history"):
        return navigation.routes(router, slug, state)

    if slug in ("async-resources", "server-rendering"):
        return runtime.async_resource(server_rendering=slug == "server-rendering")

    if slug in ("reconnection", "browser-recovery"):
        return runtime.recovery(clock=slug == "reconnection")

    if slug in ("durable-state", "capstone-resilience"):
        exercise = operations.codec()

        return pysx(t"""
            {exercise}
            iframe(id="durable-frame" src="/labs/durable" title="Real durable application lab")
            p: "Keep the lab document open through a server restart to test reconstruction."
        """)

    raise ValueError(f"unknown lesson demo: {slug}")


def course() -> Fragment:
    themes = course_themes()
    theme_name = derived(lambda: selected_name(themes))

    def lesson_view(lesson: Lesson) -> Callable[[RouteState], Fragment]:
        def view(state: RouteState) -> Fragment:
            current_request().document.title = lesson.title + " · Learn pysx"

            return Article(
                lesson=lesson,
                router=router,
                demo=lambda: demonstration(lesson.slug, router, themes, state),
            )

        return view

    def overview(_: RouteState) -> Fragment:
        current_request().document.title = "Learn pysx · A practical course"
        teaser = foundations.greeting()

        def card(lesson: Lesson) -> Fragment:
            return pysx(t"""
                Link(router={router} href={lesson.path})
                    small: {lesson.unit} " · " {f"{lesson.number:02}"}
                    {lesson.title}
            """)

        return pysx(t"""
            article(class="lesson" data-page="overview")
                p(class="eyebrow"): "A practical course · Python 3.14+"
                h1: "Small ideas.\nLiving interfaces."
                p(class="lede"):
                    "Learn server-side reactive Python by reading, changing and rebuilding "
                    "real interfaces."
                p:
                    "Start with one live value. Finish with a routed app that handles edits, "
                    "asynchronous work and server loss. Each of the 28 lessons pairs a careful "
                    "explanation with working code and a live exercise."
                nav(class="lesson-nav" aria-label="Course boundary navigation")
                    span(aria-disabled="true"): "Previous: Start of course"
                    Link(router={router} href={LESSONS[0].path}): "Next: " {LESSONS[0].title}
                section(class="demo")
                    h2: "Your first live page"
                    {teaser}
                h2: "The complete course"
                div(class="overview-grid")
                    {each(LESSONS, card, key=lambda lesson: lesson.slug)}
                p: "The course runs on pysx. Your private demo state belongs to this session."
        """)

    def review(_: RouteState) -> Fragment:
        exercise = operations.capstone()

        return pysx(t"""
            article(class="lesson" data-page="review")
                p(class="eyebrow"): "Course review"
                h1: "Build something that stays useful."
                p(class="lede"): "You have connected authoring, ownership and operations."
                p:
                    "Explain where your state lives, which values stay live, what a key preserves, "
                    "who owns background work and what recovery can actually promise. "
                    "Then prove those answers with an application journey."
                section(class="exercise")
                    h2: "One final practice"
                    p: "Add a task, retain a note while reversing, filter completed work and reset."
                    {exercise}
                h2: "Review checklist"
                ul:
                    li: "I can distinguish a live source from an eager snapshot."
                    li: "I can keep forms, keys and focus consistent while data changes."
                    li: "I can own effects/resources and recover a failed region."
                    li: "I can test isolation, authorization and reconstruction separately."
                nav(class="lesson-nav" aria-label="Course boundary navigation")
                    Link(router={router} href={LESSONS[-1].path}): "Previous: " {LESSONS[-1].title}
                    span(aria-disabled="true"): "Next: End of course"
                Link(router={router} href="/"): "Return to the course overview"
        """)

    def missing(_: RouteState) -> Fragment:
        current_request().document.status = 404

        return pysx(t"""
            article(class="lesson")
                h1: "Lesson not found"
                Link(router={router} href="/"): "Open the complete course"
        """)

    routes = [Route("/", overview), Route("/review", review)]

    for lesson in LESSONS:
        view = lesson_view(lesson)
        routes.append(Route(lesson.path, view))

        if lesson.slug in ("routes-and-parameters", "links-and-history"):
            routes.append(Route(lesson.path + "/projects/:id", view))
            routes.append(Route(lesson.path + "/files/*", view))
            routes.append(Route(lesson.path + "/*", view))
    routes.append(Route("*", missing))
    router = Router(routes)

    def switch(_: object) -> None:
        themes.select("dark" if theme_name() == "light" else "light")

    return pysx(
        t"""
        link(rel="stylesheet" href="/assets/course.css")
        header(class="course-header")
            Link(router={router} href="/" class_name="brand"):
                "pysx"
                small: "Learn by building"
            Button(id="course-theme" onClick={switch}): "Theme: " {theme_name}
        a(href="#lesson-main" class="skip-link"): "Skip to lesson"
        div(class="course-layout")
            Contents(router={router}):
            main(id="lesson-main" tabindex={-1}): {router.view()}
    """,
        themes=themes,
    )


class CourseApplication(Application):
    """Own the course and its isolated real durable lab on the same server."""

    def __init__(self, directory: Path) -> None:
        directory = directory.resolve()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        (directory / "keys").mkdir(mode=0o700, exist_ok=True)
        assets = {
            "/favicon.ico": StaticAsset(
                files("src").joinpath("assets/favicon.png").read_bytes(),
                "image/png",
                cache_seconds=3600,
            ),
            "/assets/course.css": StaticAsset(
                files("src").joinpath("assets/course.css").read_bytes(), "text/css"
            ),
        }
        config = HostConfig(
            assets=assets,
            principal=demo_identity,
            security=SecurityPolicy(authorize=authorize),
            recovery_namespace="pysx-tutorial-course",
            recovery_key_directory=directory / "keys",
        )
        super().__init__(course, config=config)
        self.lab = create_lab(
            directory,
            replace(config, recovery_namespace="pysx-tutorial-lab"),
            schema=int(os.environ.get("TUTORIAL_LAB_SCHEMA", "1")),
        )

    async def start(self) -> None:
        if not self.started:
            from pysx.hosting.recovery_keys import load_keys

            assert self.config.recovery_key_directory is not None
            load_keys(self.config.recovery_key_directory, provision=True)
        await super().start()

    async def http(self, request: RequestContext, *, method: str = "GET") -> HTTPResult:
        from pysx.hosting.http import HTTPResult

        await self.start()
        path = urlsplit(request.path).path

        if path in ("/demo-login/reader", "/demo-login/writer", "/demo-logout"):
            role = path.rsplit("/", 1)[-1] if path != "/demo-logout" else "learner"

            return HTTPResult(
                303,
                (
                    ("location", "/learn/authentication"),
                    ("set-cookie", f"course_role={role}; Path=/; HttpOnly; SameSite=Strict"),
                    (
                        "set-cookie",
                        f"course_login={uuid4().hex}; Path=/; HttpOnly; SameSite=Strict",
                    ),
                    ("cache-control", "no-store"),
                ),
                b"",
            )

        if path.startswith("/labs/"):
            response = await self.lab.http(request, method=method)

            # Only this deliberately embedded, same-origin teaching lab permits
            # framing. Ordinary course responses retain the default no-framing CSP.
            return replace(
                response,
                headers=tuple(
                    (name, value.replace("frame-ancestors 'none'", "frame-ancestors 'self'"))
                    if name == "content-security-policy"
                    else (name, value)
                    for name, value in response.headers
                ),
            )

        return await super().http(request, method=method)

    async def websocket(self, connection: Connection) -> None:
        location = parse_qs(urlsplit(connection.request.path).query).get("location", [""])[0]

        if urlsplit(location).path.startswith("/labs/"):
            self.handshakes.pop(getattr(connection, "admission_key", id(connection)), None)

            if getattr(connection, "reserved", False):
                self.runtime.unreserve()
                cast("BoundedConnection", connection).reserved = False

                if not self.lab.runtime.reserve():
                    await connection.close(1013, "lab capacity")

                    return
                cast("BoundedConnection", connection).reserved = True
            await self.lab.websocket(connection)

            return
        await super().websocket(connection)

    async def close(self) -> None:
        try:
            await self.lab.close()
        finally:
            await super().close()


def create_application(directory: Path | None = None) -> CourseApplication:
    path = directory or Path(
        os.environ.get("TUTORIAL_STATE_DIR", str(Path.cwd() / ".artifacts" / "tutorial-state"))
    )

    return CourseApplication(path)


app = create_application()
