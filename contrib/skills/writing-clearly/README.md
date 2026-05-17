# writing-clearly

A DecafClaw contrib skill that edits prose drafts using William Strunk Jr.'s *The Elements of Style* (1918).

The skill exposes one tool, `edit_with_strunk(draft, focus="")`, which delegates planning to a clean-context child agent and then applies the resulting plan deterministically in tool code. The corpus (~12k tokens) is read server-side and never enters the parent conversation.

## How it works

1. Parent calls `edit_with_strunk` with a draft inline.
2. The tool reads `elements-of-style.md` from this directory.
3. It builds a planner prompt — persona + rules + draft + focus — and passes it to `tool_delegate_task` with a `return_schema` describing the edit-plan shape.
4. A child agent (clean context, the parent's active model unless `WRITING_CLEARLY_MODEL` is set) produces a structured plan: a list of `{kind, rule, before, after, note}` entries. The child does NOT produce the revision.
5. Tool code applies each entry to the draft via deterministic string replace (first occurrence of `before` → `after`). Both substitution and rewrite kinds use the same apply path; the planner commits to the full rewritten text upfront for structural rules.
6. The tool returns `ToolResult(text=revised_prose, data={summary, applied, skipped})`. Every visible change in the revision corresponds to one entry in `applied`. Entries whose `before` couldn't be located are recorded in `skipped` with a reason. Progress events fire per applied/skipped entry so the UI can show real-time edit operations.

If the planner returns malformed JSON, the tool falls back to returning the planner's raw output as text — degrading gracefully to the simpler v1 behavior.

## When to use

Any prose a human will read: documentation, commit messages, PR descriptions, replies, blog posts, error messages. Strunk's rules are general-purpose — they tighten anything from a sentence to an essay.

## Installation

See [../README.md](../README.md) for the contrib-skill installation pattern (either `extra_skill_paths` referencing this directory, or a copy under `data/{agent_id}/skills/`).

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `WRITING_CLEARLY_MODEL` | (inherit) | Pin the child agent to a specific model. Leave unset to inherit the parent's active model. |

## Credits

This skill is adapted from [obra/the-elements-of-style](https://github.com/obra/the-elements-of-style) — specifically the [`writing-clearly-and-concisely`](https://github.com/obra/the-elements-of-style/tree/main/skills/writing-clearly-and-concisely) skill by Jesse Vincent (obra). The core idea — "dispatch a subagent with the draft and the rulebook" — and the bundled `elements-of-style.md` corpus both come from that project. Thanks!

The SKILL.md and `tools.py` are DecafClaw-specific adaptations (DecafClaw's delegation infrastructure, skill loader, and tool-definition format), but the editorial approach is theirs.

William Strunk Jr.'s *The Elements of Style* (1918) is in the public domain.
