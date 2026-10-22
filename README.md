# cock-cage

A Claude Code plugin that keeps Claude honest in Python projects, and runs itself. Claude plans before it builds, every change is checked against the state of the project before the change, each finished task gets a short educational report in a tamper-evident ledger, and work is committed and merged on its own. You never need a terminal.

The hooks need only the standard library of Python 3.9 or newer, so macOS's built-in `python3` is enough.

## Getting started

Tell Claude Code on your Mac, once:

> Set up cock-cage on this machine: clone https://github.com/1bykov/cock-cage to ~/.cock-cage/src and run `python3 ~/.cock-cage/src/plugins/cock-cage/scripts/cc.py bootstrap`.

Claude clones the source, registers the marketplace, installs the plugin for every project, turns on auto-update and adds the Python language server. Restart Claude Code once afterwards.

From then on, whenever you open Claude in a project folder (empty or with existing code), its first reply asks whether to set cock-cage up there. Say yes and it does the rest:

- Empty folder: git repository, `pyproject.toml`, dev tools and a first commit.
- Existing project: dev tools and config are added and committed on their own. Your uncommitted work is left exactly as it was, and existing test failures, lint and type errors become the baseline, so they are never blamed on new work.

Say no and it never asks again in that folder. Ask for it later by saying "set up cock-cage here".

## Working with it

1. Ask for a change. Claude explores (code map, language server, explorer subagent) and writes a plan.
2. Reply "approve", "yes" or "go ahead". Work moves to a `cc/<task>` branch.
3. Claude builds. After each edit it gets lint feedback and runs the matching tests.
4. Claude writes a short report and shows it to you. The Stop hook runs the checks, appends the report with machine-generated evidence to the ledger, commits the task and merges it into main.

Start a message with `quick:` to skip planning for trivial changes. Tests, checks and the report still apply.

## What needs your yes

Approving plans, and two things that protect the integrity of the cage:

- **Deleting ledger reports.** Ask Claude to remove a report and it files a request and asks you to confirm.
- **Loosening a check.** For example, turning test failures from blocking into warnings.

Your reply is read by a hook, which records the approval. Claude cannot record it itself, and the command that carries the action out refuses without it.

Everything else Claude does on its own: setting up projects, adding dependencies (noted in the report), merging, releasing project versions, and growing and publishing the skills library.

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
| Reading a large file whole | line count | block |
| Edits to the ledger, config, state or Claude settings | hooks and deny rules | block |
| Adding a dependency | `uv add`, `pip install` and friends | warn |
| Duplicated code | built-in copy-paste and same-shape detectors | warn |
| Dead code, complexity, coverage drops, dependency hygiene | vulture, ruff C901, pytest-cov, deptry | warn |
| Vulnerable dependencies, missing docstrings | pip-audit (daily), ast | noted in report |

"Block" means Claude cannot carry on until it fixes the problem. "Warn" means Claude is told and the ledger records it. Only problems the task introduced count.

## Reports

Each ledger entry has the same shape: What changed, Why, Concept (one idea worth learning, pointing at the line), Watch out, then Evidence written by the hook (diff stats, tests against the baseline, lint, types, coverage), Warnings and Noted items. Claude writes only the narrative, so it cannot overstate results.

The ledger (`.cock-cage/reports.md`) is append-only. Every entry records the SHA-256 of everything above it and a seal records the hash of the whole file, so any edit or truncation shows up at the next session start. On macOS the file also carries the user append-only flag, which blocks overwrites and deletion at the OS level. It stays on your machine and is never committed. Ask Claude "what did we do last week?" and it answers from the ledger.

## Versioning

- **The plugin.** `plugin.json` is the single source of truth. Claude publishes new versions with `cockcage publish`: tests, version bump, changelog, tag, push. Every project picks the release up through auto-update at its next session. Each report records the plugin version that wrote it, and config changes between versions are migrated automatically.
- **Your projects.** One branch and one checkpoint commit per task, conventional commit messages (`feat:`, `fix:` and so on), merged into main when the task is done, never committed on main directly. Ask Claude to release a version and it bumps `[project].version`, writes a `CHANGELOG.md` section from the commits and tags it.

## Growing the skills library

Built-in skills: `planning`, `reporting`, `codebase-navigation`, `operating`, `skill-capture`, `html-dashboard` and `ui`. Only their one-line descriptions load until one is needed.

Say "save this as a skill" after a task you liked, or run `/cock-cage:capture-skill`. Claude drafts the skill, promotes it into the source and publishes a new version, so every project has it from its next session. Usage is logged, so you can ask Claude which skills never fire.

## Limits worth knowing

- Hooks fail open. If cock-cage itself crashes, the error goes to `.cock-cage/hook-errors.log` and Claude carries on, so a bug never bricks a session.
- Shell commands are checked by pattern. A determined agent could still write a script that edits a protected file. The hash chain makes that visible even when prevention fails.
- Duplicate detection catches copy-paste and renamed copies. The same idea written in a different shape needs the reviewer subagent.
- If Claude edits within seconds of a session starting, before the baseline finishes, test failures downgrade to warnings and the report says so.
- On macOS the ledger's append-only flag also stops Finder, `rm -rf` and `git clean -fdx` from deleting it. Ask Claude to unlock it first; it will ask you to confirm.

## Reference: commands Claude runs

| Command | Purpose |
|---|---|
| `cockcage bootstrap` | one-time machine setup, safe to re-run |
| `cockcage init` | set up a project (refused unless you agreed) |
| `cockcage status` | task phase, baseline, ledger health, tools |
| `cockcage release patch\|minor\|major` | version a project |
| `cockcage skill list\|usage\|promote <name>` | manage skills |
| `cockcage publish patch\|minor\|major` | release a new cock-cage version |
| `cockcage request ledger-delete\|ledger-reseal\|loosen ...` | ask for your approval |
| `cockcage apply <id>` | carry out an approved request |
| `cockcage levels [check level]` | show or tighten checks |

Inside a project the CLI is `.cock-cage/bin/cockcage`, refreshed each session to point at the active plugin version.

## Development

```bash
python3 -m pytest -q tests                 # unit, hook-protocol and autonomy tests
python3 tests/e2e/run_e2e.py /tmp/cc-e2e   # real tools on an empty and an existing project
claude plugin validate --strict plugins/cock-cage
```

CI runs both suites on Ubuntu and macOS with Python 3.9 and 3.13.
