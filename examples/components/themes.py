"""Shared styled components for the themes example."""

from pysx import styled

from .controls import Action, Card
from .page import Page

ThemePage = styled(
    Page,
    t"""
        background: var(--surface, white);
        color: var(--ink, #192338);
        border-color: var(--border, #e4e8f0);
    """,
)
ThemeCard = styled(
    Card,
    t"""
        background: var(--surface, white);
        color: var(--ink, #192338);
        border-color: var(--border, #e4e8f0);
        padding: 28px;
    """,
)
Preview = styled(
    ThemeCard,
    t"""
        padding: var(--preview-space, 28px);
        border-left: 6px solid var(--preview-accent, var(--accent, #6554d9));
    """,
)
ThemeAction = styled(
    Action,
    t"""
        background: var(--accent, #6554d9);
    """,
)
