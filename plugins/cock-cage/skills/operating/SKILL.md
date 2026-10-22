---
name: operating
description: Run cock-cage on the user's behalf - set up projects, release versions, merge work, grow and publish skills, and handle requests that need the user's yes (deleting reports, loosening checks). Use whenever cock-cage itself needs managing, or the user asks about its status, a release, a report or a check level.
---

# Operating cock-cage

The user does not touch a terminal. You run every cock-cage command yourself through the Bash tool and tell them what happened in plain words.

The CLI is `.cock-cage/bin/cockcage` inside a set-up project. Outside one (or before setup), use the absolute `python3 ".../scripts/cc.py"` path given in your session context.

## What you do on your own

| Situation | Command |
|---|---|
| The user agreed to set up a folder | `cockcage init` (the hook refuses if they have not agreed) |
| Check health | `cockcage status`, `cockcage ledger verify` |
| Ship a project version (the user asks, or a sensible milestone is reached and they agree) | `cockcage release patch\|minor\|major` |
| A skill draft is ready (see the skill-capture skill) | `cockcage skill promote <name> --yes`, then `cockcage publish patch` |
| A cock-cage bug or improvement was fixed in its source | `cockcage publish patch` |
| Tighten a check | `cockcage levels <check> block` |
| First-time setup on a new machine | `python3 <clone>/plugins/cock-cage/scripts/cc.py bootstrap` |

Merging is automatic: when a task's report is logged, the Stop hook commits the checkpoint, merges the `cc/` branch into main and deletes it. If the merge conflicts, it keeps the branch and the report says so; resolve it with the user.

Adding a dependency is allowed. Say why in the report.

## What needs the user's yes in chat

Two things: deleting ledger reports, and loosening a check. File a request, ask, wait:

```
cockcage request ledger-delete 3 5 --reason "duplicate entries from a crashed session"
cockcage request ledger-reseal --reason "the user edited the ledger by hand"
cockcage request loosen tests warn --reason "integration tests need a database we don't have here"
```

Then ask in one plain sentence and give them the id, e.g. "Shall I delete reports #3 and #5? They are duplicates from the crashed session. Reply 'yes 3f2a' to allow it." Only a reply containing the id counts, and only the hook records it. When it tells you the request is approved, run `cockcage apply <id>`. If they say no, drop it. One request can be open at a time. Never try another route to the same effect: no calling hooks by hand, no editing state or config, no hiding that you are running a command.

## Reading the ledger

`.cock-cage/reports.md` holds one entry per finished task. When the user asks what happened, summarise from it rather than from memory, and point to report numbers.
