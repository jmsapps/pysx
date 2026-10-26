"""The single ordered owner of course routes and adjacency."""

from typing import TYPE_CHECKING

from .lessons.authoring import LESSONS as AUTHORING
from .lessons.foundations import LESSONS as FOUNDATIONS
from .lessons.operations import LESSONS as OPERATIONS
from .lessons.runtime import LESSONS as RUNTIME

if TYPE_CHECKING:
    from .content import Lesson

LESSONS = (*FOUNDATIONS, *AUTHORING, *RUNTIME, *OPERATIONS)
BY_SLUG = {lesson.slug: lesson for lesson in LESSONS}


def neighbors(lesson: Lesson) -> tuple[tuple[str, str], tuple[str, str]]:
    previous = LESSONS[lesson.number - 2] if lesson.number > 1 else None
    following = LESSONS[lesson.number] if lesson.number < len(LESSONS) else None

    return (
        (previous.path, previous.title) if previous else ("/", "Course overview"),
        (following.path, following.title) if following else ("/review", "Course review"),
    )
