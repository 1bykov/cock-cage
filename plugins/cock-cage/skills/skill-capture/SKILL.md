---
name: skill-capture
description: Turn a workflow from this session into a reusable cock-cage skill (or improve an existing one), then promote and publish it to every project. Use when the user says "save this as a skill", "remember how to do this", "capture this workflow", runs /cock-cage:capture-skill, or when you notice a procedure worth reusing and the user agrees.
---

# Capturing a skill

Skills are how the cock-cage library grows. You draft, promote and publish them yourself. The user only needs to know what was added.

## Decide what is worth capturing

A skill earns its place when it records something that is not obvious and will recur: a house style, a multi-step workflow, a template, hard-won gotchas. Facts about one project belong in that project's CLAUDE.md instead. Say so if that is the better home.

Run `cockcage skill list` first. If a related skill exists, improve it under the same name instead of creating a near-duplicate.

## Write the draft

Create `.cock-cage/skill-drafts/<kebab-case-name>/SKILL.md`:

```markdown
---
name: <kebab-case-name>
description: <one sentence: what it does AND when to use it, with the words a user would actually say. Under 250 characters. This line is all Claude sees until the skill fires, so it decides whether it ever does.>
---

# <Title>

<Imperative instructions. Lead with the decisions that matter, then the steps. Include a short template or example if the output has a shape. Under about 400 lines; put long reference material in a separate file in the same folder and link to it.>
```

Add templates or reference files in the same folder if they help. Never include secrets or anything specific to one client.

## Promote and publish

1. Re-read the draft once as a stranger would: is the description specific enough to trigger, and are the steps followable without this conversation?
2. `cockcage skill promote <name> --yes` copies it into the cock-cage source clone and commits it.
3. `cockcage publish patch` runs the cock-cage tests, bumps the version, tags, pushes, and refreshes this machine. Other projects get it automatically at their next session.
4. Tell the user in one or two lines what the skill does and that it is live.

If publishing fails (tests, network, credentials), say what failed and leave the promoted commit in place for the next attempt.
