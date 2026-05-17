---
name: writing-clearly
description: Edit prose for clarity and concision using Strunk's *The Elements of Style* (1918). Use whenever you have a draft — documentation, commit messages, blog posts, replies — that should be tightened before it goes out.
allowed-tools: edit_with_strunk
---

# Writing Clearly and Concisely

Edits prose drafts using Strunk's *Elements of Style*. A delegated child agent produces a structured edit plan; tool code applies the plan to the draft deterministically. The rulebook never enters this conversation, and the plan is auditable against the revision — every visible change corresponds to a recorded plan entry.

## When to use

Any prose a human will read and you want it tighter:

- Documentation, READMEs, technical explanations
- Commit messages, PR descriptions
- Replies in chat threads or email drafts
- Anywhere the draft is "OK but verbose / wordy / passive"

## How to use

Call `edit_with_strunk` with the draft inline:

```
edit_with_strunk(
  draft="<the actual prose to edit — paragraphs, sentences>",
  focus="optional: 'omit needless words' or 'active voice' — leave blank for general"
)
```

The tool returns:

- **`ToolResult.text`** — the revised prose, ready to paste back to the user.
- **`ToolResult.data`** — a structured record of what changed:
  - `summary`: one-line description of the editing pass.
  - `applied`: list of plan entries that were applied. Each entry has `kind` (substitution or rewrite), `rule` (Strunk rule name), `before`, `after`, `note`.
  - `skipped`: list of plan entries that were dropped, each with a `_skip_reason` (`before_not_found`, `before_empty`, or `noop`).

The `data` payload lets you summarize what was changed and why — useful for showing the user not just the revision but the rationale.

Use `focus` when you want the editor to bias toward one rule cluster (e.g. tighten verbs only, or strip passive voice only). Leave blank for a full pass.

## How edits are applied

The child agent produces a plan only — a list of `{kind, rule, before, after, note}` entries. Tool code then applies each entry by finding the `before` text in the draft and replacing the first occurrence with `after`. No second LLM pass; the revision is mechanically derived from the plan.

This means:

- The plan is ground truth. Every change in the revision corresponds to a recorded entry.
- If the planner's `before` field doesn't exactly match text in the draft (whitespace drift, markdown corruption), that entry is skipped and recorded in `data.skipped`. The rest of the plan still applies.
- Edits apply in plan order. A later entry can target text produced by an earlier entry.
- If the planner returns malformed JSON, the tool falls back to returning the planner's raw output as text — degrading to the simpler v1 behavior.

## What to pass as `draft`

**CRITICAL — get this right or the tool will fail.** The `draft` argument is the prose the user wants edited. Examples of what `draft` SHOULD contain:

- A blog post you just fetched with `tabstack_extract_markdown` — pass the article body verbatim.
- A commit message or PR description the user drafted — pass it verbatim.
- A reply or email body — pass the user's text verbatim.

Examples of what `draft` MUST NOT contain:

- A request to edit something ("please edit my blog post about X")
- The Strunk rules themselves
- This skill's instructions or any part of `SKILL.md`
- The tool's parameter descriptions
- An explanation of what you want edited

If the user says *"edit this blog post: <URL>"*, the workflow is: fetch the content first (e.g. with `tabstack_extract_markdown`), then pass the **fetched content** as `draft` — not the URL, not the user's request, not a summary.

If you have nothing to edit, do not call the tool — ask the user for the text first.

## What it preserves

- Technical terms, names, code, links, and quoted material — verbatim.
- The author's voice and intent. The editor tightens; it does not rewrite for style or tone.
- Sentences already clean by Strunk's standards are left alone.
