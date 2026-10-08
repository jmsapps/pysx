# Routing

Create a `Router` inside the application factory so each browser session owns its
location and route state. `Route(path, view, children=...)` takes a callable that
receives `RouteState` and returns a t-string or `Fragment`. Render `router.view()`
inside the application. `router.location`, `path`, `search`, `hash`, `query` and
`params` are signals. Search/hash retain their leading `?`/`#`; query is an ordered
tuple of pairs, preserving duplicate keys and blank values.

```python
from pysx import Fragment, Route, Router, RouteState, pysx

def user(state: RouteState) -> Fragment:
    return pysx(t'p: {state.params()["id"]}')

def app() -> Fragment:
    router = Router([Route("/users/:id", user)])
    return router.view()
```

Patterns match case-sensitive segments and ignore outer slashes. Named segments
capture one segment; multiple parameters are supported. Parameters decode strict
UTF-8 after splitting segments, so `%2F` remains one captured segment. `*` accepts
zero or more remaining segments, including in `/files/*`. Matching stops at a
wildcard. Interior empty segments remain significant. Query and hash never affect
matching.

Nested declarations flatten in declaration order. Relative child paths append to
the parent pattern; absolute children start at the root. A grouping route may omit
its view. Parent views do not wrap child views. The first ordinary match wins;
a declaration whose path is exactly `*` supplies the global fallback, with the
last fallback winning. Without a match or fallback, the outlet is empty and
parameters clear.

Route identity follows the declaration. Parameter changes retain component local
state and resources; leaving a route disposes its resources, and returning creates
a new scope. Location changes remain isolated to the browser session.

`base_path="/app"` restricts matching to that path prefix at a segment boundary.
The browser-facing location remains absolute; patterns are relative to the base.
Navigation destinations use browser-absolute paths: use `/app/...` or explicit
relative operations to stay inside that base.
Locations must be local absolute URLs with valid percent escapes and UTF-8, bounded
to 8192 characters. Control characters, whitespace, backslashes and protocol-relative
URLs are rejected. Invalid observations leave the previously accepted state intact.

`router.response` exposes the accepted location, matched pattern, and a suggested
status (`200` for a match/fallback, `404` otherwise). HTTP hosts can use this seam
for initial rendering and deep-link responses. The current standalone host serves
its shell at `/`; HTTP deep-link rendering is a separate host integration concern.

## Navigation and links

`router.navigate(destination, replace=False)` pushes history by default. Paths resolve
like a filesystem, treating the current pathname as a directory even without a
trailing slash. From `/users/1`:

| Destination | Resolved path |
|---|---|
| `/` | `/` (absolute root) |
| `foo` or `./foo` | `/users/1/foo` (child) |
| `../` | `/users/` (parent) |
| `../../` | `/` (parent's parent) |
| `../settings` | `/users/settings` (sibling) |

An initial `/` makes any path absolute. Dot segments normalize, and parent traversal
stops at the root. `+` and `-` are ordinary path segments. Path navigation discards
the old search/hash and retains those supplied by the destination. Navigation
preserves explicit trailing slashes and query/hash contents.
`#target` retains the current pathname/search; `?mode=raw` retains the
pathname and replaces search/hash. HTTP(S) and protocol-relative destinations perform
full browser navigation; other schemes are rejected by this server API.

Call `router.navigate()` from a button handler or a synchronous `effect()` to navigate
programmatically. Effects run immediately and track signal reads. Guard a redirect
with the current path so reaching its destination does not trigger another navigation.
Own its disposal with `on_cleanup(effect(callback).dispose)`. The
[navigation example](../examples/navigation.py) demonstrates a state-driven sign-in
redirect, history replacement and a Continue button that clears the requirement.

`Link("Details", router=router, href="/users/1", id="details")` renders an anchor
and accepts the complete typed native anchor attributes, including reactive hrefs,
events and `custom_attrs` for data/aria metadata. `href` is required. Its relative
href resolves into a browser-usable absolute path so native modifier navigation uses
the same destination. Use `replace=True` to replace the current history entry.

An eligible internal left click is held immediately in the browser. A declared
`on_click` handler runs first, then `on_navigate` receives a `NavigationEvent` containing
the browser snapshot, destination and eligibility. Both callbacks may be async.
Call `event.cancel_navigation()` to leave URL and route unchanged. A callback that
navigates explicitly also replaces the default Link decision. Declared `on_event`
policies remain effective, including default prevention and propagation control.

Empty hrefs, fragment-only links, schemes/protocol-relative URLs, downloads (including
an empty download attribute), other targets, modified/non-left clicks and already
prevented clicks retain browser defaults. Link callbacks still run for native clicks
that reach their declared event policy. Native fragment changes update route hash
state without an extra history push.

The browser's initial local URL is validated before constructing the session. History
and hash observations share a deduplicated listener and serialize with server events.
The browser assigns monotonically increasing navigation revisions. Server history
commands follow the patches they caused; stale revisions cannot replace newer browser
history state. This ordering does not provide transport resume or HTTP deep-link SSR.

## Scroll and focus

Navigation applies scroll/focus after the route's DOM patch commits. Fragment IDs
decode UTF-8 safely and match IDs or named anchors inside the app root. Nonfocusable
targets receive temporary `tabindex="-1"`, removed on blur, and focus without a second
scroll. Targets introduced by a later mount patch retry for at most two seconds.
Every retry checks the navigation revision and current URL; a newer navigation
cancels stale target work. Repeated native fragment links scroll/focus again without
an extra history entry.

New nonfragment navigation scrolls to the top and focuses the main region (or the
app root). A missing target on a different pathname uses that same fallback; a
missing target on the same pathname preserves position. Query-only navigation is
a new navigation and uses the top policy. Replace navigation applies the same policy
while replacing the current entry.

Routing uses manual browser scroll restoration and retains up to 128 history
positions and focus IDs in the current document. Back/forward restores a known
entry's scroll/focus after its route commits, taking precedence over fragment
scrolling. A missing remembered focus target falls back to the main region.
Unknown entries use the normal top/fragment policy. Reloading starts a new position
registry. Disconnect cancels pending retries and restores the browser's prior scroll
restoration setting.
