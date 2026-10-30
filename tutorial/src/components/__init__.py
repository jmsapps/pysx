"""Shared teaching controls, independent of the bundled example package."""

from pysx import styled

Button = styled.button(t"""
    font: inherit;
    font-weight: 600;
    padding: 10px 16px;
    min-height: 44px;
    max-width: 100%;
    line-height: 1.4;
    border: 1px solid var(--accent);
    border-radius: 8px;
    color: var(--surface);
    background: var(--accent);
    cursor: pointer;
    &:focus-visible:
      outline: 3px solid var(--ink);
      outline-offset: 3px;
    &:disabled:
      opacity: 0.5;
""")
Field = styled.input(t"""
    font: inherit;
    display: block;
    min-height: 44px;
    padding: 8px 12px;
    max-width: 100%;
    min-width: 0;
    margin-bottom: 12px;
    border: 1px solid var(--line);
    border-radius: 6px;
    color: var(--ink);
    background: var(--surface);
""")
Actions = styled.div(t"""
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    margin: 12px 0;
    & > input:
      margin-bottom: 0;
""")
Panel = styled.section(t"""
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 20px;
    margin: 18px 0;
    background: var(--surface);
    min-width: 0;
""")
Readout = styled.output(t"""
    display: block;
    padding: 12px 0;
    overflow-wrap: anywhere;
""")
