---
name: codebase-navigation
description: Find code without reading whole files, using the cock-cage code map, the LSP tool, Grep and targeted reads. Use when locating a function, class, caller or test, when deciding where a change belongs, or when a large-file read is blocked.
---

# Navigating a codebase cheaply

Every line you read stays in context for the rest of the session and is re-sent on every turn. Find first, then read only what you need.

## Order of tools

1. **Code map.** `.cock-cage/codemap.md` lists every module with its classes, functions, signatures, first docstring line and line numbers (`L42`). It is refreshed at session start and after each edit. Start here to decide which file and which lines matter.
2. **LSP tool** (if the pyright-lsp plugin is installed). Use go-to-definition, find-references and hover to answer "where is X defined" and "who calls X" from the language server's index without opening files.
3. **Grep with line numbers.** `Grep` with `-n` for names the map does not cover: string literals, config keys, SQL, log messages.
4. **Targeted Read.** `Read` with `offset` and `limit` around the line you found. Files over the configured size (400 lines by default) cannot be read whole; the hook blocks it.
5. **Explorer subagent.** For questions that span many files ("how does auth flow through the app?"), delegate to `cock-cage:explorer`. It reads in its own context and returns a short answer with file:line references, so the exploration never lands in yours.

## Before writing new code

Search for something that already does the job: look in the code map for similar names, grep for key verbs (`parse`, `load`, `format`, `retry`, `validate`), and check utility modules. Reusing an existing helper beats writing a near-copy. cock-cage's duplicate detector will flag copies at the end of the task.
