---
description: Set up cock-cage in this project (works for empty and existing projects)
argument-hint: "[--pm uv|pip] [--type-checker pyright|mypy|ty|none] [--no-commit]"
---

Set up cock-cage in the current project by running this in the Bash tool from the project root:

```
.cock-cage/bin/cockcage init $ARGUMENTS
```

It creates a git repository if there is none, adds Python defaults to `.gitignore`, installs the dev tools (ruff, pytest, pytest-cov, the type checker, vulture, deptry, pip-audit) with uv or pip, adds tool settings to `pyproject.toml` only where missing, writes the cock-cage section of `CLAUDE.md`, declares the marketplace in `.claude/settings.json`, commits only the files it created or changed (never the user's uncommitted work), and measures the baseline.

When it finishes, summarise its output for the user in a few lines: what was set up, the baseline numbers, and anything it skipped or left alone.
