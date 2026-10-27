"""Typed lesson content; the catalog is the sole owner of ordering and routes."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Lesson:
    number: int
    slug: str
    title: str
    unit: str
    objective: str
    introduction: str
    explanation: str
    code: str
    walkthrough: str
    exercise: str
    expected: str
    recap: str
    examples: tuple[str, ...]
    commands: str = ""

    @property
    def path(self) -> str:
        return "/learn/" + self.slug
