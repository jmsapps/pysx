"""Shared visual tokens and document defaults for example apps."""

from pysx import global_style

global_style("""
    :root {
      color-scheme: light;
      --canvas: #f6f7fb;
      --surface: #ffffff;
      --surface-soft: #f8f9fc;
      --ink: #192338;
      --muted: #66738a;
      --border: #e4e8f0;
      --accent: #6554d9;
      --accent-hover: #5342c3;
      --accent-soft: #eeebfc;
      --danger: #b54450;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      color: var(--ink);
      background: var(--canvas);
    }
    *, *::before, *::after { box-sizing: border-box; }
    body { margin: 0; }
    #pysx-root {
      min-height: 100svh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 48px 24px;
      background:
        radial-gradient(ellipse at 15% 10%, #eeebfc 0, transparent 45%),
        radial-gradient(ellipse at 90% 90%, #eaf0fa 0, transparent 45%),
        var(--canvas);
    }
    button, input { font: inherit; }
    button { transition: background-color 150ms, border-color 150ms, box-shadow 150ms; }
    button:hover { filter: brightness(0.96); }
    button:active { transform: translateY(1px); }
    button:focus-visible, input:focus-visible {
      outline: 3px solid #a99cf2;
      outline-offset: 3px;
    }
    button:disabled { opacity: 0.5; cursor: not-allowed; }
    .is-active {
      background: var(--accent-soft) !important;
      color: var(--accent) !important;
      border-color: #ddd6f8 !important;
    }
    .is-done .todo-text { text-decoration: line-through; color: var(--muted); }
    h2 { margin: 0; font-size: 17px; letter-spacing: -0.02em; }
    p { margin: 0; }
    pysx-list { display: contents; }
    @media (max-width: 600px) {
      #pysx-root { padding: 24px 16px; }
    }
    @media (prefers-reduced-motion: reduce) {
      button { transition: none; }
      button:active { transform: none; }
    }
""")

PAGE_CSS = """
    width: min(100%, 680px);
    margin: auto;
    font-size: 15px;
    line-height: 1.6;
    display: flex;
    flex-direction: column;
    gap: 24px;
    align-items: stretch;
    padding: clamp(24px, 5vw, 40px);
    background: var(--surface);
    color: var(--ink);
    border: 1px solid var(--border);
    border-radius: 24px;
    box-shadow: 0 16px 48px -20px rgba(31, 42, 68, 0.22), 0 2px 8px rgba(31, 42, 68, 0.03);
"""
