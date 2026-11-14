"""Authorization, tooling, hosting and a coherent final application."""

from src.components.code_sample import CodeSample
from src.content import Lesson
from src.snippets import operations as code
from src.snippets import playgrounds

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
        CodeSample(code.authentication),
        "`authorize` permits a protected write only for writer and the course-demo target. The "
        "handler calls require_access before incrementing; denied writes produce the visible "
        "denial. The course host installs this policy and resolves the role cookie from its sign- "
        "in links; those demo links are not a production identity provider.",
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
        "Practice editor diagnostics, live bindings and compatible hot reload in your own project.",
        "Fast feedback helps you reason about live templates before opening a browser. "
        "The checker and editor understand markup alongside Python, but template "
        "diagnostics remain advisory. A successful editor check is not a runtime proof.\n\n"
        "Static analysis reads source without running it. For example, replacing an eager "
        "`count()` hole with a live `count` reference fixes a frozen display. Watch mode "
        "then lets you observe compatible edits in your own running application.",
        "Watching requires a server you control, so this lesson's exercise uses a "
        "development playground rather than the course you are reading. Run it with "
        "`pysx --app playgrounds.development --watch` and edit "
        "`playgrounds/development.py` while it serves. Compatible "
        "saved edits preserve supported UI state while a broken candidate leaves the "
        "previous worker available. The documented CLI uses the same application entry "
        "as ordinary serving.\n\n"
        "Install the language-server extra and select that environment in the editor. "
        "Keep Python/Pylance ownership of Python expressions and use the framework's "
        "template-aware tooling for markup. The final installed qualification uses the "
        "real editor host, not a mock completion provider.",
        CodeSample(playgrounds.development),
        "`count` and `draft` retain delivered edits during compatible reloads. `doubled` "
        "derives twice the count. Both number displays use live holes. Changing the count "
        "span to `count()` freezes that display and produces an advisory warning; the "
        "derived display remains live. The doubled span's `title` attribute gives you "
        "a concrete markup diagnostic to try. All edits happen in the file you actually run.",
        "Increment the count and enter a draft. Change the heading in "
        "`playgrounds/development.py` and save while keeping the tab open.\n\n"
        "Change the count span's hole from `count` to `count()`. Inspect the editor warning, "
        "increment again, then restore `count`.\n\n"
        "Misspell `title` as `tittle` on the doubled span, inspect the attribute warning, "
        "then correct it.\n\n"
        "Introduce a Python syntax error and save, then correct it and save again.",
        "A compatible heading edit preserves the count and draft. An eager hole freezes "
        "one display while the derived value still updates. The editor flags the eager "
        "hole and misspelled attribute; correcting them clears those findings. A syntax "
        "error is reported in the terminal; the previous working page remains available "
        "until you save a valid correction.",
        "Combine static feedback, a real watch journey and runtime verification.",
        ("development", "diagnostics", "editor_features"),
        "pip install 'pysx-framework[lsp]'\npysx-playground development\n"
        "pysx --app playgrounds.development --watch\n",
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
        CodeSample(code.hosting_and_deployment),
        "The controls instantiate the same real HostConfig profiles as the live validator: "
        "incomplete production is rejected, while the complete profile declares HTTPS origin, "
        "secure cookies, identity and authorization. Reset demo restores the local-mode readout; "
        "validation does not start or deploy a server.",
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
        CodeSample(code.capstone_workspace),
        "The initial records are Read the lesson and Build the interface. Add allocates a stable "
        "id; each row projects its note into the same root. Reverse preserves the record identity "
        "and note, Toggle complete updates the record, and the active filter derives the visible "
        "list. Reset workspace restores the initial dataset.",
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
        "Keep the task workspace working through validation failures and server reconstruction.",
        "A useful application must explain what happens when its server stops. The "
        "workspace from the previous lesson stores task data in ordinary signals. To "
        "make selected data durable, declare a state root, supply its codec and give the "
        "host a persistent store.\n\n"
        "The capstone playground keeps the same tasks, row notes, reordering and active "
        "filter, and adds a durable task root plus an owned validation resource. "
        "A changed setup marker after worker loss proves that the application graph "
        "was rebuilt rather than merely reattached.",
        "`State` contains the saved task records. `encode` converts them to "
        "JSON-compatible values, "
        "and `decode` checks those values before constructing a new root. The SQLite "
        "store retains a snapshot while application code creates new handlers. Keep "
        "the browser document open through the restart so it can resume its session.\n\n"
        "Simulate a validation failure and retry it without replacing your tasks. After "
        "reconstruction, add another task and validate again: restored data is useful "
        "only if the rebuilt interface can continue working with it. UI state "
        "persistence does not make external transactions exactly-once.",
        CodeSample(playgrounds.capstone),
        "`Task` retains each row's stable id, title, completion flag and note. `StateCodec` "
        "bounds and validates these records, and `SQLiteOwnershipStore` saves the root. "
        "The row note projects back into that root, so reversing or filtering rows "
        "preserves the correct record. `validation` is an owned async resource; failure "
        "and retry reload its work without clearing tasks. The setup marker, handlers "
        "and resource are rebuilt after restart. The new-task draft and filter remain "
        "temporary UI rather than saved task records.",
        "Add a task and edit a row note. Reverse the tasks, toggle completion and try "
        "the active filter; turn the filter off before restarting.\n\n"
        "Click Simulate validation failure, then Retry validation. Check that the tasks remain.\n\n"
        "Stop the server with Ctrl+C, leaving the browser tab open.\n\n"
        "Start the same run command again from the same project directory.\n\n"
        "Compare the tasks, notes, completion flags, row order and setup marker. Add another "
        "task and retry validation to exercise the rebuilt handlers and resource.",
        "Failure is visible and retry succeeds without losing task edits. Saved task "
        "records and order reconstruct after restart, while the setup marker changes. "
        "New tasks and validation still work in the rebuilt graph.",
        "Resilience is an observed workflow across boundaries, not one successful initial render.",
        ("browser_recovery", "durable_state", "resources", "session_security"),
        "pip install pysx-framework\npysx-playground capstone\npysx --app playgrounds.capstone\n",
    ),
)
