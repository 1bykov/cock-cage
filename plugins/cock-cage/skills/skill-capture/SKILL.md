---
name: skill-capture
description: Draft a new reusable skill, or an improvement to an existing cock-cage skill, from work just completed. Use when the user says "save this as a skill", "remember how to do this", "capture this workflow", or runs /cock-cage:capture-skill.
---

# Capturing a skill

Skills are how the cock-cage library grows. You only draft. The user reviews and promotes the draft into the library from their own terminal, which keeps the library curated.

## Decide what is worth capturing

A skill earns its place when it records something that is not obvious and will recur: a house style, a workflow with several steps, a template, hard-won gotchas. One-off facts about this project belong in the project's CLAUDE.md instead. Say so if that is the better home.

Check `cockcage skill list` (or the list of installed skills in your context) first. If a related skill exists, draft an improved version of it under the same name instead of a new one.

## Write the draft

Create `.cock-cage/skill-drafts/<kebab-case-name>/SKILL.md`:

```markdown
---
name: <kebab-case-name>
description: <one sentence: what it does AND when to use it, with the trigger words a user would actually say. Under 250 characters. This line is all Claude sees until the skill is used, so it decides whether the skill ever fires.>
---

# <Title>

<Instructions in imperative voice. Lead with the decisions that matter, then the steps. Include a short example or template if the output has a shape. Keep it under about 400 lines; move long reference material to a separate file in the same folder and link to it.>
```

Add supporting files (templates, example code, reference notes) in the same folder if they help. Do not include secrets or anything specific to one client.

## Hand over

Tell the user the draft is ready and that they can review and promote it with:

```
cockcage skill promote <name>
```

That command shows the diff, asks for confirmation, copies the skill into their cock-cage repo and commits it. You cannot run it yourself.
