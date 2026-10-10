# DESIGN.md — ACME Observatory dashboard

Inherits the ACME operations console system (`/DESIGN.md`); only differences are listed.

## Mode and scene
Operate. A product owner glancing at a second monitor, mixed light. Light theme only.

## Colour (OKLCH, tinted neutrals)
| Token | Value | Use |
|---|---|---|
| `--ink` | `oklch(0.24 0.04 255)` | text (13:1 on surface) |
| `--ink-soft` | `oklch(0.42 0.03 255)` | secondary text (7:1) |
| `--surface` | `oklch(0.985 0.004 250)` | page |
| `--panel` | `oklch(0.96 0.008 250)` | panel background |
| `--line` | `oklch(0.88 0.012 250)` | hairlines |
| `--navy` | `oklch(0.30 0.07 255)` | header, brand |
| `--signal` | `oklch(0.58 0.17 50)` | focus ring only |
| `--warn` | `oklch(0.45 0.11 70)` | lagging text on `--warn-bg` `oklch(0.95 0.05 85)` (>=7:1) |
| `--bad` | `oklch(0.42 0.15 27)` | unavailable text on `--bad-bg` `oklch(0.95 0.03 25)` (>=7:1) |

## Typography
IBM Plex Sans Variable, tabular numerals. Scale 0.8125 / 0.875 / 1 / 1.25 rem. Panel titles 1rem semibold.

## Layout
Header bar (factory name, `as_of`, optional data-mode badge) over `main`. Five panels in a CSS grid:
`auto-fit, minmax(22rem, 1fr)`; Spend and Agents span two columns when width allows. Single column
below 40rem. 4px spacing base, hairline borders, no nested cards.

## Components
- **Panel frame** (`<section aria-labelledby>`): title `h2`, state chip, `as_of`, then body.
  State chip text: `Live` (ok), `No data yet` (empty), `Delayed` (lagging), `Unavailable` (unavailable),
  plus `Loading…` and `Failed to load` for the client. Each carries a distinct glyph (●, ○, ▲, ■).
  Non-ok states show the API `message` in a `role="status"` line; `lagging` still renders the body.
- Slices #119/#120 supply panel bodies as `children` of the frame.

## Motion
None beyond 150ms colour transitions; disabled under `prefers-reduced-motion`.

## Keyboard and screen-reader navigation
Tab order: skip link → interactive controls only (range toggle, `<details>` summaries) → scrollable table regions.
Panels are `<section>` landmarks named by their `h2` (screen-reader landmark/heading navigation); static panels
are deliberately **not** tab stops. Any container that can overflow (Spend daily table, Agents table, PR table)
is wrapped in `ScrollRegion`: `role="region"`, `aria-label`, `tabIndex={0}`, with the standard focus ring, so
axe's `scrollable-region-focusable` holds at 320/768/1280px. Manual axe check: open the page at each width and
run axe DevTools; expect 0 violations.
