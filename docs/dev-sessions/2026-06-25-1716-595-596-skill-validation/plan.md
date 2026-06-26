# Skill Validation Reporting (#595) + `skill_validate` Lint (#596) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface skill-discovery rejection reasons in `refresh_skills` (#595) and add a `skill_validate(path)` pre-flight lint tool (#596), both built on one shared discovery-level validation function so rejection reasons and lint checks never drift from `parse_skill_md`'s actual accept/reject decision.

**Architecture:** Extract the discovery-level accept/reject logic out of `parse_skill_md` into `validate_skill_md(path) -> SkillValidation` (the single source of truth). `parse_skill_md` becomes a thin `validate → build_skill_info` wrapper (behavior unchanged). `discover_skills` routes through the same validation and records `SkillRejection`s into an optional accumulator threaded out through `load_system_prompt` to `refresh_skills`. `skill_validate` reuses `validate_skill_md` and adds `tools.py`-specific checks (filename, clean import, `get_tools(ctx)` / `TOOLS` export).

**Tech Stack:** Python 3 (stdlib `importlib`, `inspect`), `dataclasses`, `yaml`; pytest (`-n auto` via pytest-xdist); decafclaw skill/tool registry; YAML eval harness (`evals/tool_choice/`).

## Global Constraints

- Stdlib imports at module level; function-level imports only to break cycles (existing `skill_tools.py` deferred imports of `..prompts` / `..tool_definitions` stay as-is).
- New runtime state goes on the dataclass — no `setattr`/`getattr` of undeclared attributes.
- Never enumerate fields when copying/snapshotting; `build_skill_info` is a constructor from parsed `meta`, not a copy of another object.
- Tools receive `ctx` as first param, always.
- Errors return `ToolResult(text="[error: ...]")`, not bare strings. Use `ToolResult.data` for structured results.
- Zero tolerance for warnings/traceback noise; never `except: pass` — use `except Exception as exc:` with a log line.
- Tool descriptions are a control surface — wording is deliberate.
- Bug/feature behavior is verified test-first (TDD): failing test → run it fail → implement → run it pass → commit.
- Tests must not `asyncio.sleep` to wait; no hand-listed field allowlists.
- `make check` (lint + typecheck) and `make test` must be green before the final commit of each task.
- Work happens in the worktree `.claude/worktrees/595-596-skill-validation` (venv already synced; `HTTP_PORT=18895`).

---

## File Structure

- `src/decafclaw/skills/__init__.py` — **modify.** Add `CheckResult`, `SkillValidation`, `SkillRejection` dataclasses; add `validate_skill_md()` and `build_skill_info()`; refactor `parse_skill_md()` to a wrapper; add `rejections` accumulator param to `discover_skills()`.
- `src/decafclaw/prompts/__init__.py` — **modify.** Thread a `rejections` param through `load_system_prompt()` into `discover_skills()`.
- `src/decafclaw/tools/skill_tools.py` — **modify.** Make `refresh_skills` collect + report rejections; add `_lint_tools_py()`, `_render_validation()`, `tool_skill_validate()`; register the new tool.
- `tests/test_skills.py` — **modify.** New `validate_skill_md` tests; `discover_skills` accumulator test; `skill_validate` tests. Existing `parse_skill_md` tests stay green unchanged.
- `evals/tool_choice/core_overlaps.yaml` — **modify.** Add a `skill_validate` vs `refresh_skills` disambiguation case.
- `docs/skills.md` — **modify.** Document rejection reporting + the `skill_validate` contract checklist.

---

## Task 1: Shared validation core (`validate_skill_md` / `build_skill_info`, `parse_skill_md` becomes a wrapper)

**Files:**
- Modify: `src/decafclaw/skills/__init__.py` (dataclasses near line 37–66; functions at 69–126)
- Test: `tests/test_skills.py`

**Interfaces:**
- Produces:
  - `@dataclass CheckResult(name: str, passed: bool, message: str)`
  - `@dataclass SkillValidation(path: Path, checks: list[CheckResult], meta: dict | None = None, body: str = "")` with `.ok: bool` and `.first_failure: str | None` properties.
  - `@dataclass SkillRejection(path: Path, reason: str)`
  - `validate_skill_md(path: Path) -> SkillValidation` — the sole discovery-level accept/reject decision (checks, in order: `readable`, `frontmatter`, `name`, `description`; short-circuits at first failure; parses frontmatter once).
  - `build_skill_info(result: SkillValidation) -> SkillInfo` — builds from a validated (`ok`) result.
  - `parse_skill_md(path: Path) -> SkillInfo | None` — unchanged behavior; now `validate → build`.

- [ ] **Step 1: Confirm `parse_skill_md` has no production callers other than the ones we control**

Run:
```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/595-596-skill-validation
grep -rn "parse_skill_md" src/ tests/
```
Expected: references only in `src/decafclaw/skills/__init__.py` (def + internal) and `tests/test_skills.py`. If any other production module imports it, note it — the wrapper preserves behavior so it stays safe, but confirm.

- [ ] **Step 2: Write failing tests for `validate_skill_md`**

Add to `tests/test_skills.py` (import the new symbols at the top: extend the existing `from decafclaw.skills import ...` line to include `CheckResult, SkillRejection, SkillValidation, build_skill_info, validate_skill_md`):

```python
# -- validate_skill_md tests --


def _checks_by_name(result):
    return {c.name: c for c in result.checks}


def test_validate_ok(tmp_path):
    skill_dir = tmp_path / "ok"
    _write_skill(skill_dir, "name: ok\ndescription: Fine.")
    result = validate_skill_md(skill_dir / "SKILL.md")
    assert result.ok is True
    assert result.first_failure is None
    assert result.meta["name"] == "ok"
    assert result.body.strip() == "Some instructions."


def test_validate_no_frontmatter(tmp_path):
    skill_dir = tmp_path / "no-fm"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Heading, no frontmatter\n")
    result = validate_skill_md(skill_dir / "SKILL.md")
    assert result.ok is False
    assert _checks_by_name(result)["frontmatter"].passed is False
    assert "frontmatter" in result.first_failure.lower()


def test_validate_missing_name(tmp_path):
    skill_dir = tmp_path / "no-name"
    _write_skill(skill_dir, "description: No name.")
    result = validate_skill_md(skill_dir / "SKILL.md")
    assert result.ok is False
    assert _checks_by_name(result)["name"].passed is False
    # frontmatter passed before name failed
    assert _checks_by_name(result)["frontmatter"].passed is True


def test_validate_missing_description(tmp_path):
    skill_dir = tmp_path / "no-desc"
    _write_skill(skill_dir, "name: no-desc")
    result = validate_skill_md(skill_dir / "SKILL.md")
    assert result.ok is False
    assert _checks_by_name(result)["description"].passed is False


def test_validate_unreadable(tmp_path):
    missing = tmp_path / "gone" / "SKILL.md"
    result = validate_skill_md(missing)
    assert result.ok is False
    assert _checks_by_name(result)["readable"].passed is False


def test_build_skill_info_from_validation(tmp_path):
    skill_dir = tmp_path / "full"
    _write_skill(skill_dir, "name: full\ndescription: Full.", tools_py=True)
    result = validate_skill_md(skill_dir / "SKILL.md")
    info = build_skill_info(result)
    assert info.name == "full"
    assert info.description == "Full."
    assert info.location == skill_dir
    assert info.has_native_tools is True
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run:
```bash
uv run pytest tests/test_skills.py -k "validate_ or build_skill_info_from" -q
```
Expected: FAIL / ImportError — `validate_skill_md`, `build_skill_info`, etc. not defined.

- [ ] **Step 4: Add the dataclasses**

In `src/decafclaw/skills/__init__.py`, immediately after the `SkillInfo` dataclass (after line 66), add:

```python
@dataclass
class CheckResult:
    """One pass/fail check produced during skill validation."""

    name: str  # "readable" | "frontmatter" | "name" | "description" | tools.py checks
    passed: bool
    message: str  # actionable on failure, descriptive on pass


@dataclass
class SkillValidation:
    """Result of validating a SKILL.md — the discovery-level accept/reject decision.

    `meta`/`body` carry the parsed frontmatter so callers (e.g.
    build_skill_info) don't re-parse. Checks are appended in order and
    short-circuit at the first failure.
    """

    path: Path
    checks: list[CheckResult]
    meta: dict | None = None
    body: str = ""

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def first_failure(self) -> str | None:
        for c in self.checks:
            if not c.passed:
                return c.message
        return None


@dataclass
class SkillRejection:
    """A skill directory found during discovery but rejected, with the reason."""

    path: Path
    reason: str
```

- [ ] **Step 5: Add `validate_skill_md` and `build_skill_info`; rewrite `parse_skill_md` as a wrapper**

Replace the entire current `parse_skill_md` function (lines 69–126) with:

```python
def validate_skill_md(path: Path) -> SkillValidation:
    """Run the discovery-level checks on a SKILL.md — THE accept/reject decision.

    Checks run in order and short-circuit at the first failure (you can't
    check `name` without frontmatter). Frontmatter is parsed exactly once
    and returned via `meta`/`body` for build_skill_info to reuse.
    """
    checks: list[CheckResult] = []

    try:
        text = path.read_text()
    except OSError as e:
        checks.append(CheckResult("readable", False, f"cannot read SKILL.md: {e}"))
        return SkillValidation(path=path, checks=checks)
    checks.append(CheckResult("readable", True, "SKILL.md is readable"))

    meta, body = _split_frontmatter(text)
    if meta is None:
        checks.append(CheckResult(
            "frontmatter", False,
            "no valid YAML frontmatter — file must start with a '---' frontmatter block",
        ))
        return SkillValidation(path=path, checks=checks, body=text)
    checks.append(CheckResult("frontmatter", True, "valid YAML frontmatter"))

    if not meta.get("name"):
        checks.append(CheckResult(
            "name", False, "missing required 'name' field in frontmatter",
        ))
        return SkillValidation(path=path, checks=checks, meta=meta, body=body)
    checks.append(CheckResult("name", True, f"name: {meta['name']}"))

    if not meta.get("description"):
        checks.append(CheckResult(
            "description", False, "missing required 'description' field in frontmatter",
        ))
        return SkillValidation(path=path, checks=checks, meta=meta, body=body)
    checks.append(CheckResult("description", True, "description present"))

    return SkillValidation(path=path, checks=checks, meta=meta, body=body)


def build_skill_info(result: SkillValidation) -> SkillInfo:
    """Build a SkillInfo from a validated (ok) result. Caller ensures result.ok.

    trust_tier is left at its default and assigned by discover_skills.
    """
    meta = result.meta or {}
    skill_dir = result.path.parent
    has_native_tools = (skill_dir / "tools.py").exists()

    requires = meta.get("requires", {})
    requires_env = requires.get("env", []) if isinstance(requires, dict) else []

    allowed_tools, shell_patterns = _parse_allowed_tools(meta.get("allowed-tools", ""))

    return SkillInfo(
        name=meta["name"],
        description=meta["description"],
        location=skill_dir,
        body=result.body.strip(),
        has_native_tools=has_native_tools,
        requires_env=requires_env,
        user_invocable=meta.get("user-invocable", meta.get("user_invocable", True)),
        allowed_tools=allowed_tools,
        shell_patterns=shell_patterns,
        context=meta.get("context", "inline"),
        argument_hint=meta.get("argument-hint", ""),
        model=meta.get("model", meta.get("effort", "")),
        requires_skills=_coerce_str_list(meta.get("required-skills", [])),
        always_loaded=bool(meta.get("always-loaded", False)),
        auto_approve=bool(meta.get("auto-approve", False)),
    )


def parse_skill_md(path: Path) -> SkillInfo | None:
    """Parse a SKILL.md into a SkillInfo, or None if invalid.

    Thin wrapper over validate_skill_md → build_skill_info so the
    accept/reject decision lives in exactly one place.
    """
    result = validate_skill_md(path)
    if not result.ok:
        log.warning(f"Rejected skill {path}: {result.first_failure}")
        return None
    return build_skill_info(result)
```

- [ ] **Step 6: Run all skill-parse + validate tests**

Run:
```bash
uv run pytest tests/test_skills.py -k "parse_ or validate_ or build_skill_info_from or catalog" -q
```
Expected: PASS — existing `parse_*` tests still green (behavior unchanged), new `validate_*` tests green.

- [ ] **Step 7: Lint, typecheck**

Run:
```bash
make lint && make typecheck
```
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add src/decafclaw/skills/__init__.py tests/test_skills.py
git commit -m "refactor(skills): extract validate_skill_md as single source of truth (#595/#596)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: #595 — `refresh_skills` reports rejected skills

**Files:**
- Modify: `src/decafclaw/skills/__init__.py` (`discover_skills`, lines 258–349)
- Modify: `src/decafclaw/prompts/__init__.py` (`load_system_prompt`, lines 36–82)
- Modify: `src/decafclaw/tools/skill_tools.py` (`tool_refresh_skills`, lines 252–268)
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes (from Task 1): `validate_skill_md`, `build_skill_info`, `SkillRejection`.
- Produces:
  - `discover_skills(config, rejections: list | None = None) -> list[SkillInfo]` — appends a `SkillRejection(path=<SKILL.md path>, reason=<first_failure>)` per rejected dir when `rejections` is provided.
  - `load_system_prompt(config, rejections: list | None = None) -> tuple[str, list[SkillInfo]]` — passes `rejections` through to `discover_skills`.
  - `tool_refresh_skills` output text gains a `Rejected (found but not loaded):` section when any rejections occurred.

- [ ] **Step 1: Write failing test for the accumulator**

Add to `tests/test_skills.py`:

```python
# -- discover_skills rejection accumulator --


def test_discover_records_rejections(tmp_path, monkeypatch):
    # Point workspace skills at a dir with one valid and one malformed skill.
    ws_skills = tmp_path / "ws" / "skills"
    _write_skill(ws_skills / "good", "name: good\ndescription: Valid.")
    (ws_skills / "bad").mkdir(parents=True)
    (ws_skills / "bad" / "SKILL.md").write_text("# No frontmatter here\n")

    cfg = _config_with_workspace_skills(tmp_path, ws_skills)

    rejections = []
    skills = discover_skills(cfg, rejections=rejections)

    names = [s.name for s in skills]
    assert "good" in names
    assert len(rejections) == 1
    assert rejections[0].path.name == "SKILL.md"
    assert "bad" in str(rejections[0].path)
    assert "frontmatter" in rejections[0].reason.lower()


def test_discover_without_accumulator_is_unchanged(tmp_path):
    ws_skills = tmp_path / "ws" / "skills"
    (ws_skills / "bad").mkdir(parents=True)
    (ws_skills / "bad" / "SKILL.md").write_text("# No frontmatter\n")
    cfg = _config_with_workspace_skills(tmp_path, ws_skills)
    # No accumulator passed — must not raise, just drop the bad skill.
    skills = discover_skills(cfg)
    assert all(s.name != "bad" for s in skills)
```

Add this helper near the top of the discover-section tests (search the file for how existing `discover_skills` tests build a config; mirror that exact pattern — they construct a `Config`/`AgentConfig` pointing workspace at a tmp dir. If an existing helper already does this, reuse it instead of adding `_config_with_workspace_skills`). If no helper exists, add:

```python
def _config_with_workspace_skills(tmp_path, ws_skills_dir):
    """Build a Config whose workspace_path resolves so that
    workspace_path / 'skills' == ws_skills_dir, and whose other scan
    tiers are empty/non-existent so only the workspace skills load."""
    from decafclaw.config import AgentConfig, Config
    cfg = Config(agent=AgentConfig(data_home=str(tmp_path / "data"), id="t", user_id="u"))
    # workspace_path / "skills" is the workspace scan root; symlink/relocate
    # by writing skills directly under the resolved workspace path:
    (cfg.workspace_path / "skills").mkdir(parents=True, exist_ok=True)
    # Re-root the test fixtures under the real workspace path:
    import shutil
    shutil.copytree(ws_skills_dir, cfg.workspace_path / "skills", dirs_exist_ok=True)
    cfg.extra_skill_paths = []
    return cfg
```

> Note for implementer: check how the EXISTING `discover_skills` tests (the block around `tests/test_skills.py` discovery tests) set `workspace_path` / build their `Config`. Prefer that established pattern over the helper above; the helper is a fallback if none exists. Either way, the assertions in Step 1 are what matter.

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
uv run pytest tests/test_skills.py -k "discover_records_rejections or discover_without_accumulator" -q
```
Expected: FAIL — `discover_skills()` takes no `rejections` kwarg.

- [ ] **Step 3: Add the accumulator to `discover_skills`**

In `src/decafclaw/skills/__init__.py`, change the signature (line 258) and the per-dir parse block (lines 288–298). New signature:

```python
def discover_skills(config, rejections: list | None = None) -> list[SkillInfo]:
```

Add to the docstring (after the existing description):

```
    When `rejections` is provided, SKILL.md files that fail validation
    are appended as SkillRejection(path, reason) instead of being
    silently dropped — surfaced by refresh_skills (#595).
```

Replace the parse block:

```python
            info = parse_skill_md(skill_md)
            if info is None:
                continue
```

with:

```python
            result = validate_skill_md(skill_md)
            if not result.ok:
                log.warning(f"Rejected skill {skill_md}: {result.first_failure}")
                if rejections is not None:
                    rejections.append(
                        SkillRejection(path=skill_md, reason=result.first_failure)
                    )
                continue
            info = build_skill_info(result)
```

(The rest of the loop — `info.trust_tier = tier`, auto-approve/always-loaded handling, requires.env, collision — stays exactly as-is.)

- [ ] **Step 4: Thread `rejections` through `load_system_prompt`**

In `src/decafclaw/prompts/__init__.py`, change the signature (line 36):

```python
def load_system_prompt(config, rejections: list | None = None):
```

and the discovery call (line 82):

```python
    skills = discover_skills(config, rejections=rejections)
```

Update the docstring `Returns:` block is unchanged; add one line under it:

```
    Pass `rejections` (a list) to collect SkillRejection entries for
    skills found-but-rejected during discovery (surfaced by refresh_skills).
```

- [ ] **Step 5: Make `refresh_skills` collect and report rejections**

In `src/decafclaw/tools/skill_tools.py`, update the `from ..skills import ...` line inside `tool_refresh_skills` (line 261) to also import `SkillRejection` — actually import it where used. Replace the body from line 260 onward:

```python
    config = ctx.config
    from ..skills import build_skill_tool_owners
    rejections: list = []
    config.system_prompt, config.discovered_skills = load_system_prompt(
        config, rejections=rejections
    )
    config.skill_tool_owners = build_skill_tool_owners(config.discovered_skills)
    invalidate_skill_cache(config)
    # List all discovered skills — text-only, native-tools, and user-invocable
    # are all valid activatable skills
    names = [s.name for s in config.discovered_skills]
    text = f"Skills refreshed. Available skills: {', '.join(names) or '(none)'}"
    if rejections:
        text += "\nRejected (found but not loaded):\n" + "\n".join(
            f"  - {_rejection_display_path(config, r.path)} — {r.reason}"
            for r in rejections
        )
    return text
```

Add a module-level helper near the top of `skill_tools.py` (after `_permissions_path`, before `_load_native_tools`):

```python
def _rejection_display_path(config, path: Path) -> str:
    """Show a rejected SKILL.md path relative to a meaningful root."""
    for root in (config.workspace_path, config.agent_path):
        try:
            return str(path.relative_to(root))
        except ValueError:
            continue
    return str(path)
```

- [ ] **Step 6: Write failing test for `refresh_skills` reporting**

Add to `tests/test_skills.py` (note `tool_refresh_skills` import — add it to the existing `from decafclaw.tools.skill_tools import (...)` block):

```python
@pytest.mark.asyncio
async def test_refresh_skills_reports_rejections(ctx):
    ws_skills = ctx.config.workspace_path / "skills"
    (ws_skills / "broken").mkdir(parents=True)
    (ws_skills / "broken" / "SKILL.md").write_text("# missing frontmatter\n")

    result = await _maybe_await(tool_refresh_skills(ctx))
    text = _text(result)
    assert "Rejected (found but not loaded):" in text
    assert "broken/SKILL.md" in text
    assert "frontmatter" in text.lower()
```

`tool_refresh_skills` is sync; if the existing test file lacks a `_maybe_await`, call it directly instead:

```python
def test_refresh_skills_reports_rejections(ctx):
    ws_skills = ctx.config.workspace_path / "skills"
    (ws_skills / "broken").mkdir(parents=True)
    (ws_skills / "broken" / "SKILL.md").write_text("# missing frontmatter\n")
    text = _text(tool_refresh_skills(ctx))
    assert "Rejected (found but not loaded):" in text
    assert "broken/SKILL.md" in text
    assert "frontmatter" in text.lower()
```

Use the sync form (second version) — `tool_refresh_skills` is a plain `def`.

- [ ] **Step 7: Run the refresh + discover tests**

Run:
```bash
uv run pytest tests/test_skills.py -k "discover_records_rejections or discover_without_accumulator or refresh_skills_reports" -q
```
Expected: PASS.

- [ ] **Step 8: Full skills test file + lint/typecheck**

Run:
```bash
uv run pytest tests/test_skills.py -q && make lint && make typecheck
```
Expected: PASS / no errors. (Confirms the `load_system_prompt` signature change didn't break its other callers — they call it with one arg, which still works.)

- [ ] **Step 9: Commit**

```bash
git add src/decafclaw/skills/__init__.py src/decafclaw/prompts/__init__.py src/decafclaw/tools/skill_tools.py tests/test_skills.py
git commit -m "feat(skills): refresh_skills reports rejected skills + reason (#595)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: #596 — `skill_validate(path)` pre-flight lint tool + docs

**Files:**
- Modify: `src/decafclaw/tools/skill_tools.py` (add `inspect` import; `_lint_tools_py`, `_render_validation`, `tool_skill_validate`; register in `SKILL_TOOLS` / `SKILL_TOOL_DEFINITIONS`)
- Modify: `docs/skills.md`
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes (from Task 1): `CheckResult`, `validate_skill_md`.
- Produces:
  - `tool_skill_validate(ctx, path: str) -> ToolResult` — validates one workspace skill dir; returns a ✓/✗ checklist (text) + structured `data={"path", "ok", "checks": [...]}`.
  - `_lint_tools_py(skill_dir: Path) -> list[CheckResult]` — tools.py checks (filename, clean import, `get_tools(ctx)` signature / `TOOLS` export). Returns `[]` for a text-only skill (no tools.py, no stray `main.py`).
  - `"skill_validate"` registered at `"low"` priority.

- [ ] **Step 1: Write failing tests for `skill_validate`**

Add to `tests/test_skills.py` (import `tool_skill_validate` in the `skill_tools` import block):

```python
# -- skill_validate tool --


def _write_ws_skill(ctx, name, frontmatter, body="Body.", tools_py=None):
    """Create skills/<name>/ under the ctx workspace. tools_py is raw source."""
    d = ctx.config.workspace_path / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n{body}")
    if tools_py is not None:
        (d / "tools.py").write_text(tools_py)
    return d


def test_skill_validate_missing_skill_md(ctx):
    d = ctx.config.workspace_path / "skills" / "empty"
    d.mkdir(parents=True)
    result = tool_skill_validate(ctx, path="skills/empty")
    assert result.data["ok"] is False
    names = {c["name"] for c in result.data["checks"]}
    assert "skill_md_present" in names


def test_skill_validate_no_frontmatter(ctx):
    d = ctx.config.workspace_path / "skills" / "nofm"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("# Heading only\n")
    result = tool_skill_validate(ctx, path="skills/nofm")
    assert result.data["ok"] is False
    fm = next(c for c in result.data["checks"] if c["name"] == "frontmatter")
    assert fm["passed"] is False


def test_skill_validate_valid_text_only(ctx):
    _write_ws_skill(ctx, "texty", "name: texty\ndescription: Text only.")
    result = tool_skill_validate(ctx, path="skills/texty")
    assert result.data["ok"] is True
    # no tools.py → no tools checks
    names = {c["name"] for c in result.data["checks"]}
    assert "tools_import" not in names


def test_skill_validate_valid_with_tools(ctx):
    _write_ws_skill(
        ctx, "withtools", "name: withtools\ndescription: Has tools.",
        tools_py="def get_tools(ctx):\n    return {}, []\n",
    )
    result = tool_skill_validate(ctx, path="skills/withtools")
    assert result.data["ok"] is True
    sig = next(c for c in result.data["checks"] if c["name"] == "get_tools_signature")
    assert sig["passed"] is True


def test_skill_validate_tools_syntax_error(ctx):
    _write_ws_skill(
        ctx, "broken", "name: broken\ndescription: Bad tools.",
        tools_py="def get_tools(ctx)\n    return {}, []\n",  # missing colon
    )
    result = tool_skill_validate(ctx, path="skills/broken")
    assert result.data["ok"] is False
    imp = next(c for c in result.data["checks"] if c["name"] == "tools_import")
    assert imp["passed"] is False
    assert "SyntaxError" in imp["message"]


def test_skill_validate_tools_undefined_name(ctx):
    _write_ws_skill(
        ctx, "phantom", "name: phantom\ndescription: Phantom api.",
        tools_py="TOOLS = {'x': default_api.shell}\n",  # NameError at import
    )
    result = tool_skill_validate(ctx, path="skills/phantom")
    assert result.data["ok"] is False
    imp = next(c for c in result.data["checks"] if c["name"] == "tools_import")
    assert imp["passed"] is False
    assert "NameError" in imp["message"]


def test_skill_validate_get_tools_no_ctx(ctx):
    _write_ws_skill(
        ctx, "noctx", "name: noctx\ndescription: Bad signature.",
        tools_py="def get_tools():\n    return {}, []\n",  # missing ctx
    )
    result = tool_skill_validate(ctx, path="skills/noctx")
    assert result.data["ok"] is False
    sig = next(c for c in result.data["checks"] if c["name"] == "get_tools_signature")
    assert sig["passed"] is False


def test_skill_validate_stray_main_py(ctx):
    d = _write_ws_skill(ctx, "mislabeled", "name: mislabeled\ndescription: Wrong file.")
    (d / "main.py").write_text("def get_tools(ctx):\n    return {}, []\n")
    result = tool_skill_validate(ctx, path="skills/mislabeled")
    fn = next(c for c in result.data["checks"] if c["name"] == "tools_filename")
    assert fn["passed"] is False
    assert "main.py" in fn["message"]


def test_skill_validate_outside_workspace(ctx):
    result = tool_skill_validate(ctx, path="../../etc")
    assert "outside the workspace" in _text(result)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
uv run pytest tests/test_skills.py -k "skill_validate" -q
```
Expected: FAIL — `tool_skill_validate` not importable.

- [ ] **Step 3: Add `inspect` import and the helpers**

In `src/decafclaw/tools/skill_tools.py`, add `import inspect` to the top stdlib imports (alphabetical: after `import importlib.util`). Update the existing `from ..media import ToolResult` area to also import the skills symbols at module level:

```python
from ..skills import CheckResult, validate_skill_md
```

(This is a module-level stdlib/intra-package import — `skill_tools` does not create a cycle with `skills/__init__.py`, which imports nothing from `tools`. Verify with `make lint` in Step 6.)

Add the helpers (place them after `_rejection_display_path` from Task 2):

```python
def _lint_tools_py(skill_dir: Path) -> list[CheckResult]:
    """tools.py-specific checks for skill_validate.

    Returns [] for a text-only skill (no tools.py and no stray entrypoint).
    Imports tools.py to surface SyntaxError / NameError / ImportError —
    the same exec_module path activation uses — and introspects (does NOT
    call) get_tools' signature.
    """
    checks: list[CheckResult] = []
    tools_py = skill_dir / "tools.py"

    if not tools_py.exists():
        stray = skill_dir / "main.py"
        if stray.exists():
            checks.append(CheckResult(
                "tools_filename", False,
                "found main.py — native tools must live in 'tools.py'; rename it",
            ))
        return checks

    checks.append(CheckResult("tools_filename", True, "tools.py present"))

    try:
        spec = importlib.util.spec_from_file_location(
            f"decafclaw_skill_validate_{skill_dir.name}", tools_py
        )
        if spec is None or spec.loader is None:
            checks.append(CheckResult(
                "tools_import", False, "could not create an import spec for tools.py",
            ))
            return checks
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:
        checks.append(CheckResult(
            "tools_import", False,
            f"tools.py failed to import: {type(exc).__name__}: {exc}",
        ))
        return checks

    checks.append(CheckResult("tools_import", True, "tools.py imports cleanly"))

    get_tools = getattr(module, "get_tools", None)
    has_static = hasattr(module, "TOOLS") or hasattr(module, "TOOL_DEFINITIONS")
    if get_tools is not None:
        try:
            params = list(inspect.signature(get_tools).parameters.values())
            accepts_ctx = any(
                p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL)
                for p in params
            )
        except (TypeError, ValueError) as exc:
            checks.append(CheckResult(
                "get_tools_signature", False,
                f"could not inspect get_tools signature: {exc}",
            ))
            return checks
        if accepts_ctx:
            checks.append(CheckResult(
                "get_tools_signature", True, "get_tools(ctx) accepts a ctx parameter",
            ))
        else:
            checks.append(CheckResult(
                "get_tools_signature", False,
                "get_tools must accept ctx as its first parameter: "
                "def get_tools(ctx) -> (dict, list)",
            ))
    elif has_static:
        checks.append(CheckResult(
            "tools_exports", True, "exports TOOLS / TOOL_DEFINITIONS",
        ))
    else:
        checks.append(CheckResult(
            "tools_exports", False,
            "tools.py exports neither get_tools(ctx) nor TOOLS / TOOL_DEFINITIONS",
        ))
    return checks


def _render_validation(path: str, checks: list[CheckResult]) -> ToolResult:
    """Render a checklist of CheckResults as a ToolResult (text + data)."""
    ok = all(c.passed for c in checks)
    header = "PASS" if ok else "FAIL"
    lines = [f"skill_validate '{path}': {header}", ""]
    for c in checks:
        lines.append(f"  {'[x]' if c.passed else '[ ]'} {c.name}: {c.message}")
    if not ok:
        lines.append("")
        lines.append(
            "Fix the unchecked items, then run skill_validate again "
            "(or refresh_skills to load it)."
        )
    return ToolResult(
        text="\n".join(lines),
        data={
            "path": path,
            "ok": ok,
            "checks": [
                {"name": c.name, "passed": c.passed, "message": c.message}
                for c in checks
            ],
        },
    )
```

- [ ] **Step 4: Add the tool function**

Add after the helpers (before `tool_refresh_skills` is fine):

```python
def tool_skill_validate(ctx, path: str) -> ToolResult:
    """Pre-flight validate a single workspace skill directory."""
    log.info(f"[tool:skill_validate] {path}")
    workspace = ctx.config.workspace_path.resolve()
    target = (workspace / path).resolve()
    if not target.is_relative_to(workspace):
        return ToolResult(text=f"[error: path '{path}' is outside the workspace]")

    skill_dir = target.parent if target.name == "SKILL.md" else target
    if not skill_dir.is_dir():
        return ToolResult(
            text=f"[error: '{path}' is not a directory in the workspace]"
        )

    checks: list[CheckResult] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        checks.append(CheckResult(
            "skill_md_present", False,
            "no SKILL.md here — a skill needs skills/<name>/SKILL.md",
        ))
        return _render_validation(path, checks)
    checks.append(CheckResult("skill_md_present", True, "SKILL.md present"))

    # Discovery-level checks — shared source of truth with refresh_skills.
    checks.extend(validate_skill_md(skill_md).checks)
    # tools.py checks run regardless of frontmatter validity (filesystem-based).
    checks.extend(_lint_tools_py(skill_dir))

    return _render_validation(path, checks)
```

- [ ] **Step 5: Register the tool**

In the `SKILL_TOOLS` dict (line 271–274) add the entry:

```python
SKILL_TOOLS = {
    "activate_skill": tool_activate_skill,
    "refresh_skills": tool_refresh_skills,
    "skill_validate": tool_skill_validate,
}
```

Append to `SKILL_TOOL_DEFINITIONS` (after the `refresh_skills` def, before the closing `]`):

```python
    {
        "type": "function",
        "priority": "low",
        "function": {
            "name": "skill_validate",
            "description": (
                "Validate a workspace skill directory BEFORE it loads, and get the "
                "specific reasons it would be rejected. Checks SKILL.md frontmatter "
                "(must have name + description), that native tools live in tools.py "
                "(NOT main.py), that tools.py imports without error, and that it "
                "exports get_tools(ctx) or TOOLS/TOOL_DEFINITIONS. Use this when a "
                "skill you authored isn't appearing, or before refresh_skills, "
                "instead of guessing. Takes a workspace-relative path like "
                "'skills/my-skill'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Workspace-relative path to the skill directory "
                            "(or its SKILL.md), e.g. 'skills/my-skill'."
                        ),
                    },
                },
                "required": ["path"],
            },
        },
    },
```

- [ ] **Step 6: Run skill_validate tests + lint/typecheck**

Run:
```bash
uv run pytest tests/test_skills.py -k "skill_validate" -q && make lint && make typecheck
```
Expected: PASS / no errors.

- [ ] **Step 7: Update `docs/skills.md`**

Open `docs/skills.md`, find the section covering skill authoring / discovery / the `refresh_skills` tool. Add (adapt heading levels to the file's existing structure):

```markdown
### Validating a skill before it loads

When a skill you authored doesn't appear, it was almost certainly **rejected
during discovery** — the loader requires a `SKILL.md` that starts with a `---`
YAML frontmatter block containing both `name` and `description`. Two tools
surface the reason instead of failing silently:

- **`refresh_skills`** re-scans every skill directory and now lists any
  found-but-rejected skills under `Rejected (found but not loaded):`, each with
  the reason (e.g. *no valid YAML frontmatter*).
- **`skill_validate('skills/<name>')`** is the pre-flight, single-skill check.
  It reports a pass/fail checklist:
  - `SKILL.md` present
  - valid `---` frontmatter with `name` + `description`
  - native tools live in **`tools.py`** (not `main.py` or another name)
  - `tools.py` imports cleanly (catches `SyntaxError`, undefined names, bad imports)
  - `tools.py` exports `get_tools(ctx)` (must accept `ctx`) **or**
    `TOOLS` / `TOOL_DEFINITIONS`

Minimal correct workspace skill:

​```
skills/<name>/
  SKILL.md      # --- frontmatter (name + description) --- then markdown body
  tools.py      # def get_tools(ctx) -> (dict, list): ...   (optional)
​```
```

(Remove the zero-width space before the triple backticks — it's only there to keep this plan's code fence intact.)

- [ ] **Step 8: Commit**

```bash
git add src/decafclaw/tools/skill_tools.py tests/test_skills.py docs/skills.md
git commit -m "feat(skills): add skill_validate pre-flight lint tool (#596)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: tool_choice eval case for `skill_validate`

**Files:**
- Modify: `evals/tool_choice/core_overlaps.yaml`

**Interfaces:**
- Consumes (from Task 3): the registered `skill_validate` tool (present in the eval loadout because `SKILL_TOOL_DEFINITIONS` is part of `decafclaw.tools.TOOL_DEFINITIONS`, and the loadout ignores deferral).

- [ ] **Step 1: Add the disambiguation case**

Append to `evals/tool_choice/core_overlaps.yaml`:

```yaml
# -- skill_validate vs refresh_skills -----------------------------------------

- name: skill-validate-vs-refresh-preflight
  scenario: "I just finished writing a new skill at skills/meta-ingest. Before I reload anything, check that I set it up correctly."
  expected: skill_validate
  near_miss: [refresh_skills]
  notes: |
    skill_validate is the targeted, non-mutating pre-flight for ONE skill —
    "check it's set up correctly" before reloading. refresh_skills re-scans
    everything and mutates the live catalog; it reports rejections too, but
    it's the broad/commit path, not the focused "did I author this right" check.
```

- [ ] **Step 2: Run the tool-choice eval**

> This makes real LLM calls (~30s). If credentials/proxy aren't configured in this worktree's `.env`, skip the run and note it for Les to run; the YAML case still ships.

Run:
```bash
make eval-tools
```
Expected: the `skill-validate-vs-refresh-preflight` case resolves to `skill_validate` (no overlap reported). If the model picks `refresh_skills`, sharpen the `skill_validate` description's pre-flight framing (it's a control surface) and re-run.

- [ ] **Step 3: Commit**

```bash
git add evals/tool_choice/core_overlaps.yaml
git commit -m "test(evals): tool_choice case for skill_validate vs refresh_skills (#596)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification (before PR)

- [ ] `make check` green (lint + typecheck + JS + message-types drift).
- [ ] `uv run pytest tests/test_skills.py -q` green; then full `make test` green.
- [ ] `uv run pytest tests/test_skills.py --durations=10` — no new test in the slow tail (no accidental real-scheduler/sleep).
- [ ] Re-read `docs/skills.md` change renders correctly (frontmatter fence intact, no stray zero-width space).
- [ ] Rebase on latest `origin/main` before the squash (main advances during long sessions).

## Self-Review (completed during planning)

- **Spec coverage:** #595 → Task 2. #596 → Task 3. Single-source-of-truth refactor → Task 1. tool_choice eval → Task 4. Docs → Task 3 Step 7. Unit tests → Tasks 1–3. All acceptance criteria mapped.
- **Placeholders:** none — every code step shows full code; the one "mirror the existing test config pattern" note has a concrete fallback helper.
- **Type consistency:** `validate_skill_md`, `build_skill_info`, `SkillValidation(.ok/.first_failure)`, `SkillRejection(path, reason)`, `CheckResult(name, passed, message)`, `_lint_tools_py`, `_render_validation`, `tool_skill_validate` used consistently across tasks; `discover_skills`/`load_system_prompt` accumulator param named `rejections` everywhere.
