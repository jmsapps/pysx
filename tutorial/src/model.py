"""Shared immutable workspace state for both capstone lessons and durable reconstruction."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Task:
    id: int
    title: str
    done: bool = False
    note: str = ""


@dataclass(frozen=True)
class LabState:
    draft: str = ""
    count: int = 0
    tasks: tuple[Task, ...] = (Task(1, "Read the lesson"), Task(2, "Build the interface"))
