# skills: true skill-level progressive disclosure

Closes #547.

## Problem

The agent consistently tries to call skill-provided tools before activating their skills. Every conversation burns extra turns on the dance: try tool → "unknown tool" error → activate → retry.

Root cause: the system prompt currently shows skill tool **names and descriptions** in the deferred-tool list under "## Available tools (use tool_search to load)". A small model like Gemini Flash sees `tabstack_extract_markdown` advertised there and reasonably concludes "I should call this." The unknown-tool error is the agent hitting a gate that the system prompt itself led it to walk into.

The fix is to remove the contradiction: skill tools should not be visible to the agent until the skill is activated. The **skill** is the unit of progressive disclosure — its tools are an implementation detail revealed by activation.

## Goal

Restructure the agent's view so:

- The skill catalog (name + description per skill) is the only skill-related thing visible at conversation start.
- Skill tools become discoverable only after `activate_skill` loads the skill into context.
- `tool_search` and the unknown-tool error help the agent find the right skill to activate when it knows what capability it wants but not where it lives.

## Architecture

### `always_loaded` opens up — two paths

Today the `always_loaded: true` frontmatter flag only takes effect for bundled skills — both `build_catalog_text` and the auto-activation loop in `agent.py` gate on bundled placement. That restriction made sense when bundled was the only tier the project author controlled.

In the new trust-tier model, `always_loaded` becomes available via two paths:

1. **In the skill's frontmatter** (`always_loaded: true`) — works for any trusted tier (bundled / admin / extra). The skill author or someone editing a local copy declares the intent.
2. **In the agent's config** (`config.skills_always_loaded: list[str]`) — a list of skill names the agent should treat as always-loaded regardless of their frontmatter. Lets a user opt a contrib skill into always-loaded without editing its source (which `git pull` would clobber for `extra_skill_paths` installations).

Example config entry:
```json
{
  "skills_always_loaded": ["writing-clearly", "tabstack"]
}
```

A skill is always-loaded if EITHER path applies AND the skill is in a trusted tier. Workspace skills are denied via either path — they can't force themselves into the system prompt for the same reason they can't self-approve.

This is the escape hatch for "I use this skill so often the 4-call dance is annoying": opt it into always-loaded in whichever path fits the deployment.

### Trust tiers — derived from placement

Each `SkillInfo` gets a `trust_tier: Literal["bundled", "admin", "extra", "workspace"]` field set at discovery time from the source path:

| Tier | Location | Confirmation on `activate_skill` |
|---|---|---|
| `bundled` | `src/decafclaw/skills/` | Skipped — trusted by project |
| `admin` | `data/{agent_id}/skills/` | Skipped — trusted by placement |
| `extra` | `extra_skill_paths` entries | Skipped — trusted by config |
| `workspace` | `data/{agent_id}/workspace/skills/` | Required — agent could have authored this |

The `tool_activate_skill` precedence chain gains one rung:

```
"deny"  >  trusted tier (bundled/admin/extra)  >  "always" in perms  >  auto-approve flag  >  interactive confirmation
```

For trusted-tier skills, `activate_skill` proceeds immediately. For workspace skills, today's confirmation flow runs unchanged.

### Skill tools hidden until activation

Skill tools are removed from the **deferred pool** entirely. The system prompt's deferred-tool list no longer contains an "### Skills" section. Skill tools enter the agent's visible tool surface only after `activate_skill` mutates `ctx.tools.extra` / `ctx.tools.extra_definitions`.

What the agent sees about an unactivated skill is exactly what the catalog shows:

```
### Available Skills

The following skills can be activated. Each skill's tools become available
after you call activate_skill(name).

- tabstack: Web execution API for reading, extracting, and transforming web pages and PDFs.
- writing-clearly: Edit prose for clarity and concision using Strunk's *The Elements of Style*.
```

The catalog text shifts from "you MUST activate before using tools" (today's slightly accusatory tone, which assumed the agent already knew the tool names from the deferred list) to a plain statement of how to discover the tools. The agent has no other path to those tool names — calling activate_skill is the only way forward.

Always-loaded bundled skills (`vault`, `background`, `mcp`) stay in their existing "Active Skills" section with their tools fully visible — they're already activated.

### `tool_search` — searches the catalog, returns skills

Today `tool_search` searches the full deferred pool, including skill-tool names and descriptions, and lets the agent fetch individual tools. After this change:

- `tool_search` searches the skill catalog (name + description) AND the **hidden** skill-tool inventory (name + description per skill tool), but **returns skill names**, not individual tool names. A match on a hidden tool name surfaces its owning skill.
- The agent's only action after a `tool_search` result is `activate_skill(name)`. There's no auto-fetch for skill tools (auto-fetch stays for any genuinely deferred non-skill tools, if those exist).

This preserves discoverability — an agent that "knows what it wants" by recalled tool name can still find the skill — without re-exposing tool surfaces the spec is trying to hide.

### Preempt-skill hint — keyword-driven activation nudge

Today's `_compose_preempt_skill_matches` already emits a hint when user-message keywords overlap with a skill's name + description. It stays. Sharpen the wording so small models actually act on it:

```
<preempt_skill_hint>
The following skills look relevant to the current message. Call activate_skill(name) to load their tools.

- tabstack
- writing-clearly
</preempt_skill_hint>
```

No tool enumeration (we just hid those). The hint is a routing signal: "consider activating these skills."

### Unknown-tool error — suggests owning skill

If the agent attempts to call a tool name it shouldn't know — recalled from training data, a previous conversation, or guessed — the unknown-tool error should help it recover. Use a precomputed `config.skill_tool_owners: dict[str, str]` map (tool name → owning skill name) built at skill discovery:

| Failed tool name | Error |
|---|---|
| Belongs to a discovered skill, not activated | `[error: 'edit_with_strunk' is provided by the 'writing-clearly' skill, which is not activated. Call activate_skill('writing-clearly') first.]` |
| Belongs to a workspace skill, denied | `[error: 'foo_tool' is provided by the workspace skill 'foo', which has been denied. Tool unavailable.]` |
| Not in any skill | Existing close-match suggestion + `tool_search` hint. |

This is the safety net for hidden-tool guesses. The same `skill_tool_owners` map serves both this error and `tool_search`'s hidden-tool-name lookup.

## What stays the same

- Progressive disclosure: only the catalog is visible at conv start. (Now stricter — also no per-tool entries for unactivated skills.)
- `activate_skill` as the loading mechanism.
- Skill body + tool list delivered as the `activate_skill` tool result, persisted in conversation history.
- Always-loaded bundled skills (`vault`, `background`, `mcp`).
- `skill_permissions.json` format and location.
- Workspace-skill confirmation flow.
- `ctx.skills.activated` lifecycle and persistence.
- MCP tool dispatch (MCP tools are not skill tools; they keep their existing visibility).
- The diagnostics sidecar.

## What changes

- `SkillInfo` gains `trust_tier`, set at discovery.
- `tool_activate_skill` precedence chain gains the trusted-tier rung — bundled / admin / extra skills skip confirmation.
- Skill tools removed from the deferred pool — never enter the system prompt's "Available tools" list. Tool classification (`tool_registry.py`) needs to treat skill tools as `skill-locked` rather than `deferred`.
- `tool_search` searches the skill catalog + hidden skill-tool inventory; returns skill names.
- `config.skill_tool_owners: dict[str, str]` precomputed at discovery; used by `tool_search` and the unknown-tool error.
- Skill catalog text updated to reflect the new model (no implicit accusation; just "call activate_skill to load tools").
- Preempt-skill hint sharpened — still names matched skills, omits the tool list.
- Unknown-tool error names the owning skill when the failed name belongs to a discovered skill.
- `always_loaded` eligibility extended from bundled-only to all trusted tiers (bundled / admin / extra). Workspace skills remain ineligible. Two specific gates relax: the filter in `build_catalog_text` and the auto-activation loop in `agent.py`.
- New top-level `config.skills_always_loaded: list[str]` setting. Skills listed here are treated as always-loaded if they're in a trusted tier. Composes with the frontmatter flag — either path triggers always-loading.

## What goes away

- The activation dance for any skill: `unknown tool` failures preceded by visible-but-uncallable tool names. The agent literally cannot reference an unactivated skill's tools.
- Confirmation prompts on first activation of trusted-tier skills.
- The "### Skills" section in the deferred-tool list. Skill tools never appear there.

## Configuration

No new knobs. The change is uniformly correct — there's no use case for showing the agent tool names it can't call.

## Failure modes and edge cases

| Scenario | Behavior |
|---|---|
| Agent reads the catalog, decides to use a skill, calls `activate_skill` | Trusted tier: skill loads immediately; tools become visible. Workspace tier: confirmation runs. |
| Agent recalls a skill tool name from prior context / training, tries to call it directly | Unknown-tool error names the owning skill and tells the agent to call `activate_skill(name)`. One extra turn instead of two (no "did you mean" round-trip, just a direct pointer). |
| Agent uses `tool_search` with a capability keyword | Matches against catalog descriptions AND hidden tool descriptions. Returns matching skill names. Agent calls `activate_skill`. |
| Agent calls `activate_skill('foo')` for an unactivated trusted-tier skill | Loads immediately, no confirmation. Body + tool list as tool result. |
| Agent calls `activate_skill('foo')` for an unapproved workspace skill | Confirmation flow runs as today. |
| Skill body references a tool that the skill no longer has | Catalog entry is still useful; activation reveals the actual current tool set. |
| Existing `skill_permissions.json` records for trusted-tier skills | Honored at read time; writes for trusted tiers stop. Records become harmless. |
| `"deny"` record for a trusted-tier skill | Still wins. Skill cannot activate. |
| Always-loaded bundled skill | Unchanged. Tools visible in active set. |

## Acceptance criteria

- [ ] `SkillInfo.trust_tier` populated at discovery from the source path.
- [ ] Skill tools no longer appear in the system prompt's deferred-tool list ("### Skills" section gone).
- [ ] `tool_activate_skill` skips confirmation for trusted-tier skills.
- [ ] Workspace-skill confirmation flow unchanged.
- [ ] `config.skill_tool_owners` precomputed at discovery.
- [ ] `tool_search` matches against catalog + hidden tool inventory; returns skill names.
- [ ] Unknown-tool error names the owning skill for hidden-skill-tool guesses.
- [ ] Preempt-skill hint sharpened; no tool enumeration.
- [ ] Catalog text updated to remove "you MUST activate" accusatory tone.
- [ ] `always_loaded: true` (frontmatter) honored for trusted-tier skills, denied for workspace skills.
- [ ] `config.skills_always_loaded` (config list) honored for trusted-tier skills, denied for workspace skills. Composes with the frontmatter path.
- [ ] Manual verification with Flash as the parent: editing flow ("read this blog post and edit it") completes in 4 tool calls — `activate_skill('tabstack')` → `tabstack_extract_markdown` → `activate_skill('writing-clearly')` → `edit_with_strunk`. Each activation succeeds silently (no user prompt). No "unknown tool" errors in the happy path.
- [ ] With both skills in `config.skills_always_loaded` (or frontmatter-declared), the same flow completes in 2 tool calls — `tabstack_extract_markdown` → `edit_with_strunk`. No activations needed.
- [ ] Tests cover: skill tools absent from deferred list, `tool_search` returning skills not individual tools, trusted-tier activation skipping confirmation, workspace-tier still confirming, unknown-tool error naming the owning skill, deny record still wins, `always_loaded` working for non-bundled trusted tiers via both paths, `always_loaded` rejected for workspace tier via both paths.

## Out of scope

- Auto-activation on tool call. Discarded after considering it — exposes the skill body to a tool call that's already happening, which defeats progressive disclosure (the agent's call didn't read the body first).
- Eagerly loading all trusted skills at conv start. Discarded — preserves progressive disclosure.
- Deprecating the `auto-approve` frontmatter flag. Becomes shadowed for trusted tiers; cleanup is a separate PR.
- Per-skill `trust_tier` override in frontmatter. No real case yet.

## Risks

1. **4 tool calls vs the idealized 2.** The unavoidable cost of progressive disclosure: the agent must read the skill body before using its tools. The spec accepts this because the body genuinely informs correct tool use (multi-step workflows, required args, gotchas). For skills the user uses constantly, the escape hatch is `always_loaded: true` in the SKILL.md frontmatter — opt the skill into the system prompt and skip both activation turns. Available for any trusted-tier skill under the relaxed rule above.
2. **Existing `auto-approve: false` on bundled skills (if any) silently bypassed by the tier check.** Grep before merging.
3. **Path-to-tier mapping edge cases.** Symlinks, `..` traversal, `extra_skill_paths` that point inside `data/{agent_id}/workspace/skills/`. The plan needs to pin down which path the tier check inspects (the discovered-source path, not a resolved/symlinked path) so the user's stated configuration controls the tier.
4. **`tool_search` behavior change is observable.** Today it can fetch individual tools; after this it returns skills. Any test or eval that asserted "tool_search loads tool X" will break. Need to grep for callers.
5. **Hidden tool-name awareness in agent training.** A model trained on DecafClaw transcripts (or that's seen similar tool names before) might still try to call a hidden tool. The improved unknown-tool error catches this and routes to `activate_skill` — but it's worth manually testing with a "name a tool" prompt to see what happens.
