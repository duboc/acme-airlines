# DESIGN.md — ACME operations console

## Mode and scene
Operate. A duty controller at a desk in a mixed-light ops room, glancing between screens. Light theme as the default (paper-like, low glare); dark is not shipped in the scaffold.

## Colour (OKLCH, tinted neutrals, no pure black/white)
| Token | Value | Use |
|---|---|---|
| `--ink` | `oklch(0.24 0.04 255)` | text, 13:1 on surface |
| `--ink-soft` | `oklch(0.42 0.03 255)` | secondary text (7:1) |
| `--surface` | `oklch(0.985 0.004 250)` | page |
| `--panel` | `oklch(0.96 0.008 250)` | panels, nav |
| `--line` | `oklch(0.88 0.012 250)` | hairlines |
| `--navy` | `oklch(0.30 0.07 255)` | brand, header, primary |
| `--signal` | `oklch(0.58 0.17 50)` | single attention accent, used sparingly |
Status always pairs colour with a text label and icon shape.

## Typography
IBM Plex Sans Variable (self-hosted via Fontsource); tabular numerals for times and flight data. Scale ratio ~1.25: 0.875 / 1 / 1.25 / 1.563 rem. Body measure ≤ 70ch. Headings balanced.

## Layout
Header bar (brand, simulated-data notice, route nav) over a single content region. 4px spacing base; separation by space and hairlines, not nested cards. Console is for a multi-column layout (timeline / journey / guardrail) in later issues; stacked below 56rem.

## Components (scaffold)
- App shell with skip link, `nav` with `aria-current="page"` links.
- Focus ring: 3px `--signal` outline, 2px offset, on every interactive element.
- Selection and scrollbars themed from the palette.

## Motion
Minimal; 150ms ease-out colour transitions only, disabled under `prefers-reduced-motion`.
