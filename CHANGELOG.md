# Changelog

All notable changes to cock-cage. Versions follow semantic versioning. Tags are `cock-cage--v<version>`.

## [Unreleased]

## [0.2.0] - 2026-10-22

### Added
- Runs autonomously. Installs once per machine (`cockcage bootstrap`, run by Claude) and asks once per folder before setting a project up.
- Chat-approved requests: deleting ledger reports and loosening checks need the user's yes, recorded only by the hook (`cockcage request`, `cockcage apply`).
- Finished tasks merge into main automatically; conflicts keep the branch and are reported.
- `cockcage publish` releases a new cock-cage version from the managed source clone and refreshes the machine. Skills are promoted and published by Claude.
- `operating` skill describing how Claude runs cock-cage.
- Quick-mode tasks also get their own branch and checkpoint.

### Changed
- Adding dependencies is allowed and logged as a warning (was: ask).
- Config schema 2, migrated automatically from schema 1.
- A session in an unset folder no longer creates files; it offers setup instead. If Claude's first reply skips the offer, the Stop hook sends it back once to ask.
- Turn ends without a report are work in progress: nothing blocks, and the task is checked, committed and merged when the report is written.

### Fixed (independent review)
- Checkpoints commit only files Claude wrote. Files with the user's uncommitted work, files the user created mid-task, and ignored files are never committed.
- The Stop hook no longer autofixes code the task did not write (only files Claude created). Ruff gets `target-version = "py39"` when no `requires-python` is set.
- Init never adds a tool table whose family already exists (no duplicate `[tool.ruff.lint]`), validates TOML when possible, and leaves an unparseable pyproject alone.
- A fresh repository's first commit leaves out secrets, vendored folders and files over 5 MB; `.gitignore` gains `node_modules/`, `*.pem`, `*.key`, `*.p12`.
- Quick tasks that change nothing end at the next turn end.
- Claude Code's own plans and memory under `~/.claude` are writable again; only settings and plugins are protected.
- Detached HEAD gets a task branch; checkpoints never commit onto a detached HEAD.
- Lint, type and dead-code findings only count in files the task touched.
- Deleting the whole ledger is detected through a copy of the seal kept in `~/.cock-cage`; marker text inside reports is escaped.
- `ship` never reports a merge that did not happen.
- Shell parsing is quote-aware: `>` or `|` inside quotes is data. Git global options (`-C`, `-c`, `--no-pager`) and `bash -c "..."` no longer hide commands.
- Plan approval needs the whole message to be approval ("OK, go ahead"), so feedback like "go back and rethink step 2" no longer approves.
- Init works inside a subdirectory of a larger repository; project releases refuse when `pyproject.toml` has uncommitted edits.

## [0.1.0] - 2026-10-22

### Added
- Plan gate with approval detection, quick mode and automatic task branches.
- Guards for secrets, dangerous git and shell commands, new dependencies, large-file reads and the cage's own files.
- Stop hook that checks tests, removed tests, lint, types, coverage, dead code, complexity, duplicates and dependency hygiene against a background baseline.
- Hash-chained, append-only report ledger with a seal, macOS append-only flag and user-only reseal.
- Checkpoint commits with conventional messages, `ship` and project `release` commands.
- Code map built with `ast`, injected at session start and refreshed after edits.
- Skills: planning, reporting, codebase-navigation, skill-capture, html-dashboard, ui. Agents: explorer, reviewer.
- Skill capture and promotion workflow with usage logging.
- `init` for empty and existing projects with uv or pip.
