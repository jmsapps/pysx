"""Shared checkable list rows."""

from pysx import styled

List = styled.ul(t"""
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
""")

Item = styled.li(t"""
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 6px 10px 6px 16px;
    gap: 10px;
""")

Row = styled.label(t"""
    display: flex;
    align-items: center;
    gap: 12px;
    flex: 1;
    min-width: 0;
    min-height: 44px;
    cursor: pointer;
""")

Checkbox = styled.input(t"""
    width: 18px;
    height: 18px;
    margin: 0;
    flex-shrink: 0;
    accent-color: var(--accent);
    cursor: pointer;
""")

Text = styled.span(t"""
    flex: 1;
    font-size: 14px;
    overflow-wrap: anywhere;
""")
