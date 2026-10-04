"""Isolated flat-CSS lineage proof; this is not the production styled API."""

from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class Lineage:
    declarations: tuple[str, ...]

    def extend(self, css: str) -> Lineage:
        if "{" in css or "}" in css:
            raise ValueError("lineage proof accepts flat property lists")
        return Lineage((*self.declarations, css))

    @property
    def css(self) -> str:
        return ";".join(part.strip().rstrip(";") for part in self.declarations)

    @property
    def css_class(self) -> str:
        return "proof_" + sha256(self.css.encode()).hexdigest()[:16]


def cascade_fixture() -> dict[str, object]:
    """Opposing chains, identical pre-registration and literal/dynamic class merges."""
    empty = Lineage(())
    red = empty.extend("color: red; background-color: white; padding-left: 3px")
    blue = empty.extend("color: blue; background-color: black; padding-left: 7px")
    red_blue = red.extend("color: blue; padding-left: 11px")
    blue_red = blue.extend("color: red; padding-left: 13px")
    preregistered = Lineage((blue_red.css,))
    rules: dict[str, str] = {}
    for lineage in (preregistered, blue, red, red_blue, blue_red):
        previous = rules.setdefault(lineage.css_class, lineage.css)
        assert previous == lineage.css
    return {
        "css": "\n".join(f".{name}{{{css}}}" for name, css in rules.items()),
        "classes": {"red_blue": red_blue.css_class, "blue_red": blue_red.css_class},
        "preregistered": preregistered.css_class,
        "rule_count": len(rules),
    }
