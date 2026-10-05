# Example theme

The examples share a quiet light theme: a cool off-white canvas, white cards, slate
text, and a violet accent. `theme.py` owns the CSS tokens and document defaults;
`page.py` and `controls.py` own reusable layouts and controls.

- Center the main card in the viewport, with a maximum width of 680px. Longer pages
  grow naturally and scroll. Keep 16px outside the card on narrow screens.
- Use `Page` for the main card and `Card` for related content inside it. Borders stay
  subtle; shadows belong to the main card. Use `page(...)` for width/layout overrides.
- Group headings with `Eyebrow`, `Title`, and `Description`. Keep body copy muted,
  short, and readable. Use `Metric` and `Value` for prominent numeric readouts, and
  `Reading` with `Result` for dense reference rows where many values share a card.
- Use `CompactPage` for focused forms. `Form`, `Field`, `Submit`, `Filters`, and
  `FilterButton` share control sizing and colors. `List`, `Item`, `Row`, `Checkbox`,
  and `Text` provide checkable rows; `Remove` and `Clear` provide destructive actions.
- Group buttons in wrapping `Actions` rows. `Action` is the primary control; secondary
  controls use light surfaces and borders. Keep interactive targets at least 44px tall.
- Use the shared `--ink`, `--muted`, `--border`, `--accent`, `--accent-soft`, and
  `--danger` variables in example-specific styles. Avoid separate page palettes.
- Preserve visible keyboard focus, reduced-motion preferences, and mobile layouts.
  Keep signals inside ordinary flow elements so flex/grid does not split inline text.

Verify changes with the example runner and the existing browser acceptance suite.
Check desktop and narrow viewports, live updates, and keyboard focus.
