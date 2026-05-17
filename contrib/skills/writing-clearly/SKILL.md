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
  draft="<the prose you wrote>",
  focus="optional: 'omit needless words' or 'active voice' — leave blank for general"
)
```

The tool returns the revised prose. Hand it back to the user, or use it as the next iteration of the draft.

Use `focus` when you want the editor to bias toward one rule cluster (e.g. tighten verbs only, or strip passive voice only). Leave blank for a full pass.

## What it preserves

- Technical terms, names, code, links, and quoted material — verbatim.
- The author's voice and intent. The editor tightens; it does not rewrite for style or tone.
- Sentences already clean by Strunk's standards are left alone.
