---
name: planning
description: Write the cock-cage plan (.cock-cage/plan.md) before changing any project file, and get the user's approval. Use at the start of every non-trivial task, when the plan gate blocks an edit, or when the user asks you to plan.
---

# Planning a change

cock-cage blocks edits to project files until the user approves a plan. A good plan is short enough to read in a minute and specific enough that the user can spot a wrong turn before any code exists.

## Before writing the plan

1. Read `.cock-cage/codemap.md` (or the index injected at session start) to find the relevant modules.
2. For anything beyond a single file, delegate discovery to the `cock-cage:explorer` subagent. Ask it what already exists that the change could reuse, and which tests cover the area.
3. If the request is ambiguous in a way that changes the design, ask one question first. Do not plan around a guess.

## Write `.cock-cage/plan.md`

Use exactly this shape:

```markdown
# <imperative title, e.g. "Add retry logic to price fetching">

## Goal
<one or two sentences: what will be true when this is done>

## Approach
<the design in 3-6 lines. Name the existing functions or modules you will reuse.>

## Rejected alternative
<one option you considered and why you did not choose it>

## Files
- path/to/file.py: <what changes>

## Tests
- <which tests you will add or change, and what each proves>

## Steps
1. <small, verifiable steps; each should leave the code working>
```

Keep it under about 250 words. The title becomes the branch name, the commit subject and the report title, so make it a clear imperative.

## Ask for approval

Show the plan to the user in chat (a short summary is fine, the file holds the detail) and ask them to approve it. Approval is detected from their reply ("approve", "yes", "go ahead", "lgtm"). If they push back, revise the plan file and ask again.

After approval, cock-cage moves the work to a `cc/<title>` branch if you were on `main`.

## Trivial work

If the user starts a message with `quick:`, the plan gate is off for that task. Tests, checks and the report still apply when you finish.
