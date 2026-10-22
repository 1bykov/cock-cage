# cock-cage

A Claude Code plugin that keeps Claude honest in Python projects. It makes Claude plan before it builds, checks every change against the state of the project before the change, writes a short educational report for each finished task into a tamper-evident ledger, and commits each task as a clean checkpoint on its own branch.

Everything runs on the standard library of Python 3.9 or newer, so macOS's built-in `python3` is enough to run the hooks.

## What it enforces

| Check | How | Default |
|---|---|---|
| Edit before an approved plan | plan file + your approval | block |
| New test failures | pytest against the session baseline | block |
| Tests that disappeared | pytest test ids against the baseline | block (unless documented in the report) |
| New lint or type errors | ruff, pyright (or mypy, ty) against the baseline | block |
| Finishing without a report | report draft check | block |
| Large diff without a review | `cock-cage:reviewer` subagent must run | block |
| Touching secrets | `.env`, keys, `.ssh`, credentials | block |
| Dangerous git or shell commands | force push, `reset --hard`, `rm -rf ~`, commits on `main` | block |
| Adding a dependency | `uv add`, `pip install` and friends | asks you |
| Reading a large file whole | line count | block |
| Duplicated code | built-in copy-paste and same-shape detectors | warn |
| Dead code, complexity, coverage drops, dependency hygiene | vulture, ruff C901, pytest-cov, deptry | warn |
| Vulnerable dependencies, missing docstrings | pip-audit (daily), ast | noted in report |

"Block" means Claude cannot carry on until it fixes the problem. "Warn" means Claude is told and the ledger records it. Only problems the task introduced count. Pre-existing failures are measured at session start and never blamed on the task.

Claude cannot loosen its own cage. Edits to the ledger, the config, the state file and the project's Claude settings are refused by hooks and by deny rules.

## Install once per machine

```bash
# 1. Your local clone: needed to grow the skills library and to release new versions
git clone git@github.com:1bykov/cock-cage.git ~/code/cock-cage

# 2. Register the marketplace with Claude Code (uses your git credentials for a private repo)
claude plugin marketplace add 1bykov/cock-cage

# 3. A terminal command for the things only you may do
echo "alias cockcage='python3 ~/code/cock-cage/plugins/cock-cage/scripts/cc.py'" >> ~/.zshrc
source ~/.zshrc
cockcage config source-repo ~/code/cock-cage

# 4. Optional but recommended: symbol navigation through a language server
uv tool install pyright          # provides pyright-langserver
claude plugin install pyright-lsp@claude-plugins-official
```

## Add it to a project

The same two steps work for an empty folder and for a project with existing code:

```bash
cd ~/code/my-project
claude plugin install cock-cage@cock-cage --scope project
cockcage init
```

`init` creates a git repository if there is none, adds Python defaults to `.gitignore`, installs ruff, pytest, pytest-cov, pyright, vulture, deptry and pip-audit as dev dependencies (uv if available, otherwise pip into `.venv`), adds tool settings to `pyproject.toml` only where missing, writes a section into `CLAUDE.md`, declares the marketplace with auto-update in `.claude/settings.json`, and measures the baseline.

- Empty project: everything goes into a first commit.
- Existing project: only the files `init` created or changed are committed. Your uncommitted work is left exactly as it was, and existing failures, lint issues and type errors become the baseline instead of blocking you.

You can also run `/cock-cage:init` from inside Claude Code instead of the terminal command.

## Daily use

1. Ask for a change. Claude explores (code map, LSP, explorer subagent) and writes `.cock-cage/plan.md`.
2. Reply "approve" (or "yes", "go ahead", "lgtm"). Work moves to a `cc/<task>` branch.
3. Claude builds. After each edit it gets lint feedback and runs the matching test file.
4. Claude writes `.cock-cage/report-draft.md` and shows it to you. The Stop hook runs the checks, appends the report with machine-generated evidence to `.cock-cage/reports.md`, and commits a checkpoint with a conventional message.
5. When you are happy, `cockcage ship` merges the task branch into `main`.

Start a message with `quick:` to skip planning for trivial changes. Tests, checks and the report still apply.

## Reports

Each ledger entry has the same shape: What changed, Why, Concept (one idea worth learning, pointing at the line), Watch out, then Evidence written by the hook (diff stats, test counts against the baseline, lint, types, coverage), Warnings and Noted items. Claude writes only the narrative, so it cannot overstate results.

The ledger is append-only. Every entry records the SHA-256 of everything above it, and `ledger.seal` records the hash of the whole file, so any edit or truncation is detected at the next session start. On macOS the files also carry the user append-only flag, so overwriting or deleting them fails at the OS level. To remove entries yourself:

```bash
cockcage ledger unlock
# edit .cock-cage/reports.md
cockcage ledger reseal
```

The ledger lives only on your machine (`.cock-cage/` is gitignored except `config.json`), so it never causes merge conflicts.

## Versioning

The plugin itself:

- `plugins/cock-cage/.claude-plugin/plugin.json` is the single source of truth for the version. Projects stay on the version they have until it changes.
- `python3 scripts/release.py patch|minor|major --push` runs the tests, validates the manifests, bumps the version, rolls `CHANGELOG.md`, commits, tags `cock-cage--vX.Y.Z` and pushes.
- Projects initialised by cock-cage have auto-update on, so they pick up a release at their next session. Otherwise run `claude plugin update cock-cage@cock-cage`.
- Each ledger entry records the plugin version that produced it, and config changes between versions are migrated automatically at session start.

Your projects:

- One branch per task, one checkpoint commit per finished task, conventional commit messages (`feat:`, `fix:`, `refactor:` and so on), never a commit on `main` by Claude.
- `cockcage ship [--squash]` merges a finished task branch.
- `cockcage release patch|minor|major` bumps `[project].version`, writes a `CHANGELOG.md` section grouped from the conventional commits since the last tag, commits and tags `vX.Y.Z`.

## Growing the skills library

Skills ship inside the plugin: `planning`, `reporting`, `codebase-navigation`, `skill-capture`, `html-dashboard` and `ui`. Only their one-line descriptions are loaded until one is needed.

1. After a task you liked, run `/cock-cage:capture-skill <topic>`. Claude drafts `.cock-cage/skill-drafts/<name>/SKILL.md`.
2. Review and promote it in your terminal: `cockcage skill promote <name>`. It shows the diff, asks for confirmation, copies it into your clone and commits.
3. Release: `python3 scripts/release.py patch --push`. Every project gets the new skill.

`cockcage skill usage` shows how often each skill fires across your projects, so you can prune the ones that never earn their place.

## Commands

| Where | Command | Purpose |
|---|---|---|
| Claude | `/cock-cage:init` | set up the project |
| Claude | `/cock-cage:status` | task phase, baseline, ledger health, tools |
| Claude | `/cock-cage:approve` | approve the current plan |
| Claude | `/cock-cage:capture-skill` | draft a skill from recent work |
| Terminal | `cockcage init` | set up the project |
| Terminal | `cockcage status` | same as above |
| Terminal | `cockcage levels [check level]` | show or change enforcement levels (you only) |
| Terminal | `cockcage ledger verify\|show\|unlock\|reseal` | manage the ledger (unlock and reseal are you only) |
| Terminal | `cockcage skill list\|usage\|promote <name>` | manage skills (promote is you only) |
| Terminal | `cockcage ship [--squash]` | merge the task branch (you only) |
| Terminal | `cockcage release patch\|minor\|major` | version the project |

Inside a project, `.cock-cage/bin/cockcage` is also available. It always points at the plugin version the last session loaded.

## Limits worth knowing

- Hooks fail open. If cock-cage itself crashes, the error goes to `.cock-cage/hook-errors.log` and Claude carries on, so a bug never bricks a session.
- Shell commands are checked by pattern. A determined agent could still write a script that edits a protected file. The hash chain makes that visible even when prevention fails.
- Duplicate detection catches copy-paste and renamed copies. The same idea written in a different shape needs the reviewer subagent.
- Without a finished baseline (for example if Claude edits within seconds of the session starting), test failures downgrade to warnings and the report says so.

## Development

```bash
python3 -m pytest -q tests                 # unit and hook-protocol tests
python3 tests/e2e/run_e2e.py /tmp/cc-e2e   # real tools on an empty and an existing project
claude plugin validate --strict plugins/cock-cage
```

CI runs both suites on Ubuntu and macOS with Python 3.9 and 3.13.
