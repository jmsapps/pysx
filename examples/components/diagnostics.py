"""Shared source and diagnostic display for the authoring example."""

from pysx import styled

SourceCode = styled.pre(t"""
    margin: 0;
    padding: 16px;
    border: 1px solid var(--border);
    border-radius: 10px;
    color: var(--ink);
    background: var(--surface);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 13px;
    line-height: 1.6;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
""")
