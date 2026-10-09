"""Shared styled surfaces for the template-language example."""

from pysx import Fragment, pysx, styled

from .controls import Card
from .page import Page

TemplatePage = styled(Page)(t"width: min(100%, 860px)")
GroupPanel = styled(Card)(t"""
    display: grid;
    gap: 12px;
    &:hover:
      border-color: var(--accent);
""")
GroupHeading = styled.h2(t"margin: 0; font-size: 18px;")
Break = styled.br(t"")
SnapshotLabel = styled.p(t"margin: 0; color: var(--muted);")


def snapshot_label(name: str) -> Fragment:
    return pysx(t"SnapshotLabel: {name}")
