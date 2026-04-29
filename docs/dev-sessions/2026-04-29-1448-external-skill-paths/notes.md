# External Skill Paths — Retro

## What shipped

A top-level `Config.extra_skill_paths: list[str]` with `EXTRA_SKILL_PATHS` env override, resolved via `~`/`$VAR` expansion + `agent_path` anchoring for relatives, appended to `discover_skills`' `scan_paths` after bundled. Externals never shadow bundled skills. Empty env vars now fall through to `config.json` (matched the module's "first non-empty wins" docstring after Copilot caught the gap).

13 new tests across `tests/test_skills.py` and `tests/test_config.py`. New `docs/skills.md` subsection ("Installing skills via `npx skills`"), updated scan-order table, extended `CLAUDE.md` skills bullet.

PR #427 — squashed feature commit + separate session-docs commit.

## Scope drift

- **`always_loaded` discovery-time stripping** was added for ALL non-bundled skills (workspace + agent + external), not just externals. The spec claimed "trust boundary unchanged — falls out of the existing `is_relative_to(_BUNDLED_SKILLS_DIR)` check," but only `auto_approve` was actually stripped at discovery; `always_loaded` was filtered only in the catalog text builder. Copilot's first review comment caught it: a non-bundled skill with `always-loaded: true` was still getting its tools registered as critical at `activate_skill_internal`. Mirrored the existing `auto_approve` pattern; bundled skills (`vault`, `background`, `mcp`) are exempt and verified to retain the flag.

- **Manual verification via `make config`** turned out to be impossible without an unrelated change. `decafclaw config show` only iterates nested-dataclass groups, so top-level scalar/list fields (`default_model`, `providers`, `model_configs`, `extra_skill_paths`) silently don't appear. Documented as a known pre-existing gap in the PR body; substituted a `python -c "load_config().extra_skill_paths"` check.

## Surprises

- **Pre-existing inconsistency in `discover_skills`**: `auto_approve` stripped at discovery (line 215-223 pre-change), `always_loaded` only filtered later in `build_catalog_text`. Two enforcement sites for what should be one trust boundary. Spec/research didn't surface this because the documentarian's question framing (well-meaning) traced the auto_approve path as the canonical example without asking "what other flags share this trust posture, and is enforcement uniform?"

- **`vercel-labs/skills`** already includes an `openclaw` agent target in its hardcoded registry (with `~/.openclaw/skills` global and fallback to `~/.clawdbot`/`~/.moltbot`). Not relevant to this session — but a curious neighbor in the namespace.

- **No `--path` flag** in the `npx skills` CLI. The agent registry is fully hardcoded; the only env-based redirection is per-known-agent (`CLAUDE_CONFIG_DIR` for `claude-code`, etc.). Confirmed there's no pure-config integration path without modifying decafclaw.

## Workflow friction

- **Documentarian research substep was very valuable.** The 5 neutral questions produced precise `file:line` refs that grounded both the spec and plan. Saved redundant lookups during execute.

- **TDD signal partially diluted.** 4 of 7 Phase 1 tests genuinely failed before implementation; 3 "passed" because they were regression nets (negative checks that succeed when externals are simply ignored). The 4 real failures were enough to validate the test setup; the 3 passers became valuable on their own merits as protection against future regressions. Net: TDD discipline still produced first-run-pass on the implementation.

- **Plan self-review caught 3 spec gaps** before execute: missing `$VAR` test, missing "workspace+agent shadow external" test, env-var tests didn't match `tests/test_config.py`'s actual class/fixture pattern. Self-review wasn't theatre.

- **Express mode worked well.** Brainstorm + plan stayed interactive (good — Les's intent capture); execute + branch self-review + PR + Copilot cycle ran autonomously. Copilot's two comments were both real bugs and both fixable in <30 minutes. Force-push with `--force-with-lease` per Les's standing rule.

## Misses

- **Should have asked one more research question:** "Where else does the bundled-only trust boundary get enforced beyond `discover_skills`?" That would have surfaced `activate_skill_internal`'s `always_loaded_skill_tools` caching, and the spec/test would have stripped `always_loaded` from the start instead of needing a Copilot comment to catch it.

- **Should have spot-checked `make config`** during planning. The manual verification item rested on an untested assumption.

## Memory candidates

- **`decafclaw config show` limitation:** only prints nested-dataclass groups. Top-level scalar/list fields on `Config` silently skip. Relevant whenever adding a top-level config field — you can't smoke-test the value via `make config` without first patching `cmd_show`. Save as a project/reference memory.

- **Trust boundary canonical pattern:** `is_relative_to(_BUNDLED_SKILLS_DIR)` is the bundled-only check used by `auto_approve` and now `always_loaded`. Any future flag that should be bundled-only follows the same pattern in `discover_skills` (strip + warn for non-bundled). Save as a project memory — relevant whenever adding new skill frontmatter flags with trust implications.

- **`vercel-labs/skills` agent install paths:** for `claude-code` it's `~/.claude/skills/` global and `.claude/skills` project-local, controlled by `CLAUDE_CONFIG_DIR` env if set. Already documented in `docs/skills.md` now, so doesn't need a separate memory — but worth a reference if anyone asks again.

## Skill candidates

- **Documentarian prompt could nudge toward "enforcement-site enumeration."** Today the framing is good for tracing flow; less good for "find ALL places where X is enforced." Adding a question template like "for each behavior: list every enforcement site, not just the most obvious one" might catch latent inconsistencies before code review does. But this might be too narrow to formalize — the existing prompt's negation rules already discourage solution-shaped questions, which is the deeper safeguard.

- **Dev-session retro pattern: capture latent codebase inconsistencies surfaced during a session** as a separate output channel (not just memory). When Copilot/branch self-review surfaces "this thing was already wrong," that's worth a follow-up issue, not just a fix-in-place. This session didn't open one because the fix was tightly coupled to the new feature, but in general it's worth a reflex.
