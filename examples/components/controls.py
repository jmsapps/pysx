"""Reusable controls and headings for the example apps."""

from pysx import button, div, h1, p, span, styled

Action = styled(button, t"""
    border: 1px solid transparent;
    border-radius: 10px;
    min-height: 44px;
    padding: 10px 18px;
    font: inherit;
    font-weight: 600;
    color: white;
    background: var(--accent);
    box-shadow: 0 2px 4px rgba(66, 49, 160, 0.14);
    cursor: pointer;
""")

Title = styled(h1, t"""
    margin: 0;
    font-size: clamp(28px, 4vw, 34px);
    line-height: 1.2;
    letter-spacing: -0.035em;
""")

Eyebrow = styled(p, t"""
    margin: 0 0 10px;
    color: var(--accent);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
""")

Description = styled(p, t"""
    margin: 10px 0 0;
    color: var(--muted);
    font-size: 14px;
    line-height: 1.7;
""")

Card = styled(div, t"""
    padding: 22px;
    background: var(--surface-soft);
    border: 1px solid var(--border);
    border-radius: 16px;
    display: flex;
    flex-direction: column;
    gap: 14px;
""")

Actions = styled(div, t"""
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
""")

Metric = styled(p, t"""
    margin: 0;
    padding: 36px 24px;
    display: flex;
    justify-content: center;
    align-items: baseline;
    gap: 12px;
    color: var(--muted);
    background: var(--surface-soft);
    border: 1px solid var(--border);
    border-radius: 16px;
""")

Value = styled(span, t"""
    color: var(--ink);
    font-size: clamp(56px, 8vw, 80px);
    line-height: 1;
    font-weight: 650;
    letter-spacing: -0.06em;
    font-variant-numeric: tabular-nums;
""")
