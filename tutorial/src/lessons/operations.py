"""Authorization, tooling, hosting and a coherent final application."""

from textwrap import dedent

from src.content import Lesson

LESSONS = (
    Lesson(
        24,
        "authentication",
        "Authentication and authorization",
        "Build and operate",
        "Apply current host authorization to an operation and keep login generations separate.",
        "Authentication identifies the current principal and login session. Authorization "
        "decides whether that principal may perform a particular operation on a target. "
        "A hidden button is not an authorization check; the handler must still enforce policy.\n\n"
        "This course has explicitly labeled local demonstration roles. The login links "
        "change a demo cookie and create a new authentication generation. They are not "
        "a production credential provider and do not ask you for a real password.",
        "The host resolves identity for HTTP and socket operations. `require_access` "
        "checks the current policy at the write boundary. The reader may edit its own "
        "private UI draft while a protected commit remains denied.\n\n"
        "Identity changes must not import another login's delivered state. Recovery "
        "tests need both a same-generation restoration control and a changed-generation "
        "rejection; a system that never restores anything would otherwise appear secure "
        "by passing only the negative case.",
        dedent("""
            from pysx import AccessDenied, RequestContext, SecurityPolicy

            def authorize(request: RequestContext, action: str, target: str) -> bool:
                return action != "write" or request.principal == "writer"

            policy = SecurityPolicy(authorize=authorize)
            assert not authorize(RequestContext(principal="reader"), "write", "draft")
        """).strip(),
        "The policy evaluates the principal from the request. The live handler calls "
        "`require_access` before updating its protected counter; it does not merely compare "
        "a role label.",
        "Sign in as reader and attempt a protected write. Repeat as writer, then sign out "
        "and inspect fresh state.",
        "Reader receives a visible denial; writer succeeds. A new login generation starts "
        "its own authorized state.",
        "Check authorization at the operation, and bind continuity to the current login session.",
        ("session_security",),
    ),
    Lesson(
        25,
        "development-workflow",
        "Development workflow and editor tools",
        "Build and operate",
        "Use actual static diagnostics and a compatible watch workflow without executing samples.",
        "Fast feedback helps you reason about live templates before opening a browser. "
        "The checker and editor understand markup alongside Python, but template "
        "diagnostics remain advisory. A successful editor check is not a runtime proof.\n\n"
        "The source panel below is analyzed as text. Its fix changes an eager `count()` "
        "hole to a live `count` reference. This is safe static analysis, not evaluation of "
        "whatever source a learner supplies.",
        "Watching requires a server you control, so this lesson's exercise uses a "
        "playground rather than the course you are reading. Scaffold the counter from "
        "lesson 1 again, then run it with `pysx --app playgrounds.counter --watch` and edit "
        "`playgrounds/counter.py` while it serves. Compatible "
        "saved edits preserve supported UI state while a broken candidate leaves the "
        "previous worker available. The documented CLI uses the same application entry "
        "as ordinary serving.\n\n"
        "Install the language-server extra and select that environment in the editor. "
        "Keep Python/Pylance ownership of Python expressions and use the framework's "
        "template-aware tooling for markup. The final installed qualification uses the "
        "real editor host, not a mock completion provider.",
        dedent("""
            from pysx.tooling.check import diagnostics_for_source

            source = '''from pysx import pysx
            view = pysx(t\"\"\"
                div(hreff="/")
            \"\"\")
            '''
            assert diagnostics_for_source(source)
        """).strip(),
        "`diagnostics_for_source` reads source without importing its application. The "
        "intentional `hreff` misspelling produces an advisory finding rather than a "
        "fabricated failure.",
        "Inspect the frozen-hole warning, apply its real suggested fix, then show and "
        "correct an attribute warning.",
        "The actual diagnostic report changes with the sample, and a repaired sample has "
        "no finding.",
        "Combine static feedback, a real watch journey and runtime verification.",
        ("development", "diagnostics", "editor_features"),
    ),
    Lesson(
        26,
        "hosting-and-deployment",
        "Hosting and deployment",
        "Build and operate",
        "Validate a host configuration and distinguish local teaching defaults from "
        "production policy.",
        "An application needs operational boundaries as well as attractive markup. "
        "`HostConfig` owns budgets, assets, lifecycle hooks and security policy. Standalone "
        "and optional ASGI adapters serve the same application engine.\n\n"
        "A loopback teaching server is not a production authentication/TLS setup. The "
        "`production` flag validates required security choices instead of silently "
        "accepting an unsafe collection of defaults.",
        "The configuration controls below instantiate real `HostConfig` values. Select "
        "an invalid production profile and inspect its rejection, then choose the complete "
        "profile. No remote host is contacted and no credentials are collected.\n\n"
        "Read the course runbook before exposing the application: use a real identity "
        "provider, explicit HTTPS origins, secure cookies and a deliberate authorization "
        "policy. Health checks and graceful shutdown are part of deployment. Keep the "
        "state/key directory stable across the restarts you expect to recover from.",
        dedent("""
            from pysx import HostConfig

            config = HostConfig(render_capacity=25)
            assert config.render_capacity == 25
            try:
                HostConfig(production=True)
            except ValueError:
                pass
            else:
                raise AssertionError("Incomplete production policy must be rejected")
        """).strip(),
        "The first configuration is a bounded local host. The second deliberately "
        "omits production requirements and must raise rather than looking secure by label alone.",
        "Try the incomplete production configuration, select the complete profile, then "
        "reset to local mode.",
        "The validator rejects missing security requirements and accepts explicitly "
        "complete configuration.",
        "Treat deployment policy, budgets and lifecycle as application behavior that can "
        "be tested.",
        ("session_security", "durable_state"),
    ),
    Lesson(
        27,
        "capstone-workspace",
        "Build the capstone workspace",
        "Build and operate",
        "Combine the course's state, keyed forms, components, routes and themes in one "
        "useful workflow.",
        "Now bring the pieces together in a project workspace. Individual demonstrations "
        "teach one boundary at a time; a coherent application reveals where those "
        "boundaries interact. A record edit changes derived filters, but must not move "
        "its private draft to a different row.\n\n"
        "The capstone uses the tutorial's own shared controls and theme tokens. A new "
        "task receives a stable identifier. Editing replaces the matching record while "
        "reordering changes only order. Routes and application-level state have deliberate owners.",
        "Create a task, edit its note, mark it complete and filter the list. Reverse "
        "the rows while a draft has focus. These are ordinary user actions, not isolated "
        "API calls; the app should keep the full story consistent.\n\n"
        "Use Reset workspace to return to the starting dataset. A page refresh is a "
        "different operation with session semantics; do not use it as a hidden substitute "
        "for a broken reset. The next lesson extends this same application with failure "
        "and recovery rather than replacing it with a different showcase.",
        dedent("""
            from dataclasses import dataclass, replace
            from pysx import signal

            @dataclass(frozen=True)
            class Task:
                id: int
                title: str
                done: bool = False

            tasks = signal([Task(1, "Build")])
            tasks.update(lambda rows: [replace(row, done=True) for row in rows])
            assert tasks()[0].done
        """).strip(),
        "The record identifier stays fixed while its value is replaced. A keyed view "
        "can therefore retain its input identity while rendering the changed record.",
        "Add a task, edit a row note, reverse, toggle completion and test the active "
        "filter. Reset and repeat.",
        "The same row retains its note across movement, filters show the correct records "
        "and reset restores the dataset.",
        "A good capstone verifies a complete user journey across several ownership boundaries.",
        ("todos", "keyed_rows", "forms", "navigation", "themes"),
    ),
    Lesson(
        28,
        "capstone-resilience",
        "Make the capstone resilient",
        "Build and operate",
        "Add real async failure/retry, authorization and reconstruction to the completed "
        "workspace.",
        "A useful application must explain what happens when work fails. Extend the "
        "workspace with an owned validation resource, a protected commit and an explicit "
        "declared state root. A retry changes only the work that needs to be retried.\n\n"
        "The resilient capstone uses the same real durable lab as the persistence "
        "lesson, with a complete task journey rather than an invented saved label. "
        "A new setup marker after worker loss proves that code and resources were rebuilt.",
        "Edit a draft, perform an authorized commit, simulate a resource failure and "
        "retry it. Keep the document open through the documented server restart, then "
        "continue using rebuilt handlers. Compare a new tab and an authentication change.\n\n"
        "These are several different guarantees: live reattach, reconstruction of "
        "committed declared state, current authorization and ordinary fresh-session "
        "behavior. None promises exactly-once external transactions. The installed "
        "application checks and your final review judge this entire story together.",
        dedent("""
            from pysx import AuthenticatedIdentity, StateCodec

            identity = AuthenticatedIdentity("learner", "login-generation-1")
            assert identity.principal == "learner"
            codec = StateCodec[str](lambda value: value, lambda value: str(value))
        """).strip(),
        "Authentication generation and state codec are explicit contracts. The lab's "
        "complete source adds bounded decoding, a store, authorization and owned resources.",
        "Complete the embedded lab's draft/commit/fail/retry workflow here. Then, for the "
        "restart half, scaffold `pysx-playground durable`, run `pysx --app "
        "playgrounds.durable`, stop it with the tab open and start it again.",
        "Committed declared state is reconstructed, the setup marker changes, retry works "
        "and current policy still applies.",
        "Resilience is an observed workflow across boundaries, not one successful initial render.",
        ("browser_recovery", "durable_state", "resources", "session_security"),
    ),
)
