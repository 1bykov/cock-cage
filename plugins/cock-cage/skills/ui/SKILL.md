---
name: ui
description: Design and build user interfaces for Python apps (FastAPI or Flask with Jinja and HTMX, Streamlit, or plain HTML/CSS) that look deliberate rather than templated. Use when creating or restyling pages, forms, layouts, components, or when asked to make a UI look better.
---

# UI for Python projects

## Pick the stack by job

- **Internal tool or data app, single user:** Streamlit. Fastest path; accept its layout limits.
- **Real web app with forms and state:** FastAPI or Flask + Jinja templates + HTMX for partial updates. Server-rendered HTML, no SPA framework, no build step. Add Alpine.js only for small client-side state.
- **One-off report:** a static HTML file (see the html-dashboard skill).

Match what the project already uses before introducing anything new.

## Design decisions before code

1. **One primary action per screen.** Name it. Everything else is visually secondary.
2. **Tokens, not ad-hoc values.** Define CSS custom properties once: a 4px spacing scale (4, 8, 12, 16, 24, 32, 48), a type scale (13, 15, 18, 22, 28), 2 weights (400, 600), one accent colour, neutrals, and semantic colours (success, warning, danger). Add a dark-mode set under `prefers-color-scheme: dark`.
3. **Hierarchy through size, weight and space** before colour or borders. Group related things with proximity; separate groups with space, not lines.
4. **Content width.** Text columns at 60-75 characters. Forms single-column.

## Component rules

- Buttons: one filled primary per view, secondary as outline or text. Verbs as labels ("Save changes", not "OK").
- Forms: label above field, helper text below, inline validation message next to the field, keep user input on error.
- States: design empty, loading, error and success states for every list and form. HTMX: use `hx-indicator` for loading and return a partial with the error message on 4xx.
- Tables: right-align numbers, tabular figures, sticky header, sortable only where useful.
- Accessibility: real `<button>` and `<label for>`, visible focus ring, colour contrast at least 4.5:1, never colour alone to carry meaning.

## Avoid the template look

No gradients for decoration, no drop shadows on everything, no centred hero with three icon cards by default, no five accent colours. Pick one distinctive choice (a typeface pairing, a bold accent, generous whitespace) and apply it consistently.

## Verify

Render the page and check it at 375px and 1280px wide, in light and dark mode. For HTMX endpoints, add pytest tests with FastAPI's `TestClient` (or Flask's test client) that assert status codes and key fragments of the returned HTML.
