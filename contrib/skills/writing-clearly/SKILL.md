---
name: writing-clearly
description: Edit prose for clarity and concision using Strunk's *The Elements of Style* (1918). Use whenever you have a draft — documentation, commit messages, blog posts, replies — that should be tightened before it goes out.
allowed-tools: edit_with_strunk
---

# Writing Clearly and Concisely

Adapts the [writing-clearly-and-concisely](https://github.com/obra/the-elements-of-style) skill for DecafClaw. The Strunk corpus (`elements-of-style.md`, ~12k tokens) never enters this conversation — it's loaded server-side and inlined into a delegated child agent that does the edit in a clean context.

## When to use

Whenever you have prose a human will read and want it tighter:

- Documentation, READMEs, technical explanations
- Commit messages, PR descriptions
- Replies in chat threads or email drafts
- Anywhere the draft is "OK but verbose / wordy / passive"

## How to use

Call `edit_with_strunk` with the draft inline:

```
edit_with_strunk(
  draft="<the prose you wrote>",
  focus="optional: 'omit needless words' or 'active voice' or leave blank for general"
)
```

The tool returns the revised prose. Hand it back to the user, or use it as the next iteration of your draft.

## What happens under the hood

1. The tool reads `elements-of-style.md` from disk (your context never sees it).
2. Builds a task: persona + rules + draft + focus.
3. Calls `delegate_task` — a child agent with clean context applies the rules and returns the revision.
4. The revision lands as the tool's output.
