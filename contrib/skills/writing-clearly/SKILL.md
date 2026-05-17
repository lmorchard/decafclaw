---
name: writing-clearly
description: Edit prose for clarity and concision using Strunk's *The Elements of Style* (1918). Use whenever you have a draft — documentation, commit messages, blog posts, replies — that should be tightened before it goes out.
allowed-tools: edit_with_strunk
---

# Writing Clearly and Concisely

Edits prose drafts using Strunk's *Elements of Style*. The rulebook is inlined into a delegated child agent, so it never enters this conversation.

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

The tool returns the revised prose. Hand it back to the user, or use it as the next iteration of the draft.

Use `focus` when you want the editor to bias toward one rule cluster (e.g. tighten verbs only, or strip passive voice only). Leave blank for a full pass.

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
