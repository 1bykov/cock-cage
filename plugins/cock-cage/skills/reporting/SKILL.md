---
name: reporting
description: Write the end-of-task report (.cock-cage/report-draft.md) that cock-cage logs to the ledger. Use when you have finished a change, before your final message, or when the Stop hook says a report is missing or too long.
---

# Reporting finished work

The report explains what happened to someone who did not watch you work. It should teach as well as inform, and it must stay short. The Stop hook adds the hard evidence (diff stats, test results, lint and type counts, warnings), so you write only the narrative.

## Write `.cock-cage/report-draft.md`

```markdown
# <the plan title, or a clear imperative title>

## What changed
<2-4 lines. Files and functions in plain words, e.g. "fetch_prices() in data/client.py now retries failed requests.">

## Why
<the decision in 1-3 sentences, including the main alternative you rejected and why>

## Concept (<name of the idea>)
<2-3 sentences teaching the single idea worth learning from this change: a pattern, a library feature, a language mechanism or a design principle. Point to the exact place, e.g. "See client.py line 42.">

## Watch out
<only if there is a real risk, trade-off, limitation or open question. Otherwise delete the section.>
```

## Rules

- At most about 220 words in total. The hook rejects longer reports.
- Plain language. No marketing words, no restating the plan, no list of every file (the evidence section has that).
- Never claim tests pass or checks are clean. The hook measures that and records the real numbers.
- Pick a Concept the user is likely to meet again. Explain it so it sticks: what it is, why it works, where it lives in this code.
- Show the report to the user in your final message (the full draft is fine, it is short).

## If the Stop hook blocks you

It lists what is wrong (new test failures, new lint or type errors, a missing review, or report problems). Fix those, update the draft if the change moved, then finish again.
