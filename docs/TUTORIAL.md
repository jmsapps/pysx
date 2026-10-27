# Interactive tutorial website

The full course is the standalone application in [tutorial/](../tutorial/README.md).
It has its own package, shared components, themes, routes, assets and environment.
It is grounded in the maintained runnable examples and uses pysx to teach pysx.

From the library checkout, prepare its independent environment and run it:

```sh
uv run --project . python tutorial/bootstrap.py
uv run --project tutorial pysx-tutorial
```

Open http://127.0.0.1:8750. In the tutorial environment the command is
`pysx-tutorial`. Follow the course overview through 28 ordered lessons,
each with named Previous/Next links, original explanations, a code walkthrough,
a live demo, an exercise, observable outcomes and a recap.

The course progresses from installation, templates and signals to forms,
structured state, keyed identity, components, effects, styling and navigation.
Later lessons cover accessible focus, async work, errors, HTTP rendering,
isolation, reconnect, browser-held recovery, explicit durability, authorization,
editor/development tools and hosting. The final workspace puts the ideas together.

Every demo has a reset path. Practice marks are the learner's self-assessment.
Exercises execute trusted application code and accept bounded data; they do not
evaluate arbitrary submitted Python. The [course runbook](../tutorial/README.md)
covers independent installed-wheel operation, real restart journeys and local
demonstration identities.

For reference material alongside the course, use the [API index](API.md),
[installed quickstart](QUICKSTART.md), [migration guide](MIGRATION.md) and
[example guide](../examples/README.md).
