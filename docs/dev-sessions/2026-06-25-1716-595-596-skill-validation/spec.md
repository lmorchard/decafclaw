# Spec: Skill validation reporting (#595) + `skill_validate` lint (#596)

## Background

Both issues come from the same postmortem (conversation `web-lmorchard-fa5ec853`,
Caffeina / vertex-gemini-pro). The agent authored `skills/meta-ingest/SKILL.md` with
**no YAML frontmatter**, the skill was silently rejected on every `refresh_skills`, and
the agent burned ~150 messages thrashing on unrelated files before giving up. A single
rejection reason in the tool result would have ended the session at the first refresh.

- **#595** — reactive: `refresh_skills` should report skills that were found-but-rejected
  during discovery, and why. Rejections are currently log-only and silently dropped.
- **#596** — proactive: a standalone `skill_validate(path)` tool that pre-flight-validates
  one workspace skill directory before refresh, catching authoring mistakes with
  actionable messages.

## Core risk this design guards against

The rejection reasons (#595) and the lint checks (#596) must not **drift** away from
`parse_skill_md`'s actual accept/reject decision — drift is the exact failure the
postmortem warns about. The design keeps **one source of truth** for the discovery-level
accept/reject decision.

## Goals

1. `refresh_skills` lists rejected skills with an actionable reason.
2. A `skill_validate(path)` tool validates a single skill directory deeply (including
   importing `tools.py`) and returns a pass/fail checklist.
3. Discovery-level validation logic lives in exactly one place, consumed by all three
   callers (`parse_skill_md`, `discover_skills`/`refresh_skills`, `skill_validate`).

## Non-goals (explicitly out of scope)

- **#597** — surfacing the authoring contract in-context at skill-creation time.
- **#598** — agent diagnostic-discipline guardrails (loop-breaker, verify-tool-fired,
  apology spiral) and the "agent pivots to diagnosis" behavior eval.
- Calling `get_tools(ctx)` at runtime during validation (needs a real `ctx`, may have
  side effects). We introspect its signature only.

## Design

### 1. Shared validation core — `skills/__init__.py` (single source of truth)

New dataclasses:

```python
@dataclass
class CheckResult:
    name: str       # "readable" | "frontmatter" | "name" | "description"
    passed: bool
    message: str    # actionable on failure

@dataclass
class SkillValidation:
    path: Path
    checks: list[CheckResult]
    meta: dict | None = None   # parsed frontmatter (reused, not re-parsed)
    body: str = ""
    @property
    def ok(self) -> bool: ...            # all checks passed
    @property
    def first_failure(self) -> str | None: ...   # message of first failed check

@dataclass
class SkillRejection:
    path: Path
    reason: str
```

New / refactored functions:

```python
def validate_skill_md(path: Path) -> SkillValidation:
    """Run the discovery-level checks; THE accept/reject decision.
    Checks are sequential/short-circuiting (can't check name without frontmatter):
      1. readable     — file opens (maps to the current OSError branch)
      2. frontmatter  — valid `---` YAML frontmatter present
      3. name         — non-empty `name`
      4. description  — non-empty `description`
    Stops at the first failure; parses frontmatter once into `meta`/`body`."""

def build_skill_info(result: SkillValidation) -> SkillInfo:
    """Build a SkillInfo from a validated (ok) result, reusing result.meta/body.
    Sets base fields incl. location; trust_tier is assigned by discover_skills."""

def parse_skill_md(path: Path) -> SkillInfo | None:
    """Back-compat wrapper: validate → build. Behavior unchanged for existing callers
    (valid → SkillInfo, invalid → None)."""
```

`parse_skill_md`'s observable behavior is unchanged, so existing call sites and tests
that only assert valid→`SkillInfo` / invalid→`None` keep working; its internals now route
through `validate_skill_md`.

### 2. #595 — `refresh_skills` reports rejections

`discover_skills` calls `validate_skill_md` directly per skill dir:

```python
result = validate_skill_md(skill_md)
if not result.ok:
    if rejections is not None:
        rejections.append(SkillRejection(path=skill_md, reason=result.first_failure))
    continue
info = build_skill_info(result)
# ... existing trust_tier / requires.env handling unchanged
```

Rejections surface via an **optional accumulator param** (plumbing only — the validation
logic is already unified, so this is orthogonal to the drift concern):

```python
def discover_skills(config, rejections: list | None = None) -> list[SkillInfo]: ...
def load_system_prompt(config, rejections: list | None = None) -> tuple[str, list]: ...
```

`refresh_skills`:

```python
rejections: list[SkillRejection] = []
config.system_prompt, config.discovered_skills = load_system_prompt(config, rejections=rejections)
...
text = f"Skills refreshed. Available skills: {', '.join(names) or '(none)'}"
if rejections:
    text += "\nRejected (found but not loaded):\n" + "\n".join(
        f"  - {_relpath(r.path)} — {r.reason}" for r in rejections
    )
return text
```

Existing callers of `discover_skills` / `load_system_prompt` pass nothing and are
unaffected → no return-shape churn across the ~dozen `discover_skills` tests.

Rejection paths are shown relative to a meaningful root (workspace/agent root) so the
message points at the file the author just wrote.

### 3. #596 — `skill_validate(path)` tool — `skill_tools.py`

Signature: `async/def tool_skill_validate(ctx, path: str) -> ToolResult`.

- **Path resolution:** resolve `path` under the workspace (via the workspace path
  chokepoint) so the tool is sandboxed to the agent-writable tier; accept either a skill
  directory or a `SKILL.md` within it.
- **Checks** (pass/fail checklist):
  1. Path resolves and `SKILL.md` present.
  2. Discovery-level checks — **reuse `validate_skill_md`** (frontmatter / name / description).
  3. tools.py checks — only when a `tools.py` exists or frontmatter declares native tools:
     - filename is `tools.py` (warn if a stray `main.py`/other `.py` looks like a
       misnamed entrypoint — the incident used `main.py`).
     - **imports cleanly** — reuse `_load_native_tools`, catching `SyntaxError`,
       `NameError` (e.g. the `default_api.shell` reference), `ImportError`, any `Exception`;
       report the exception type + message.
     - exports `get_tools` whose signature accepts a `ctx` positional param (via
       `inspect.signature`, **not** called) — OR `TOOLS` / `TOOL_DEFINITIONS` present.
       (The incident wrote `get_tools()` with no params.)
  4. `allowed-tools` / `requires.env` parse — reported as info (non-fatal, matching
     `parse_skill_md`'s non-fatal warnings).
- **Return:** `ToolResult` rendering a ✓/✗ checklist with actionable messages and an
  overall pass/fail summary; structured `data` carries the list of checks.
- **Registration:** `SKILL_TOOLS` + `SKILL_TOOL_DEFINITIONS`, priority `"low"` (deferred,
  matching `refresh_skills`; #597 will surface it at authoring time later).

### 4. Tests & evals

**Unit (`tests/test_skills.py`, plus tool tests where skill tools are covered):**
- `validate_skill_md`: each branch (unreadable, no frontmatter, missing name, missing
  description, valid) → expected failing/all-passing checks + messages.
- `parse_skill_md`: behavior unchanged (valid → `SkillInfo`; each invalid → `None`).
- `discover_skills`: with a `rejections` accumulator, a malformed workspace skill is
  recorded with the right reason while valid skills are still discovered; without the
  accumulator, behavior unchanged.
- `skill_validate`: missing `SKILL.md`; bad frontmatter; missing name/description;
  `tools.py` with `SyntaxError`; `tools.py` with zero-arg `get_tools`; `tools.py`
  referencing an undefined name (`default_api`); valid skill with proper `tools.py`;
  valid text-only skill (tools checks skipped).

**Eval:**
- One `tool_choice` case in `evals/tool_choice/` for `skill_validate` disambiguation
  (convention: new/sharpened tool description → new tool_choice case; ~30s).
- The "agent pivots to diagnosis on an unloadable skill" behavior eval is deferred to #598.

### 5. Docs

- `docs/skills.md`: document `refresh_skills` rejection reporting and the `skill_validate`
  contract checklist (frontmatter shape, `tools.py` filename, `get_tools(ctx)` signature).

## Acceptance criteria

- [ ] `refresh_skills` output lists rejected skills with an actionable reason.
- [ ] One `validate_skill_md` is the sole discovery-level accept/reject decision; `parse_skill_md`
      and `discover_skills` both route through it.
- [ ] `skill_validate(path)` returns a pass/fail checklist covering frontmatter, name,
      description, `tools.py` filename, clean import, and `get_tools`/`TOOLS` export.
- [ ] `skill_validate` catches `SyntaxError`, undefined-name (`default_api`), and a
      zero-arg `get_tools` from the original incident.
- [ ] New tool registered and discoverable; `tool_choice` eval case added.
- [ ] `docs/skills.md` updated.
- [ ] `make check` + `make test` green.
