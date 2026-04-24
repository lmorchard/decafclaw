# System prompt structural delimiters

Tracking issue: #304

## Problem

`load_system_prompt` in `src/decafclaw/prompts/__init__.py` concatenates
five distinct sources with `\n\n` separators — SOUL.md, AGENT.md, optional
workspace USER.md, the skill catalog, and the bodies of always-loaded
skills. A separate `build_deferred_list_text` in
`src/decafclaw/tools/tool_registry.py` produces the deferred-tool catalog
that gets injected as a second system message. In both cases the output
is a long blob where sections bleed together — legible to neither humans
reviewing the assembled prompt nor the model trying to navigate it.

Anthropic's [Effective Context Engineering for AI Agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
specifically recommends wrapping prompt sections in explicit delimiters so
the model can distinguish identity from instructions from reference
material. This is the warm-up intervention before the eval harness (#303)
lands; the next round of prompt-structure work can then be measured.

## Goal

Wrap each assembled section in an explicit XML tag. Source files stay
plain markdown — wrapping happens at assembly time. Scope is tight:
main system prompt + deferred-tool catalog only.

## Section structure

### Main system prompt (`messages[0]`)

```
<soul>
...SOUL.md contents...
</soul>

<agent_role>
...AGENT.md contents...
</agent_role>

<user_context>        (only when data/{agent_id}/USER.md exists)
...USER.md contents...
</user_context>

<skill_catalog>
...build_catalog_text output (## Active Skills + ## Available Skills
   sub-lists, name+description each)...
</skill_catalog>

<loaded_skills>       (only when at least one always-loaded skill body
<skill name="vault">  is included — one nested <skill> per body, bundled
...body...            only, following the existing trust-boundary check)
</skill>
<skill name="background">
...body...
</skill>
</loaded_skills>
```

Four judgment calls documented in the brainstorm and locked in:

- **`<soul>` not `<identity>`.** Matches the source filename; shifting the
  tag while keeping SOUL.md creates a name mismatch without benefit.
- **`<skill_catalog>` and `<loaded_skills>`, not `<available_skills>` /
  `<active_skills>`.** The output of `build_catalog_text` already
  contains `## Active Skills` and `## Available Skills` as internal
  markdown sub-headings (one listing always-loaded skills,
  the other listing on-demand skills). Wrapping with the same words
  would create two-levels-of-same-label confusion — `<active_skills>`
  the XML tag (bodies) vs `## Active Skills` the markdown heading
  (listing). The renamed tags sidestep the collision entirely.
- **Nested `<skill name="...">` inside `<loaded_skills>`, not pooled.**
  Same "structure is visible to the model" impulse applied one level
  deeper. Per-skill boundary is useful since the bodies are
  independently authored.
- **Empty sections emit nothing.** No `<user_context></user_context>`
  when USER.md isn't present; no `<skill_catalog>` when there are
  zero skills; no `<loaded_skills>` when nothing is always-loaded.
  Empty wrappers are noise.

### Deferred-tool catalog (`messages[1]`, second system message)

```
<deferred_tools>
## Available tools (use tool_search to load)

### Core
- web_fetch — ...
...

### Skills
- ...

### Tools from MCP server `github`
- ...
</deferred_tools>
```

Wrap inside `build_deferred_list_text` itself so both callers (the
initial ContextComposer compose + the per-iteration refresh in
`agent.py::_build_tool_list`) get consistent treatment. The empty case
still returns `""` — no wrapper around empty content.

## Out of scope

Deferred to **#357** (separate follow-up issue, P2 backlog):

- `REFLECTION.md` / `MEMORY_SWEEP.md` / the compaction prompt in
  `compaction.py`. These are single cohesive instruction blocks, not
  multi-source concatenations — the specific problem #304 names doesn't
  apply. Their dynamic-input wrapping (conversation being judged,
  messages being compacted) is a related but distinct concern.
- Behavioral A/B measurement. Depends on the #303 eval harness, which
  is explicitly the next ticket in the cluster.

## Acceptance criteria

- `load_system_prompt` output always has `<soul>`, `<agent_role>`
  present (in that order) for the default bundled prompts.
- `<user_context>` appears iff `data/{agent_id}/USER.md` exists and is
  non-empty.
- `<skill_catalog>` appears iff at least one skill is discovered
  (i.e. `build_catalog_text` returns non-empty).
- `<loaded_skills>` appears iff at least one bundled always-loaded
  skill was discovered; its body contains one `<skill name="…">` block
  per always-loaded skill.
- `build_deferred_list_text` output is wrapped in `<deferred_tools>`
  when non-empty; returns `""` (not `<deferred_tools></deferred_tools>`)
  when there's nothing to list.
- Docs updated (`docs/context-composer.md` or a new
  `docs/system-prompt.md`) to describe the convention so future prompt
  contributors don't undo it.

## Testing

- **Structural unit test** (new file or addition to existing prompts
  test): assemble the prompt against a known configuration; assert the
  tag sequence, presence/absence conditions, and nested-skill shape.
- **`build_deferred_list_text` unit test**: assert wrapper present on
  non-empty output, absent on empty.
- **No automated behavioral verification.** The #303 eval harness is
  the correct surface for that; a manual smoke test post-merge suffices
  for this commit.

## Files touched

- `src/decafclaw/prompts/__init__.py` — assembly logic.
- `src/decafclaw/tools/tool_registry.py` — `build_deferred_list_text`
  wrap.
- `docs/context-composer.md` (+ possibly a new
  `docs/system-prompt.md`) — convention documentation.
- `CLAUDE.md` "System prompt from files" bullet — mention the wrapping
  convention.
- Tests: `tests/test_prompts.py` (if it exists) or new, plus
  `tests/test_tool_registry.py` for the deferred-list wrap.
