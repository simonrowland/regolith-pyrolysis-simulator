# DESIGN.md — Regolith Pyrolysis Simulator

Design source of truth for the web UI. The canonical tokens live in
`web/static/css/style.css` (`:root`); this file explains the intent. When they
disagree, the CSS wins — update this doc to match.

## What this UI is

An **APP UI**: a data-dense scientific instrument (a furnace control + ledger
dashboard), not a marketing site. Design serves **legibility, hierarchy, and
truthful data presentation** — never decoration. This tracks the project North
Star: the UI's job is to show the model honestly, not to look pretty. No AI-slop
patterns (no card-grid hero, no purple gradients, no centered everything, no
decorative blobs).

Classifier rules: App UI — calm surface hierarchy, strong typography, few colors,
dense but readable, minimal chrome. Cards only when the card *is* the interaction.

## Typography

- **Typeface:** IBM Plex Sans (technical/engineering character), loaded via CDN
  like the existing Plotly/HTMX/Socket.IO deps, with a full system fallback
  (`-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`) so it
  degrades gracefully offline.
- **Scale** (`--fs-*`): xs 11px (labels/captions), sm 13px (secondary), base 15px
  (body), lg 16px (section h3), xl 18px (panel h2). Headings sit clearly above
  body so hierarchy isn't carried by weight alone.
- **Numerals:** `font-variant-numeric: tabular-nums` globally — this is a numeric
  dashboard; digits must align in tables and not jitter as values update each tick.
- **Weights:** 400 body, 500 controls/links, 600–700 headings & labels.
- Group labels are 11–12px uppercase with 0.5px tracking (uppercase only — never
  letter-space lowercase).

## Color

A CSS-variable system. Light is the default; **dark follows
`prefers-color-scheme`** (matches the lunar moon-glyph brand). `color-scheme:
light dark` so native controls adapt.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#f8f9fa` | `#0e1116` | page background |
| `--surface` | `#ffffff` | `#161b22` | panels, cards, inputs |
| `--border` | `#dee2e6` | `#2d333b` | hairlines |
| `--text` | `#212529` | `#e6edf3` | body (dark uses off-white, not pure white) |
| `--text-secondary` | `#5f656b` | `#9aa4af` | muted labels (both pass WCAG AA ≥4.5:1) |
| `--primary` | `#2563eb` | `#4d90ec` | accent / primary buttons |
| `--accent` | `#059669` | `#2ea868` | success / shuttle |
| `--danger` | `#dc2626` | `#f06262` | error / venting |
| `--warning` | `#d97706` | `#e0a44a` | throttle / debug |

Semantic mapping is conventional: green = success, red = danger, amber = warning.
Filled primary surfaces use dark text in dark mode for contrast on the lighter
accent. Keep the palette ≤12 non-gray colors.

## Charts (Plotly)

- `paper_bgcolor`/`plot_bgcolor: transparent` so charts inherit the page theme.
- Font + gridline colors are derived from `prefers-color-scheme` at load
  (`chartFontColor`, `chartGrid` in `simulator-charts.js`), so charts go dark with
  the UI.
- **Honest hovers.** Stacked-area traces must report the true per-series value,
  not the stacked sum. The O₂ Budget "Vented" trace line sits at stored+vented (so
  the band reads as vented) but carries `customdata = true vented` with a
  `hovertemplate`, so hover shows the actual vented kg. Apply this pattern to any
  future stacked trace.

## Spacing & layout

- Two-column grid: 300px config sidebar + flexible main area (`max-width: 1600px`).
- Flexible columns and chart containers set `min-width: 0` so responsive charts
  reflow instead of forcing horizontal overflow.
- Section content uses bordered `.section-card` panels; the condensation train is a
  horizontal `overflow-x: auto` strip.
- **Spacing scale:** `--space-1..8` (rem-based, so spacing scales with the user's
  font size). All padding/margin/gap reference tokens, not raw px. Charts are
  Plotly `responsive:true`, so they re-fit the container on load/resize.

## Motion

Subtle, functional only (bar fills, hover, scale toggles). `prefers-reduced-motion:
reduce` is respected (near-instant transitions). No `transition: all` in new code —
list properties explicitly.

## Accessibility

- Semantic landmarks: `nav`/`main`/`aside`/`section`/`fieldset`/`legend`.
- Form controls: visible group legends; `aria-label` on the engine/feedstock
  selects; additive inputs wrap their labels.
- Live region: `role="status" aria-live="polite"` on the status text; `role=alert`
  on VENTING. **Scope live regions to status/alerts, not per-tick numeric spans**
  (announcing every simulated hour would flood a screen reader).
- `:focus-visible` ring on all interactive elements (never `outline: none` without
  a replacement).
- Contrast: body ~12:1; muted labels ≥4.5:1; status badges (THROTTLED/VENTING/
  DEBUG) ≥4.6:1 — all in both themes (amber badges use dark text; VENTING uses
  dark text in dark mode, white on red in light).
- **Note:** mobile touch targets are desktop-density (this is a desktop-primary tool).

## Responsive

Desktop-primary local workbench. Layout collapses to one column at `max-width:
960px`. Mobile is usable (config panel fits); a small residual overflow remains
from the chart/condensation-train internals — low priority given the usage.

## Known debt (prioritized for future design passes)

1. `game.css` (Lunar Operator, hidden stub) hardcodes ~10 hex chip colors —
   tokenize and dedupe against the shared vars.
2. Optional explicit light/dark toggle (theme is OS-driven only today).
3. Delightful motion + performance polish (self-host the CDN deps for offline +
   lazy-load) — the last lever to a clean A.
