# Polymorphic `extra_skill_paths` — per-skill opt-in for shared optional skills

## Problem

Optional skills like `linkding-ingest` and `mastodon-ingest` live in `contrib/skills/` — versioned templates that aren't loaded by default because they need API keys / binaries and aren't universally useful. To actually use one, the deployment operator runs:

```bash
cp -r contrib/skills/linkding-ingest data/{agent_id}/skills/
data/{agent_id}/skills/linkding-ingest/download-binary.sh
```

After that, the skill is loaded but is now a *copy*, disconnected from `git pull`. Every update to the canonical template requires re-copying. In practice this means updates to optional skills don't reach the deployments using them unless someone remembers to re-copy.

## Goal

Let a deployment opt into a shared optional skill **by reference**, not by copy, so:

1. `git pull` automatically delivers SKILL.md updates to deployments using the skill.
2. Per-skill opt-in (not all-or-nothing for a directory).
3. Per-deployment override is still possible (drop a local copy under `data/{agent_id}/skills/` to shadow the shared version).
4. No new config field; reuse the existing `extra_skill_paths` mechanism.

## Mechanism

Extend `extra_skill_paths` to accept two kinds of entries (today it only accepts the first):

1. **Directory of skills** (existing behavior): the path is a directory whose subdirectories each contain a `SKILL.md`. Each subdirectory is a discovered skill.
2. **Direct skill directory** (new behavior): the path itself contains a `SKILL.md` at its root. The path IS the skill.

Detection rule: if `<path>/SKILL.md` exists, treat as a direct skill directory. Otherwise, scan subdirectories as today.

Example config (relative paths anchor to `data/{agent_id}/`):

```json
{
  "extra_skill_paths": [
    "../../contrib/skills/linkding-ingest",
    "../../contrib/skills/mastodon-ingest",
    "~/.claude/skills"
  ]
}
```

First two = per-skill opt-in (direct skill dirs). Third = a directory containing zero or more skills (current behavior). Absolute paths and `$VAR` expansion also work; relative entries resolve against `config.agent_path` via the existing `_resolve_extra_skill_paths` helper.

## Loader change

`src/decafclaw/skills/__init__.py` — function `discover_skills()` around line 194-269.

Current logic: for each `base_path` in `scan_paths`, iterate `base_path.iterdir()` and parse each subdir's `SKILL.md`.

Change: before iterating subdirs, check if `base_path / "SKILL.md"` exists. If yes, parse `base_path` directly as a skill and skip the subdir scan for that path. If no, fall through to the existing subdir scan.

The `_resolve_extra_skill_paths()` helper at line 178 needs no change — it already expands env vars, expands `~`, and anchors relative paths to `config.agent_path`. The new direct-skill-dir entries get the same treatment.

Estimated change: ~10 lines in `discover_skills()`, no API surface change.

## Priority unchanged

The existing scan order stays:

1. Workspace skills (`data/{agent_id}/workspace/skills/`)
2. Admin skills (`data/{agent_id}/skills/`)
3. Bundled skills (`src/decafclaw/skills/`)
4. `extra_skill_paths` (lowest)

So a deployment that wants to customize a shared skill can still drop a local copy at `data/{agent_id}/skills/linkding-ingest/` and that will shadow the contrib version via the existing `seen_names` dedupe. Local override always wins.

## Binary handling

`download-binary.sh` lives next to `SKILL.md` and downloads to `$SKILL_DIR/bin/{platform}/`. With the new mechanism:

- `$SKILL_DIR` resolves to `contrib/skills/linkding-ingest/` for an opted-in deployment.
- Binaries download into `contrib/skills/linkding-ingest/bin/{platform}/`.
- `contrib/skills/.gitignore` already excludes `*/bin/`, so binaries persist across `git pull` and stay out of version control.
- `fetch.sh` references `$SKILL_DIR/bin/{platform}/...` and works unchanged.

No code changes to the binary download or fetch scripts.

## Migration (per deployment, one-time)

For an existing deployment using the cp-based approach:

1. Edit `data/{agent_id}/config.json` to add an `extra_skill_paths` entry (relative paths anchor to `data/{agent_id}/`):
   ```json
   {
     "extra_skill_paths": [
       "../../contrib/skills/linkding-ingest",
       "../../contrib/skills/mastodon-ingest"
     ]
   }
   ```
2. Run `contrib/skills/{name}/download-binary.sh` to seed binaries in `contrib/`.
3. Optionally delete `data/{agent_id}/skills/{name}/` once verified.

Step 3 is optional — a leftover admin-level copy will shadow the contrib version via the priority order, which is the same "override" mechanism users get for customizing a shared skill. Deletion is just cleanup.

The old `cp -r` install path keeps working for users who want a fully detached, customized copy.

## Documentation updates

- `contrib/skills/README.md`: recommend the path-based install as the default. Keep the `cp -r` flow documented as an explicit "fork for customization" option.
- `docs/skills.md`: short note about the polymorphic semantics of `extra_skill_paths`.
- `docs/config.md` (if it documents `extra_skill_paths`): update the description.

## Edge cases

- **Path doesn't exist / isn't a directory.** Silent skip with a debug log. Matches existing behavior for missing scan dirs (`if not base_path.is_dir(): continue` at line 215).
- **Path is a skill dir AND has SKILL.md-bearing subdirs.** Treat as direct skill; ignore subdirs. Documented as a "don't do that" but doesn't crash.
- **Name collision with a higher-priority skill.** Existing `seen_names` dedupe handles it — first-seen wins per the priority order. A contrib-level `linkding-ingest` is shadowed by an admin-level `linkding-ingest` if both exist. (This is the override mechanism.)
- **Trust flags (`auto_approve`, `always-loaded`) declared in a contrib skill.** The existing check at line 235 strips them with a warning unless the skill is under `_BUNDLED_SKILLS_DIR`. Direct-skill-dir entries from `extra_skill_paths` are not bundled, so flags get stripped — correct behavior.

## Test plan

- Add a unit test in `tests/` that exercises `discover_skills()` with an `extra_skill_paths` entry pointing at a direct skill dir (a temp dir containing only a SKILL.md) — verify the skill is discovered.
- Add a unit test for the directory-of-skills behavior (existing) to confirm it still works after the change.
- Add a unit test for a mixed config: one direct path, one directory path, verify both load.
- Add a unit test for a non-existent path entry — verify it's silently skipped.
- Manual: in Les's deployment, add the two contrib paths to `extra_skill_paths`, restart the agent, confirm both skills appear in the catalog. Run `/linkding-ingest` and verify it works (covers the PR #445 changes in production).

## Non-goals

- Renaming `extra_skill_paths` — keeps backwards compatibility.
- Auto-discovery of contrib/skills/ as a default load location — opt-in stays explicit.
- A skill-management UI in the web console — out of scope for this change.
- Moving the ingest skills out of `contrib/skills/` into `src/decafclaw/skills/` as bundled — separate question about whether they should be universally available.
