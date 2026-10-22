---
description: Set up cock-cage in this project (works for empty and existing projects)
argument-hint: "[--pm uv|pip] [--type-checker pyright|mypy|ty|none]"
---

Set up cock-cage in the current project. Run its init command from the project root with the Bash tool:

- In a folder that is not set up yet, the exact command is in your context from the cock-cage hook: `python3 "<plugin path>/scripts/cc.py" init $ARGUMENTS`.
- In a project that already has `.cock-cage/`, use `.cock-cage/bin/cockcage init $ARGUMENTS`.

It creates a git repository if there is none, adds Python defaults to `.gitignore`, installs the dev tools with uv or pip, adds tool settings to `pyproject.toml` only where missing, writes the cock-cage section of `CLAUDE.md`, commits only the files it created or changed (never the user's uncommitted work), and measures the baseline.

Afterwards, summarise for the user in a few lines: what was set up, the baseline numbers, and anything skipped or left alone.
