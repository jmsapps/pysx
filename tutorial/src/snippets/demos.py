"""Authored pysx source displays used by the interactive demonstrations."""

from pysx import Fragment, pysx


def card_normal() -> Fragment:
    return pysx(rt"""
        "pysx("
        span(data-syntax="keyword"): "t"
        "\"\"\"\n    "
        span(data-syntax="tag"): "p"
        "("
        span(data-syntax="attribute"): "style"
        span(data-syntax="keyword"): "="
        span(data-syntax="string"): "\"font-weight: 400\""
        "): {{"
        span(data-syntax="expression"): "text"
        "}}\n\"\"\")"
    """)


def card_strong() -> Fragment:
    return pysx(rt"""
        "pysx("
        span(data-syntax="keyword"): "t"
        "\"\"\"\n    "
        span(data-syntax="tag"): "p"
        "("
        span(data-syntax="attribute"): "style"
        span(data-syntax="keyword"): "="
        span(data-syntax="string"): "\"font-weight: 800\""
        "): {{"
        span(data-syntax="expression"): "text"
        "}}\n\"\"\")"
    """)


def frozen_signal() -> Fragment:
    return pysx(rt"""
        span(data-syntax="keyword"): "from"
        " pysx "
        span(data-syntax="keyword"): "import"
        " pysx, signal\n\ncount "
        span(data-syntax="keyword"): "="
        " signal("
        span(data-syntax="number"): "0"
        ")\nview "
        span(data-syntax="keyword"): "="
        " pysx("
        span(data-syntax="keyword"): "t"
        "\"\"\"\n    "
        span(data-syntax="tag"): "p"
        ": {{"
        span(data-syntax="expression"): "count()"
        "}}\n\"\"\")\n"
    """)


def live_signal() -> Fragment:
    return pysx(rt"""
        span(data-syntax="keyword"): "from"
        " pysx "
        span(data-syntax="keyword"): "import"
        " pysx, signal\n\ncount "
        span(data-syntax="keyword"): "="
        " signal("
        span(data-syntax="number"): "0"
        ")\nview "
        span(data-syntax="keyword"): "="
        " pysx("
        span(data-syntax="keyword"): "t"
        "\"\"\"\n    "
        span(data-syntax="tag"): "p"
        ": {{"
        span(data-syntax="expression"): "count"
        "}}\n\"\"\")\n"
    """)


def invalid_attribute() -> Fragment:
    return pysx(rt"""
        span(data-syntax="keyword"): "from"
        " pysx "
        span(data-syntax="keyword"): "import"
        " pysx\n\nview "
        span(data-syntax="keyword"): "="
        " pysx("
        span(data-syntax="keyword"): "t"
        "\"\"\"\n    "
        span(data-syntax="tag"): "div"
        "("
        span(data-syntax="attribute"): "hreff"
        span(data-syntax="keyword"): "="
        span(data-syntax="string"): "\"bad\""
        ")\n\"\"\")\n"
    """)


def valid_attribute() -> Fragment:
    return pysx(rt"""
        span(data-syntax="keyword"): "from"
        " pysx "
        span(data-syntax="keyword"): "import"
        " pysx\n\nview "
        span(data-syntax="keyword"): "="
        " pysx("
        span(data-syntax="keyword"): "t"
        "\"\"\"\n    "
        span(data-syntax="tag"): "div"
        "("
        span(data-syntax="attribute"): "title"
        span(data-syntax="keyword"): "="
        span(data-syntax="string"): "\"valid\""
        ")\n\"\"\")\n"
    """)
