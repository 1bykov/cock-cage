---
name: html-dashboard
description: Build a self-contained HTML dashboard (KPI tiles, charts, filterable table) from Python data, openable in a browser with no server. Use when asked for a dashboard, an HTML report, a metrics page, or to visualise results from pandas, SQL or CSV.
---

# HTML dashboards from Python

Output is one `.html` file with the data embedded as JSON and charts drawn in the browser. No build step, no server, works offline except for the chart library CDN.

## Decide first

- **Audience and question.** Write down the 1-3 questions the dashboard answers. Every tile and chart must serve one of them. Cut the rest.
- **Grain.** Aggregate in Python (pandas or SQL), not in JavaScript. Ship only the rows the page needs; above roughly 50k rows, pre-aggregate.
- **Chart type by question.** Trend over time → line. Compare categories → horizontal bar, sorted. Part of whole with 2-4 parts → stacked bar, not pie. Distribution → histogram. Relationship → scatter. Exact values → table.

## Build

1. In Python, produce a dict: `{"kpis": [...], "series": {...}, "rows": [...], "generated_at": ...}`. Use `json.dumps(data, default=str)` so dates and decimals serialise.
2. Fill `template.html` (in this skill's folder) by replacing the `/*__DATA__*/null` placeholder with the JSON. Use `str.replace`, not f-strings, because the template contains braces. Escape `</` as `<\/` in the JSON to keep the script tag intact.
3. Write the file next to the project's outputs (e.g. `reports/dashboard.html`) and print its path.

## Design rules

- Order: KPI row first (3-5 tiles: value, label, change vs previous period with an up/down marker that does not rely on colour alone), then the main chart, then supporting charts, then the detail table.
- One accent colour plus greys. Use colour to encode meaning (good/bad, selected/unselected), never decoration. Respect `prefers-color-scheme` for dark mode.
- Label axes with units. Format numbers for humans (`12.4k`, `£1.2m`, `3.1%`). Start bar axes at zero.
- Responsive grid: tiles wrap on narrow screens, charts keep a minimum height of 240px.
- Accessibility: every chart gets a short text summary above it; the table is a real `<table>` with headers.

## Test

Add a pytest that builds the dashboard from a tiny fixture and asserts the file exists, contains the JSON payload and has no leftover placeholder. Do not snapshot the whole HTML.
