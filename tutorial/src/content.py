"""Typed lesson content; the catalog is the sole owner of ordering and routes."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.components.code_sample import CodeSample


@dataclass(frozen=True)
class Lesson:
    number: int
    slug: str
    title: str
    unit: str
    objective: str
    introduction: str
    explanation: str
    sample: CodeSample
    walkthrough: str
    exercise: str
    expected: str
    recap: str
    examples: tuple[str, ...]
    commands: str = ""

    @property
    def code(self) -> str:
        return self.sample.text

    @property
    def path(self) -> str:
        return "/learn/" + self.slug
