"""Shared styled navigation controls and a scrollable chapter layout."""

from pysx import Link as Link
from pysx import styled

from .controls import Card
from .page import Page

NavigationPage = styled(Page)(t"width: min(100%, 800px)")
NavigationLink = styled(Link)(t"""
    display: inline-block;
    padding: 8px 12px;
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--accent);
    text-decoration: none;
    &:hover:
      background: var(--accent-soft);
    &:focus-visible:
      outline: 3px solid #a99cf2;
      outline-offset: 3px;
""")
RouteCard = styled(Card)(t"margin-top: 20px")
ChapterGap = styled.div(t"height: 70svh")
