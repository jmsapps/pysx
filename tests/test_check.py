import sys
import tempfile
from pathlib import Path

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0] + "/src")

from pysx.check import diagnostics  # noqa: E402

HEADER = "from pysx import component, div, html, signal, styled\n\n"


def check(body: str):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "case.py"
        p.write_text(HEADER + body, encoding="utf-8")
        return diagnostics(p), (HEADER + body).split("\n")


def test_clean_examples_are_empty():
    here = Path(__file__).resolve().parents[1]
    for name in ("counter", "todos"):
        path = here / f"src/pysx/examples/{name}/app.py"
        assert diagnostics(path) == [], (name, diagnostics(path))


def test_unknown_tag_after_non_ascii_line():
    body = (
        'Page = styled(div, t"""color: red;""")\n'
        'NOTE = "café ☕ — a non-ASCII line before the diagnostic"\n'
        "\n"
        "@component\n"
        "def app():\n"
        '    return html(t"""\n'
        "        Pge:\n"
        '            "hi"\n'
        '    """)\n'
    )
    diags, lines = check(body)
    assert len(diags) == 1, diags
    d = diags[0]
    assert "unknown component 'Pge'" in d["message"], d
    assert lines[d["line"]].strip() == "Pge:", (d, lines[d["line"]])
    assert lines[d["line"]][d["startChar"]:d["endChar"]] == "Pge", d


def test_called_signal_column_is_utf16_on_a_non_ascii_line():
    body = (
        'Page = styled(div, t"""color: red;""")\n'
        "\n"
        "@component\n"
        "def app():\n"
        "    count = signal(0)\n"
        '    return html(t"""\n'
        "        Page:\n"
        '            "café ☕ — total: "; {count()}\n'
        '    """)\n'
    )
    diags, lines = check(body)
    warns = [d for d in diags if d["severity"] == "warning"]
    assert len(warns) == 1, diags
    d = warns[0]
    assert "frozen" in d["message"] and "{count}" in d["message"], d

    line = lines[d["line"]]
    # Slice by UTF-16 units, the way an editor would.
    u16 = line.encode("utf-16-le")
    sliced = u16[d["startChar"] * 2:d["endChar"] * 2].decode("utf-16-le")
    assert sliced == "{count()}", repr(sliced)
    # And prove the naive byte offset would have been wrong.
    assert d["startChar"] != line.encode("utf-8").index(b"{count()}"), \
        "fixture is not exercising the byte/UTF-16 difference"


def test_bare_lambda_reports_the_parenthesis_fix():
    body = (
        'Page = styled(div, t"""color: red;""")\n'
        "\n"
        "@component\n"
        "def app():\n"
        '    return html(t"""\n'
        "        Page(onClick={lambda e: None}):\n"
        '            "hi"\n'
        '    """)\n'
    )
    diags, _ = check(body)
    assert len(diags) == 1, diags
    assert "wrap it in parentheses" in diags[0]["message"], diags


def test_fstring_instead_of_tstring():
    body = (
        'Page = styled(div, t"""color: red;""")\n'
        "\n"
        "@component\n"
        "def app():\n"
        '    return html(f"""\n'
        "        Page:\n"
        '            "hi"\n'
        '    """)\n'
    )
    diags, _ = check(body)
    assert len(diags) == 1, diags
    assert "needs a t-string" in diags[0]["message"], diags


def test_parser_error_is_reported():
    body = (
        'Page = styled(div, t"""color: red;""")\n'
        "\n"
        "@component\n"
        "def app():\n"
        '    return html(t"""\n'
        "        Page:\n"
        "            else:\n"
        '                Page: "hi"\n'
        '    """)\n'
    )
    diags, _ = check(body)
    assert any("without a matching" in d["message"] for d in diags), diags


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"{len(fns)} passed")
