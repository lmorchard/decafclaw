# Polymorphic `extra_skill_paths` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `extra_skill_paths` to accept direct-skill-dir entries (path that itself contains `SKILL.md`), so deployments can opt into shared optional skills from `contrib/skills/` by reference without copying.

**Architecture:** Refactor the inner loop of `discover_skills()` to use a small generator helper that yields skill-directory candidates for a given scan entry — yielding the entry itself when it's a direct skill dir, or its subdirs otherwise. No new config field; the polymorphism is transparent to callers.

**Tech Stack:** Python 3.12, pytest, existing `decafclaw.skills` module.

---

## File Structure

**Modified:**
- `src/decafclaw/skills/__init__.py` — extract `_iter_skill_dirs()` helper, update `discover_skills()` to use it
- `tests/test_skills.py` — add tests for polymorphic behavior
- `contrib/skills/README.md` — document path-based install as the default
- `docs/skills.md` — note polymorphic semantics

No new files. All changes are local to the skill-discovery layer; no API surface changes.

---

### Task 1: Add the `_iter_skill_dirs` helper and rewire `discover_skills`

**Files:**
- Modify: `src/decafclaw/skills/__init__.py:194-268`
- Test: `tests/test_skills.py` (append after existing `discover_skills` tests)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_skills.py`:

```python
def test_discover_loads_direct_skill_dir_from_extra_paths(tmp_path, config):
    """An entry in extra_skill_paths that points directly at a skill
    directory (one with SKILL.md at its root) loads that skill."""
    direct_skill = tmp_path / "my-direct-skill"
    _write_skill(
        direct_skill,
        "name: my-direct-skill\ndescription: Loaded directly.",
    )
    config.extra_skill_paths = [str(direct_skill)]

    skills = discover_skills(config)
    matching = [s for s in skills if s.name == "my-direct-skill"]
    assert len(matching) == 1
    assert matching[0].description == "Loaded directly."
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_skills.py::test_discover_loads_direct_skill_dir_from_extra_paths -v
```

Expected: FAIL. The current loader does `base_path.iterdir()` and looks for SKILL.md inside *subdirs*, so a path that itself contains SKILL.md gets iterated for its subdirs (which won't have SKILL.md at all) and yields nothing.

- [ ] **Step 3: Implement the loader change**

In `src/decafclaw/skills/__init__.py`, add the helper just above `discover_skills()` (around line 193):

```python
def _iter_skill_dirs(base_path: Path):
    """Yield skill-directory candidates for a scan entry.

    If ``base_path`` itself contains ``SKILL.md`` at its root, it IS a
    skill directory — yield it directly. Otherwise, if ``base_path`` is
    a directory, yield each immediate subdirectory (the caller checks
    for SKILL.md presence inside each).

    Yields nothing when ``base_path`` doesn't exist or isn't a directory.
    """
    if (base_path / "SKILL.md").exists():
        yield base_path
        return
    if not base_path.is_dir():
        return
    for entry in sorted(base_path.iterdir()):
        if entry.is_dir():
            yield entry
```

Then in `discover_skills()`, replace the outer scan loop body (the `for base_path in scan_paths:` block, lines 214-265) so the candidate iteration goes through the helper:

```python
    for base_path in scan_paths:
        for skill_dir in _iter_skill_dirs(base_path):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            info = parse_skill_md(skill_md)
            if info is None:
                continue

            # auto-approve and always-loaded are bundled-only (trust
            # boundary). Admin, workspace, and external skills declaring
            # them get the flag stripped and a warning logged so the
            # documented "bundled-only" trust posture is enforced
            # uniformly (catalog text + activation-time tool caching).
            is_bundled = skill_dir.resolve().is_relative_to(bundled_dir)
            if info.auto_approve and not is_bundled:
                log.warning(
                    "Ignoring 'auto-approve: true' on non-bundled skill "
                    "'%s' at %s — only bundled skills may auto-approve.",
                    info.name, skill_dir,
                )
                info.auto_approve = False
            if info.always_loaded and not is_bundled:
                log.warning(
                    "Ignoring 'always-loaded: true' on non-bundled skill "
                    "'%s' at %s — only bundled skills may always-load.",
                    info.name, skill_dir,
                )
                info.always_loaded = False

            # Check requires.env
            missing_env = [v for v in info.requires_env if not os.environ.get(v)]
            if missing_env:
                log.debug(f"Skipping skill '{info.name}': missing env vars {missing_env}")
                continue

            # Name collision: first-found wins
            if info.name in seen_names:
                log.debug(
                    f"Skill '{info.name}' at {skill_dir} shadowed by {seen_names[info.name]}"
                )
                continue

            seen_names[info.name] = skill_dir
            skills.append(info)
```

The only structural change is replacing the `if not base_path.is_dir(): continue` + `for skill_dir in sorted(base_path.iterdir()): if not skill_dir.is_dir(): continue` preamble with a single `for skill_dir in _iter_skill_dirs(base_path):`. The body — SKILL.md check, parse, flag-stripping, env-check, dedupe, append — is unchanged.

Update the `discover_skills` docstring to add a line about polymorphism. After the existing scan-order list:

```python
    """Scan skill directories and return discovered skills.

    Scan order (highest priority first):
    1. Workspace skills: data/{agent_id}/workspace/skills/
    2. Agent-level skills: data/{agent_id}/skills/
    3. Bundled skills: src/decafclaw/skills/
    4. config.extra_skill_paths (lowest — never shadows bundled)

    Each scan entry is interpreted polymorphically: if the entry path
    itself contains a SKILL.md at its root, it IS the skill (per-skill
    opt-in). Otherwise, each immediate subdirectory with a SKILL.md is
    a discovered skill (directory-of-skills, the original behavior).
    Both forms can be freely mixed within `extra_skill_paths`.
    """
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_skills.py::test_discover_loads_direct_skill_dir_from_extra_paths -v
```

Expected: PASS.

- [ ] **Step 5: Run the full test_skills suite to verify no regressions**

```bash
pytest tests/test_skills.py -v
```

Expected: All tests pass — the rewiring is structurally equivalent for directory-of-skills entries.

- [ ] **Step 6: Commit**

```bash
git add src/decafclaw/skills/__init__.py tests/test_skills.py
git commit -m "$(cat <<'EOF'
feat(skills): polymorphic extra_skill_paths — direct-skill-dir entries

discover_skills now treats each scan entry polymorphically: if the
path itself contains SKILL.md at its root, it IS the skill; otherwise
its subdirs are scanned as before. Lets deployments opt into a
specific shared skill (e.g. contrib/skills/linkding-ingest) by
reference, so git pull keeps SKILL.md up to date without manual cp.

Implementation: extract _iter_skill_dirs helper; the rest of the
discover_skills body is unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Regression tests for mixed config and non-existent paths

**Files:**
- Test: `tests/test_skills.py` (append after the test added in Task 1)

- [ ] **Step 1: Write the mixed-config test**

Append to `tests/test_skills.py`:

```python
def test_discover_mixed_extra_paths(tmp_path, config):
    """extra_skill_paths can mix direct skill dirs and directory-of-skills
    entries; both kinds load."""
    direct = tmp_path / "direct-skill"
    _write_skill(direct, "name: direct-skill\ndescription: Direct.")

    dir_of_skills = tmp_path / "skills-root"
    _write_skill(dir_of_skills / "child-a", "name: child-a\ndescription: Child A.")
    _write_skill(dir_of_skills / "child-b", "name: child-b\ndescription: Child B.")

    config.extra_skill_paths = [str(direct), str(dir_of_skills)]

    skills = discover_skills(config)
    names = {s.name for s in skills}
    assert "direct-skill" in names
    assert "child-a" in names
    assert "child-b" in names
```

- [ ] **Step 2: Run the test**

```bash
pytest tests/test_skills.py::test_discover_mixed_extra_paths -v
```

Expected: PASS.

- [ ] **Step 3: Write the missing-path test**

Append to `tests/test_skills.py`:

```python
def test_discover_skips_missing_extra_path(tmp_path, config, caplog):
    """A non-existent path in extra_skill_paths is silently skipped —
    no crash, no unrelated skills lost."""
    real_skill = tmp_path / "real-skill"
    _write_skill(real_skill, "name: real-skill\ndescription: Real.")

    config.extra_skill_paths = [
        str(tmp_path / "does-not-exist"),
        str(real_skill),
    ]

    skills = discover_skills(config)
    names = {s.name for s in skills}
    assert "real-skill" in names
```

- [ ] **Step 4: Run the test**

```bash
pytest tests/test_skills.py::test_discover_skips_missing_extra_path -v
```

Expected: PASS.

- [ ] **Step 5: Write the override-still-works test**

The existing priority order (admin > extra) means a local copy still shadows a contrib-referenced skill. Verify this explicitly so the override mechanism is covered:

```python
def test_discover_admin_skill_shadows_direct_extra_path(tmp_path, config):
    """A skill at admin level (data/{agent_id}/skills/) wins over a
    direct-skill-dir entry in extra_skill_paths with the same name —
    this is how per-deployment overrides of shared optional skills work."""
    # Direct entry in extra_skill_paths
    shared = tmp_path / "shared-loc"
    _write_skill(shared, "name: shared-skill\ndescription: From contrib.")

    # Admin-level shadow
    admin_skills = config.agent_path / "skills"
    _write_skill(
        admin_skills / "shared-skill",
        "name: shared-skill\ndescription: Local override.",
    )

    config.extra_skill_paths = [str(shared)]

    skills = discover_skills(config)
    matching = [s for s in skills if s.name == "shared-skill"]
    assert len(matching) == 1
    assert matching[0].description == "Local override."
```

- [ ] **Step 6: Run the test**

```bash
pytest tests/test_skills.py::test_discover_admin_skill_shadows_direct_extra_path -v
```

Expected: PASS.

- [ ] **Step 7: Run the full test_skills suite once more**

```bash
pytest tests/test_skills.py -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add tests/test_skills.py
git commit -m "$(cat <<'EOF'
test(skills): coverage for polymorphic extra_skill_paths

- mixed config: direct skill dir + directory-of-skills both load
- missing path entries are silently skipped
- admin-level shadow still wins over direct-skill-dir extra paths
  (the per-deployment override mechanism for shared skills)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Update `contrib/skills/README.md`

**Files:**
- Modify: `contrib/skills/README.md`

- [ ] **Step 1: Rewrite the Installation section**

Read the current file first:

```bash
cat contrib/skills/README.md
```

Replace the `## Installation` section with:

```markdown
## Installation

Two installation styles, depending on whether you want updates to flow with `git pull`.

### Option 1 — Reference (recommended)

Add the skill's directory to your agent's `extra_skill_paths` so the loader picks it up in place. `git pull` then keeps `SKILL.md` up to date automatically; downloaded binaries persist (they're already gitignored).

In `data/{agent_id}/config.json`:

```json
{
  "extra_skill_paths": [
    "../../contrib/skills/linkding-ingest",
    "../../contrib/skills/mastodon-ingest"
  ]
}
```

Each entry points at a single skill directory (one with `SKILL.md` at its root). **Relative paths are anchored to `data/{agent_id}/`**, so `../../contrib/skills/<name>` reaches the repo's `contrib/skills/` directory when `data_home` is at its default `./data` location. Absolute paths and `~` / `$VAR` expansion also work — e.g. set `DECAFCLAW_REPO=/path/to/repo` in `.env` and use `$DECAFCLAW_REPO/contrib/skills/<name>` to decouple from the `data_home` layout.

Then download the required binaries (run from the repo root):

```bash
contrib/skills/linkding-ingest/download-binary.sh
```

And set the required environment variables (in `.env` or `config.json` `env` section).

### Option 2 — Copy (fork for customization)

If you want a fully detached copy you can edit per-deployment, copy the skill directory into your agent's admin-level skills folder:

```bash
cp -r contrib/skills/linkding-ingest data/{agent_id}/skills/
data/{agent_id}/skills/linkding-ingest/download-binary.sh
```

A skill at `data/{agent_id}/skills/<name>/` shadows any same-named entry in `extra_skill_paths`, so you can also start with Option 1 and switch to Option 2 later if you need to customize.
```

- [ ] **Step 2: Visually review the rendered Markdown**

```bash
cat contrib/skills/README.md
```

Confirm the section reads cleanly and that both installation options are described accurately.

- [ ] **Step 3: Commit**

```bash
git add contrib/skills/README.md
git commit -m "$(cat <<'EOF'
docs(contrib): document path-based skill install as the default

Adds the new \"reference\" install using extra_skill_paths pointed at
the contrib skill directory directly. Keeps the cp -r flow as
\"fork for customization\" with a note that admin-level skills shadow
extra_skill_paths entries (so users can start with reference and
switch to copy later).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Update `docs/skills.md`

**Files:**
- Modify: `docs/skills.md:233` (update the discovery table row)
- Modify: `docs/skills.md` — add a new subsection after the table at line 235

The discovery section already documents `extra_skill_paths` in a four-row table and has a "Using community skills / Installing skills via `npx skills`" section that covers directory-of-skills usage. We just need to (a) note the polymorphism on the table row and (b) add a focused subsection explaining direct-skill-dir entries.

- [ ] **Step 1: Update the discovery-table row for tier 4**

Replace this line (`docs/skills.md:233`):

```markdown
| 4 | Paths listed in `extra_skill_paths` config | Externally-managed (e.g., `npx skills add`). Lowest priority — cannot shadow bundled skills. |
```

with:

```markdown
| 4 | Paths listed in `extra_skill_paths` config | Externally-managed. Each entry can be a directory of skills (e.g., `~/.claude/skills`) or a direct skill directory (e.g., `../../contrib/skills/linkding-ingest`). Lowest priority — cannot shadow bundled skills. |
```

- [ ] **Step 2: Add a new subsection after the table**

Insert this new subsection right after the existing line `Higher-priority skills override lower-priority ones...` (currently `docs/skills.md:235`), before the `## Activation and permissions` heading:

```markdown
### Direct skill paths vs. directories of skills

Each entry in `extra_skill_paths` is interpreted polymorphically:

- **Directory of skills** — the entry points at a directory whose immediate subdirectories each contain a `SKILL.md`. Each subdir is a discovered skill. Common targets: `~/.claude/skills`, `~/.agents/skills` (see the `npx skills` section below).
- **Direct skill directory** — the entry path itself contains a `SKILL.md` at its root and IS the skill. Use this to opt into a specific shared skill (e.g. one in `contrib/skills/`) without copying it into `data/{agent_id}/skills/`.

The two forms can be mixed within the same `extra_skill_paths` list. Detection is per-entry — the loader checks for `SKILL.md` at the entry path first; if absent, it falls back to scanning subdirectories.

Example mixing both forms (relative paths anchor to `data/{agent_id}/`, so `../../contrib/skills/<name>` reaches the repo's `contrib/skills/` directory when `data_home` is at its default `./data` location):

```json
{
  "extra_skill_paths": [
    "../../contrib/skills/linkding-ingest",
    "../../contrib/skills/mastodon-ingest",
    "~/.claude/skills"
  ]
}
```

For deployments where `data_home` lives outside the repo, use absolute paths or `$VAR` expansion (e.g. set `DECAFCLAW_REPO=/path/to/repo` in `.env` and reference `$DECAFCLAW_REPO/contrib/skills/<name>`).

Per-deployment customization still works via the priority order: a same-named skill under `data/{agent_id}/skills/<name>/` shadows any entry in `extra_skill_paths`. So you can start by referencing a shared skill and switch to a local copy later if you need to fork it.
```

- [ ] **Step 3: Visually review the change**

```bash
git diff docs/skills.md
```

Confirm the table row reads correctly and the new subsection flows naturally between the discovery table and the activation/permissions section.

- [ ] **Step 4: Commit**

```bash
git add docs/skills.md
git commit -m "$(cat <<'EOF'
docs(skills): document polymorphic extra_skill_paths

Update the discovery-table row and add a new subsection explaining
the two entry forms (directory of skills vs. direct skill directory)
and how per-deployment overrides still work via the priority order.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Run the full check pipeline

**Files:** None modified.

- [ ] **Step 1: Run `make check`**

```bash
make check
```

Expected: all checks pass (lint + typecheck for Python and JS). The change is small and Python-only, but this catches any incidental lint/format issues.

- [ ] **Step 2: Run the full test suite**

```bash
make test
```

Expected: all tests pass.

- [ ] **Step 3: Skim `pytest --durations=25`**

```bash
pytest --durations=25 2>&1 | tail -30
```

Expected: no new tests appear in the slow-25. The new tests use `tmp_path` and `_write_skill` (filesystem only, no scheduler/timer paths), so they should be milliseconds.

If a new test does land in the top-25, investigate — likely a missing fixture or accidental real-IO before deciding it's fine.

---

### Task 6: Open the PR

**Files:** None modified.

- [ ] **Step 1: Push the branch**

```bash
git push -u origin feat/polymorphic-extra-skill-paths
```

- [ ] **Step 2: Create the PR**

```bash
gh pr create --title "feat(skills): polymorphic extra_skill_paths" --body "$(cat <<'EOF'
## Summary

- Extend `extra_skill_paths` to accept direct-skill-dir entries (paths whose root contains `SKILL.md`) in addition to directory-of-skills entries. Detection is per-entry; both forms can be mixed.
- Lets deployments opt into a specific shared optional skill (e.g. `contrib/skills/linkding-ingest`) by reference instead of copying. `git pull` keeps `SKILL.md` up to date automatically; per-deployment customization still works by dropping a local copy under `data/{agent_id}/skills/<name>/` (which shadows the extra-paths entry via the existing priority order).
- Loader change is a small refactor: extract `_iter_skill_dirs` helper; the rest of `discover_skills` is unchanged.

## Test plan

- [ ] `make check` passes
- [ ] `make test` passes
- [ ] Manual smoke in the deployed agent: add `contrib/skills/linkding-ingest` and `contrib/skills/mastodon-ingest` to `data/{agent_id}/config.json` `extra_skill_paths`, restart, confirm both skills appear in the catalog and `/linkding-ingest` works end-to-end (this validates the path-based install in production)

Spec: `docs/dev-sessions/2026-05-12-1312-polymorphic-skill-paths/spec.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Add Copilot as reviewer**

```bash
gh pr edit <PR_NUMBER> --add-reviewer copilot-pull-request-reviewer
```

(Replace `<PR_NUMBER>` with the number returned by the previous step.)

---

## Post-merge verification (manual, in deployment)

Not part of the plan tasks — runs after merge:

1. On the deployed agent (lmorchard@decafclaw), `git pull` to pick up the loader change.
2. Edit `data/{agent_id}/config.json` to add (relative paths anchor to `data/{agent_id}/`):
   ```json
   "extra_skill_paths": [
     "../../contrib/skills/linkding-ingest",
     "../../contrib/skills/mastodon-ingest"
   ]
   ```
3. Run `contrib/skills/linkding-ingest/download-binary.sh` and the mastodon equivalent (if binaries aren't already in `contrib/`).
4. Restart the agent.
5. Confirm both skills appear in the catalog.
6. Trigger `/linkding-ingest` and `/mastodon-ingest` to confirm end-to-end.
7. Optionally: delete the now-redundant `data/{agent_id}/skills/{linkding,mastodon}-ingest/` directories once verified.
