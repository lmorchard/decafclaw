# External Skill Paths Spec

**Goal:** Let decafclaw users discover skills installed by external tooling (notably `npx skills add …` from `vercel-labs/skills`) by adding a configurable list of extra directories to scan, without requiring an upstream PR or manual symlinks per install.

**Source:** User request 2026-04-29 — "Check out this NPM command, can we support it for installing skills in decafclaw?"

## Current state

Skill discovery is hardcoded in `src/decafclaw/skills/__init__.py:186-189` to three paths:

1. `config.workspace_path / "skills"` (highest priority)
2. `config.agent_path / "skills"`
3. `_BUNDLED_SKILLS_DIR` (lowest)

First-name-wins shadowing (`research.md:36-42`). No existing `list[Path]` config field anywhere in the codebase (`research.md:96-122`); the closest analog is `vault_path` (single string, anchored to `agent_path`, no `~` expansion).

Trust boundary is post-hoc: `is_relative_to(_BUNDLED_SKILLS_DIR)` strips `auto-approve` and `always-loaded` from non-bundled skills (`research.md:24-32`). Scheduled-task discovery only scans bundled + admin (`research.md:135-139`).

The npm tool `vercel-labs/skills` (`/tmp/skills-inspect/package/dist/cli.mjs:712`) installs into hardcoded per-agent paths like `~/.claude/skills/`. None of those match decafclaw's three scan paths, so today there is no way for decafclaw to see those skills.

## Desired end state

A user can:

```bash
npx skills add vercel-labs/agent-skills -a claude-code -g
# skill files land at ~/.claude/skills/<name>/SKILL.md
```

Edit `data/{agent_id}/config.json`:

```json
{ "extra_skill_paths": ["~/.claude/skills"] }
```

Or pass via env: `EXTRA_SKILL_PATHS=~/.claude/skills,~/share/team-skills`

After restart (or `refresh_skills`), those skills appear in the catalog with the same trust posture as workspace/agent skills today: `auto-approve` stripped, `always-loaded` stripped, no scheduled-task registration, but `user-invocable` and `tools.py` work normally. They never shadow bundled skills.

## Design decisions

- **Decision:** New top-level `extra_skill_paths: list[str]` field on `Config` (not nested under a new `SkillsDiscoveryConfig` dataclass).
  - **Why:** Single field for a single concern; no other discovery options on the horizon worth coupling. Smallest blast radius.
  - **Rejected:** Nested `SkillsDiscoveryConfig` — symmetric with `VaultConfig`/`EmailConfig` but premature for one field.

- **Decision:** Externals slot **below** bundled in scan order (`workspace > agent > bundled > external`).
  - **Why:** Externals are inherently lower-trust (third-party npm content). Allowing them to shadow bundled critical skills (`vault`, `background`, `mcp`) would silently strip `always_loaded` from those skills (since the shadow loses always-loaded under the existing trust check) and break core decafclaw infra. Power users can still override bundled skills via `data/{agent_id}/skills/` or `data/{agent_id}/workspace/skills/`.
  - **Rejected:** Between agent and bundled (matches "later overrides earlier" intuition but is the foot-gun above). Carve-out exempting `always_loaded` skills from being shadowed (Option C from brainstorm) — extra code for a niche case the user can already work around.

- **Decision:** Expand `~` (via `Path.expanduser()`) and `$VAR` (via `os.path.expandvars()`) when reading paths from this field.
  - **Why:** External skill paths point at OS-level locations. `~/.claude/skills` is the natural way users will write them; demanding absolute paths is hostile UX.
  - **Rejected:** No expansion (current convention everywhere else) — that convention exists because all current paths are anchored to `agent_path`/`workspace_path`. Different field, different ergonomics.

- **Decision:** Relative paths (after `~`/`$VAR` expansion) are anchored to `config.agent_path`, mirroring `vault_root` (`research.md:67-72`).
  - **Why:** Consistent with the only existing user-configurable path field. A user who writes a relative path probably means "relative to my agent dir."
  - **Rejected:** Reject relative paths entirely (3b in brainstorm) — slightly more explicit but inconsistent with `vault_path` and adds an error path users will trip over.

- **Decision:** Trust boundary unchanged. Externals are treated identically to non-bundled (workspace/agent) skills.
  - **Why:** Falls out automatically from `is_relative_to(_BUNDLED_SKILLS_DIR)`. No new code for the trust checks; just new paths in the scan list.
  - **Concrete consequences:** `auto-approve` stripped (warning logged); `always-loaded` stripped; not eligible for scheduled-task registration (already excluded — `schedules.py` only scans bundled + admin); `user-invocable` allowed; `tools.py` loaded as Python with no sandboxing (same as workspace today).

- **Decision:** Env-var override `EXTRA_SKILL_PATHS` (comma-separated or JSON array, via existing `_parse_list`).
  - **Why:** Parity with other config fields (`research.md:75-79`). Lets users override per-shell without touching `config.json`.
  - **Rejected:** No env var — minor inconsistency with the rest of config plumbing.

- **Decision:** Non-existent or non-directory paths are silently skipped (existing `if not base_path.is_dir(): continue` at `src/decafclaw/skills/__init__.py:197-198` covers it).
  - **Why:** Same fail-soft semantics as the existing scan paths. A user removing `~/.claude/skills` shouldn't break decafclaw startup.
  - **Rejected:** Warning on missing — noisy if a path is intentionally absent on some machines.

## Patterns to follow

- **Config field declaration:** Top-level `Config` dataclass field with `field(default_factory=list)`, mirroring `default_model: str = ""` at `src/decafclaw/config.py:166` (top-level scalar/list, not nested). Add the field to the dataclass declaration at `src/decafclaw/config.py:151-180` and to the `Config(...)` constructor call at `src/decafclaw/config.py:474-497` (the constructor hand-lists fields; the "never enumerate fields" rule from CLAUDE.md applies to copies/forks/snapshots, not to this single canonical construction site).
- **Config loading:** Add to `load_config()` in `src/decafclaw/config.py:338-502`. Top-level fields don't go through `load_sub_config`, so do an inline env-then-file-then-default read (the `default_model` pattern at `config.py:447` is the precedent — but it has no env override). Use `_parse_list()` (already in this module) for the env var so JSON-array and comma-split forms work uniformly. Env var name: `EXTRA_SKILL_PATHS`. Pseudocode: `extra_skill_paths = _parse_list(os.environ["EXTRA_SKILL_PATHS"]) if "EXTRA_SKILL_PATHS" in os.environ else file_data.get("extra_skill_paths", [])`.
- **Path expansion + anchoring:** Mirror the `vault_root` property pattern at `src/decafclaw/config_types.py:210-213`: `Path(s).expanduser()`, then `os.path.expandvars()` on the string form, then `.is_absolute()` check, anchor relatives to `config.agent_path`. Single helper at module scope in `skills/__init__.py` (or `config.py`) so the resolution logic lives in one place.
- **Discovery integration:** Extend the `scan_paths` list in `discover_skills()` at `src/decafclaw/skills/__init__.py:186-189` — append resolved external paths AFTER `_BUNDLED_SKILLS_DIR`. Do not change the existing loop.
- **Tests:** Extend `tests/test_skills.py` using the existing `_write_skill()` helper (`research.md:173`) and `tmp_path`-driven `config` fixture (`research.md:162-170`). Tests to add:
  - External skill discovered when path is in `config.extra_skill_paths`
  - Bundled skill wins over same-named external skill (precedence verification)
  - Workspace and agent skills both win over same-named external skill
  - `auto-approve: true` stripped from external skill (parity with existing workspace test at `test_discover_strips_auto_approve_from_workspace_skill`)
  - `~` expansion works (use `monkeypatch.setenv("HOME", str(tmp_path))` and write to `~/.foo/skills`)
  - `$VAR` expansion works
  - Relative external path anchored to `agent_path`
  - Non-existent external path silently skipped
  - `EXTRA_SKILL_PATHS` env var (comma-separated and JSON array forms)
- **Docs:** New section in `docs/skills.md` covering: the `extra_skill_paths` field, the `npx skills` install workflow with `-a claude-code -g`, the trust posture (no auto-approve, no scheduling), and the limitation that externally-installed skills won't include decafclaw-specific `tools.py` extensions. Update `CLAUDE.md` skills section with a one-line reference.

## What we're NOT doing

- **No upstream PR** to `vercel-labs/skills` to register a `decafclaw` agent target. Explicitly ruled out by the user.
- **No automatic sniffing** of well-known paths (e.g., auto-scanning `~/.claude/skills` if it exists). User must opt in via config.
- **No `Makefile` wrapper** for invoking `npx skills add` with sensible defaults — this spec only adds discovery; install workflow is upstream's responsibility.
- **No symlink/copy automation** from external paths into decafclaw's existing scan paths — extra paths just get scanned in place.
- **No new trust tier** for "trusted external paths" that bypasses the bundled check. Externals match workspace trust posture exactly.
- **No carve-out** for shadowing bundled `always_loaded` skills (Option C from brainstorm). Externals slot below bundled; cannot shadow.
- **No reshape of `config.skills`** (currently a per-skill config dict) into a unified dataclass.
- **No deduplication** when an external path overlaps an existing scan path. The first-found-wins logic handles it; duplicates produce a debug log and continue.
- **No global-vs-per-agent split.** Each decafclaw agent's `config.json` declares its own `extra_skill_paths`. Multi-agent setups duplicate the field if they want shared external skills.
- **No `refresh_skills` changes.** The existing `tool_refresh_skills` re-runs `discover_skills` and will pick up changes to `extra_skill_paths` paths automatically.

## Open questions

None blocking. Defaults the plan can proceed with:

- **Field default** is `[]` (empty list).
- **Env var separator** matches `_parse_list` (`research.md:88-90`): JSON array if it parses, else comma-split.
- **Order within `extra_skill_paths`** is preserved (config order = scan order); no sort. First-found-wins applies within the list too.
