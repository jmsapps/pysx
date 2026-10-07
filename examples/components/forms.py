"""Shared form controls, filters and secondary actions."""

from pysx import styled

from .controls import Action

Form = styled.form(t"""
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 10px;
    align-items: center;
""")

Field = styled.input(t"""
    width: 100%;
    min-width: 0;
    min-height: 44px;
    border-radius: 10px;
    border: 1px solid var(--border);
    padding: 10px 14px;
    background: var(--surface-soft);
    color: var(--ink);
    font-size: 16px;
""")

Submit = Action

Meta = styled.p(t"""
    margin: 0;
    color: var(--muted);
    font-size: 13px;
""")

Filters = styled.nav(t"""
    display: flex;
    gap: 4px;
    padding: 4px;
    background: var(--surface-soft);
    border: 1px solid var(--border);
    border-radius: 12px;
""")

FilterButton = styled.button(t"""
    flex: 1;
    min-width: 0;
    min-height: 44px;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 8px 10px;
    background: transparent;
    color: var(--muted);
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
""")

SecondaryAction = styled.button(t"""
    min-height: 44px;
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 10px 16px;
    background: var(--surface);
    color: var(--ink);
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
""")

Remove = styled.button(t"""
    border: none;
    min-height: 44px;
    cursor: pointer;
    background: transparent;
    color: var(--danger);
    font-size: 12px;
    font-weight: 600;
    padding: 8px;
""")

Clear = styled.button(t"""
    align-self: flex-end;
    min-height: 44px;
    border: 1px solid #f1dce0;
    background: #fff5f6;
    color: var(--danger);
    padding: 10px 14px;
    border-radius: 10px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
""")
