# Schedule Frontmatter Editing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every `ScheduleTask` frontmatter field viewable and editable from the web UI's schedules page, and make it fail a test if a future field is added without being wired through.

**Architecture:** Server-side, close the drift between four hand-maintained field lists and add a test that iterates `dataclasses.fields(ScheduleTask)` so they cannot drift again. Client-side, follow the existing `wiki-metadata` pattern — a presentational panel that emits change events while the host owns every PUT — with the chip control extracted for shared use.

**Tech Stack:** Python 3 / Starlette / dataclasses / PyYAML / croniter; Lit 3 web components (light DOM), vitest.

**Spec:** [`spec.md`](./spec.md)

## Global Constraints

- Worktree: `.claude/worktrees/schedule-frontmatter-ui`, branch `schedule-frontmatter-ui`, `HTTP_PORT=18897`. Run every command with an **absolute** `cd` to that worktree — a stray `cd` to the main clone makes tests pass in the wrong tree.
- Python venv is `.venv` created by `uv sync`. Run tests via `make test` or `.venv/bin/python -m pytest`. Single-file runs need `-n0` (xdist is on by default in `pyproject.toml` and rejects `-p no:xdist`).
- JS tests: `make test-js` (`npx vitest run` from `src/decafclaw/web/static`).
- `make check` must be green before every commit (ruff + pyright + tsc + message-type drift).
- Never enumerate dataclass fields by hand in tests — iterate `dataclasses.fields()`. This plan's whole point is that hand-lists rot.
- Web components use `createRenderRoot() { return this; }` (light DOM). Component CSS goes in `styles/<name>.css` and must be added to `style.css` as an `@import`.
- Tag-qualify custom button rules (`button.foo`, not `.foo`) — Pico's `button:not(...)` is 0,1,1 and beats a bare class.
- Commit after each task. Do not push until the whole plan is done and `make check` + `make test` + `make test-js` are green.

## File Structure

| File | Responsibility |
|---|---|
| `src/decafclaw/skills/__init__.py` | Add `_frontmatter_span` / `_extract_frontmatter_text`; refactor `_split_frontmatter` onto the shared span helper |
| `src/decafclaw/schedules.py` | `unknown_keys` field, known-key constant, parse capture, two new `write_overlay` patch keys |
| `src/decafclaw/http_server.py` | `_schedule_to_dict` completeness, `_read_frontmatter_raw`, `models_list` handler + route |
| `tests/test_schedule_wire_drift.py` | **New.** The drift guard — wire completeness and patchability, both derived from `dataclasses.fields` |
| `tests/test_schedules.py` | Unknown-key capture and round-trip tests |
| `tests/test_web_schedules_api.py` | `GET /api/models` |
| `src/decafclaw/web/static/components/chip-list.js` | **New.** Shared chip input |
| `src/decafclaw/web/static/components/chip-list.test.js` | **New.** |
| `src/decafclaw/web/static/components/wiki-metadata.js` | Refactor its two chip fields onto `<chip-list>` |
| `src/decafclaw/web/static/components/schedule-metadata.js` | **New.** The schedules metadata panel |
| `src/decafclaw/web/static/components/schedule-metadata.test.js` | **New.** |
| `src/decafclaw/web/static/components/schedule-page.js` | Host the panel; drop the three inline controls |
| `src/decafclaw/web/static/components/schedule-page.test.js` | **New.** Panel wiring, model-list fetch, 400 surfacing |
| `src/decafclaw/web/static/styles/chip-list.css` | **New.** |
| `src/decafclaw/web/static/styles/schedule-metadata.css` | **New.** |
| `src/decafclaw/web/static/styles/wiki-metadata.css` | Remove the chip rules that moved |
| `src/decafclaw/web/static/style.css` | Two new `@import` lines |
| `docs/schedules.md`, `docs/web-ui.md` | Documentation |

---

### Task 1: Capture unrecognized frontmatter keys

**Files:**
- Modify: `src/decafclaw/skills/__init__.py:252-279` (`_split_frontmatter`)
- Modify: `src/decafclaw/schedules.py:32-52` (`ScheduleTask`), `55-111` (`parse_schedule_file`)
- Test: `tests/test_schedules.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `skills._frontmatter_span(text: str) -> tuple[str, str] | None` — `(frontmatter_text, body_text)` or `None`.
  - `skills._extract_frontmatter_text(text: str) -> str` — raw YAML block without `---` delimiters, `""` when absent.
  - `ScheduleTask.unknown_keys: list[str]` — sorted, populated by `parse_schedule_file`, never serialized.
  - `schedules._KNOWN_FRONTMATTER_KEYS: frozenset[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_schedules.py`:

```python
class TestUnknownFrontmatterKeys:
    """#729 one layer up: a key the parser ignores must not vanish silently."""

    def _write(self, tmp_path, text):
        path = tmp_path / "task.md"
        path.write_text(text)
        return path

    def test_unrecognized_keys_are_captured_sorted(self, tmp_path):
        path = self._write(tmp_path, (
            "---\n"
            'schedule: "0 3 * * *"\n'
            "modle: vertex-gemini-pro\n"
            "efort: strong\n"
            "---\n\n"
            "Do the thing.\n"
        ))
        task = parse_schedule_file(path)
        assert task is not None
        assert task.unknown_keys == ["efort", "modle"]

    def test_recognized_keys_are_never_flagged(self, tmp_path):
        path = self._write(tmp_path, (
            "---\n"
            'schedule: "0 3 * * *"\n'
            "enabled: false\n"
            "channel: ops\n"
            "model: gemini-flash\n"
            "effort: strong\n"
            "pre_script: scripts/x.py\n"
            "allowed-tools: vault_read\n"
            "required-skills:\n  - vault\n"
            "email-recipients:\n  - a@example.com\n"
            "---\n\n"
            "Body.\n"
        ))
        task = parse_schedule_file(path)
        assert task is not None
        assert task.unknown_keys == []

    def test_unknown_keys_are_not_written_back(self, tmp_path):
        """serialize_to_markdown must not resurrect keys nothing reads."""
        path = self._write(tmp_path, (
            "---\n"
            'schedule: "0 3 * * *"\n'
            "modle: pro\n"
            "---\n\n"
            "Body.\n"
        ))
        task = parse_schedule_file(path)
        assert task is not None
        assert "modle" not in serialize_to_markdown(task)


class TestExtractFrontmatterText:
    def test_returns_the_raw_block_without_delimiters(self):
        from decafclaw.skills import _extract_frontmatter_text
        raw = _extract_frontmatter_text('---\na: 1\nb: 2\n---\n\nBody.\n')
        assert raw == "a: 1\nb: 2"

    def test_returns_empty_when_absent(self):
        from decafclaw.skills import _extract_frontmatter_text
        assert _extract_frontmatter_text("No frontmatter here.\n") == ""

    def test_returns_empty_when_unterminated(self):
        from decafclaw.skills import _extract_frontmatter_text
        assert _extract_frontmatter_text("---\na: 1\n") == ""
```

Add `serialize_to_markdown` to the existing `from decafclaw.schedules import (...)` block at the top of the file if it is not already imported.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui
.venv/bin/python -m pytest tests/test_schedules.py -q -n0 -k "UnknownFrontmatter or ExtractFrontmatter"
```

Expected: FAIL — `ImportError: cannot import name '_extract_frontmatter_text'` and `AttributeError: 'ScheduleTask' object has no attribute 'unknown_keys'`.

- [ ] **Step 3: Add the span helpers**

In `src/decafclaw/skills/__init__.py`, replace `_split_frontmatter` with:

```python
def _frontmatter_span(text: str) -> tuple[str, str] | None:
    """Locate the frontmatter block. Returns (frontmatter_text, body_text).

    Returns None when the text has no opening ``---`` or no closing
    delimiter. Sole owner of the delimiter arithmetic so the parsed and
    raw views cannot disagree about where the block ends.
    """
    stripped = text.strip()
    if not stripped.startswith("---"):
        return None
    end = stripped.find("\n---", 3)
    if end == -1:
        return None
    return stripped[3:end].strip(), stripped[end + 4:]


def _extract_frontmatter_text(text: str) -> str:
    """The raw YAML frontmatter block, without the ``---`` delimiters.

    Empty string when there is no well-formed frontmatter. Used to show
    what is actually on disk, including keys the parser ignores.
    """
    span = _frontmatter_span(text)
    return span[0] if span else ""


def _split_frontmatter(text: str) -> tuple[dict | None, str]:
    """Split YAML frontmatter from markdown body.

    Returns (parsed_dict, body_str). parsed_dict is None
    if no valid frontmatter delimiters found or YAML is invalid.
    """
    span = _frontmatter_span(text)
    if span is None:
        return None, text
    frontmatter_str, body = span

    try:
        parsed = yaml.safe_load(frontmatter_str)
    except yaml.YAMLError as e:
        log.warning(f"Unparseable YAML frontmatter: {e}")
        return None, text

    if not isinstance(parsed, dict):
        return None, text

    return parsed, body
```

- [ ] **Step 4: Add the field and the capture**

In `src/decafclaw/schedules.py`, add to `ScheduleTask` after `pre_script`:

```python
    # Frontmatter keys the parser does not recognize. Diagnostic only —
    # never serialized back, never patchable. Exists so a typo'd key is
    # visible in the UI instead of being silently dropped (#729).
    unknown_keys: list[str] = field(default_factory=list)
```

Add above `parse_schedule_file`:

```python
# Frontmatter keys `parse_schedule_file` understands. Anything else lands
# in `ScheduleTask.unknown_keys`. `effort` is the legacy alias for `model`.
_KNOWN_FRONTMATTER_KEYS = frozenset({
    "schedule", "enabled", "channel", "model", "effort",
    "allowed-tools", "pre_script", "required-skills", "email-recipients",
})
```

In `parse_schedule_file`, immediately before the `return ScheduleTask(...)`:

```python
    unknown_keys = sorted(set(meta) - _KNOWN_FRONTMATTER_KEYS)
    if unknown_keys:
        log.warning("Unrecognized frontmatter in %s: %s (ignored)",
                    path.name, ", ".join(unknown_keys))
```

and add to the constructor call, after `email_recipients=...`:

```python
        unknown_keys=unknown_keys,
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui
.venv/bin/python -m pytest tests/test_schedules.py tests/test_skills.py -q -n0
```

Expected: PASS. `test_skills.py` is included because `_split_frontmatter` was refactored and skills parsing depends on it.

- [ ] **Step 6: Full check and commit**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui
make check && make test
git add src/decafclaw/skills/__init__.py src/decafclaw/schedules.py tests/test_schedules.py
git commit -m "feat(schedules): capture unrecognized frontmatter keys

parse_schedule_file now records any frontmatter key it does not
understand in ScheduleTask.unknown_keys and logs it, instead of
discarding it silently. Diagnostic only: never serialized back.

Extracts _frontmatter_span as the sole owner of the delimiter
arithmetic so the parsed and raw views cannot disagree."
```

---

### Task 2: Make `shell_patterns` and `email_recipients` patchable

**Files:**
- Modify: `src/decafclaw/schedules.py:233-296` (`write_overlay`)
- Test: `tests/test_schedules.py`

**Interfaces:**
- Consumes: `ScheduleTask.unknown_keys` from Task 1 (present but untouched here).
- Produces: `write_overlay(config, name, patch)` accepts `shell_patterns: list[str]` and `email_recipients: list[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_schedules.py`:

```python
class TestWriteOverlayListFields:
    """shell_patterns / email_recipients were unreachable through the API."""

    def _seed(self, config):
        path = config.workspace_path / "schedules" / "seeded.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\n"
            'schedule: "0 3 * * *"\n'
            "allowed-tools: vault_read, shell(echo hi)\n"
            "---\n\n"
            "Body.\n"
        )
        return path

    def test_shell_patterns_round_trip(self, config):
        self._seed(config)
        write_overlay(config, "seeded", {"shell_patterns": ["curl *"]})
        task = {t.name: t for t in discover_schedules(config)}["seeded"]
        assert task.shell_patterns == ["curl *"]
        # Patching one must not clear the other.
        assert task.allowed_tools == ["vault_read"]

    def test_email_recipients_round_trip(self, config):
        self._seed(config)
        write_overlay(config, "seeded", {"email_recipients": ["a@example.com"]})
        task = {t.name: t for t in discover_schedules(config)}["seeded"]
        assert task.email_recipients == ["a@example.com"]

    def test_allowed_tools_patch_preserves_shell_patterns(self, config):
        self._seed(config)
        write_overlay(config, "seeded", {"allowed_tools": ["vault_write"]})
        task = {t.name: t for t in discover_schedules(config)}["seeded"]
        assert task.allowed_tools == ["vault_write"]
        assert task.shell_patterns == ["echo hi"]

    def test_non_list_shell_patterns_rejected(self, config):
        self._seed(config)
        with pytest.raises(ValueError, match="shell_patterns must be a list"):
            write_overlay(config, "seeded", {"shell_patterns": "curl *"})

    def test_non_list_email_recipients_rejected(self, config):
        self._seed(config)
        with pytest.raises(ValueError, match="email_recipients must be a list"):
            write_overlay(config, "seeded", {"email_recipients": "a@example.com"})
```

Ensure `write_overlay` and `discover_schedules` are in the module's import block.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui
.venv/bin/python -m pytest tests/test_schedules.py -q -n0 -k WriteOverlayListFields
```

Expected: FAIL — patches are ignored, so `task.shell_patterns == ["echo hi"]` not `["curl *"]`; the two rejection tests fail with `DID NOT RAISE`.

- [ ] **Step 3: Extend the patch handling**

In `write_overlay`, change the list-validation loop to cover all four:

```python
    # Validate list fields — reject non-list values (e.g. comma-separated strings)
    # rather than silently iterating characters.
    for list_field in ("allowed_tools", "required_skills",
                       "shell_patterns", "email_recipients"):
        if list_field in patch and not isinstance(patch[list_field], list):
            raise ValueError(f"{list_field} must be a list of strings")
```

and add two lines to the `replace(...)` call, after `required_skills=...`:

```python
        shell_patterns=list(patch.get("shell_patterns", base.shell_patterns)),
        email_recipients=list(patch.get("email_recipients", base.email_recipients)),
```

Also update the docstring's patch-key list:

```
    Patch keys (all optional): enabled (bool), schedule (str), body (str),
    channel (str), allowed_tools (list[str]), required_skills (list[str]),
    shell_patterns (list[str]), email_recipients (list[str]), model (str),
    pre_script (str).
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui
.venv/bin/python -m pytest tests/test_schedules.py -q -n0
```

Expected: PASS.

- [ ] **Step 5: Full check and commit**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui
make check && make test
git add src/decafclaw/schedules.py tests/test_schedules.py
git commit -m "feat(schedules): make shell_patterns and email_recipients patchable

Both were on the dataclass and written by serialize_to_markdown but
absent from write_overlay's patch handling, so no API caller could
set them. Patching allowed_tools continues to leave shell_patterns
intact, and vice versa."
```

---

### Task 3: Wire completeness and the drift guard

**Files:**
- Modify: `src/decafclaw/http_server.py:2056-2077` (`_schedule_to_dict`)
- Create: `tests/test_schedule_wire_drift.py`

**Interfaces:**
- Consumes: `ScheduleTask.unknown_keys` (Task 1); `write_overlay` patch keys (Task 2).
- Produces: `_schedule_to_dict` returns keys `shell_patterns`, `email_recipients`, `pre_script`, `unknown_keys`, `frontmatter_raw` in addition to the existing ones. `http_server._read_frontmatter_raw(task) -> str`.

- [ ] **Step 1: Write the failing drift guard**

Create `tests/test_schedule_wire_drift.py`:

```python
"""The guard that keeps four hand-maintained field lists honest.

`ScheduleTask`, `serialize_to_markdown`, `write_overlay`'s patch handling
and `_schedule_to_dict` are four separate enumerations of the same field
set. They had drifted apart at four different points before #729's
follow-on work. These tests derive their expectations from
`dataclasses.fields(ScheduleTask)`, so adding a field fails here until it
is either wired through or explicitly exempted.
"""

from dataclasses import fields
from pathlib import Path

import pytest

from decafclaw.http_server import _schedule_to_dict
from decafclaw.schedules import ScheduleTask, discover_schedules, write_overlay

# Fields `_schedule_to_dict` renames on the wire. Renaming is fine;
# dropping is not, so these are mapped rather than exempted.
WIRE_RENAMES = {"source": "source_tier", "path": "source_path"}

# Not patchable: identity and provenance (the file's location is not
# user-editable content) plus the parser's read-only diagnostic.
NOT_PATCHABLE = {"name", "source", "path", "unknown_keys"}

# Sample values by annotated type. A field with a new type raises
# KeyError here, which is the point: it forces a decision instead of
# silently skipping the field.
SAMPLES = {
    str: "sample-value",
    bool: False,
    list[str]: ["sample-value"],
}

# Values that must satisfy a validator rather than merely round-trip.
SAMPLE_OVERRIDES = {"schedule": "*/5 * * * *"}


def _seed(config, name="drift-probe"):
    path = config.workspace_path / "schedules" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        'schedule: "0 3 * * *"\n'
        "---\n\n"
        "Body.\n"
    )
    return path


def test_every_dataclass_field_reaches_the_wire(config):
    """No field may be invisible to the UI."""
    _seed(config)
    task = {t.name: t for t in discover_schedules(config)}["drift-probe"]
    payload = _schedule_to_dict(config, task)

    missing = [
        f.name for f in fields(ScheduleTask)
        if WIRE_RENAMES.get(f.name, f.name) not in payload
    ]
    assert missing == [], (
        f"fields absent from _schedule_to_dict: {missing}. Add them to the "
        f"wire payload, or add a rename to WIRE_RENAMES if the key differs."
    )


@pytest.mark.parametrize(
    "field_name",
    [f.name for f in fields(ScheduleTask) if f.name not in NOT_PATCHABLE],
)
def test_every_editable_field_is_patchable(config, field_name):
    """write_overlay must accept a patch for each editable field."""
    _seed(config)
    spec = {f.name: f for f in fields(ScheduleTask)}[field_name]
    sample = SAMPLE_OVERRIDES.get(field_name, SAMPLES[spec.type])

    write_overlay(config, "drift-probe", {field_name: sample})
    task = {t.name: t for t in discover_schedules(config)}["drift-probe"]

    assert getattr(task, field_name) == sample, (
        f"write_overlay ignored a patch for {field_name!r}. Add it to the "
        f"replace(...) call, or to NOT_PATCHABLE if it is read-only."
    )


def test_exemption_sets_name_only_real_fields(config):
    """A renamed or deleted field must not leave a stale exemption behind."""
    names = {f.name for f in fields(ScheduleTask)}
    assert NOT_PATCHABLE <= names, f"stale: {NOT_PATCHABLE - names}"
    assert set(WIRE_RENAMES) <= names, f"stale: {set(WIRE_RENAMES) - names}"


def test_frontmatter_raw_is_the_file_not_a_reserialization(config):
    """The raw view exists to show keys the parser drops."""
    path = config.workspace_path / "schedules" / "raw-probe.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        'schedule: "0 3 * * *"\n'
        "modle: vertex-gemini-pro\n"
        "---\n\n"
        "Body.\n"
    )
    task = {t.name: t for t in discover_schedules(config)}["raw-probe"]
    payload = _schedule_to_dict(config, task)

    assert "modle: vertex-gemini-pro" in payload["frontmatter_raw"]
    assert payload["unknown_keys"] == ["modle"]


def test_frontmatter_raw_survives_a_missing_file(config):
    """Bundled tasks whose path was removed must not 500 the list endpoint."""
    task = ScheduleTask(
        name="ghost", schedule="0 3 * * *", body="B",
        source="bundled", path=Path("/nonexistent/SCHEDULE.md"),
    )
    payload = _schedule_to_dict(config, task)
    assert payload["frontmatter_raw"] == ""
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui
.venv/bin/python -m pytest tests/test_schedule_wire_drift.py -q -n0
```

Expected: FAIL — `test_every_dataclass_field_reaches_the_wire` reports `['shell_patterns', 'email_recipients', 'pre_script', 'unknown_keys']`; both `frontmatter_raw` tests fail with `KeyError`.

- [ ] **Step 3: Add the raw reader**

In `src/decafclaw/http_server.py`, add above `_schedule_to_dict`:

```python
def _read_frontmatter_raw(task) -> str:
    """The frontmatter block as it appears on disk.

    Deliberately not a re-serialization of the parsed task:
    ``serialize_to_markdown`` writes only recognized fields, which would
    omit exactly the unrecognized keys this view exists to surface.
    """
    try:
        return _extract_frontmatter_text(task.path.read_text())
    except OSError as exc:
        log.debug("frontmatter_raw: cannot read %s: %s", task.path, exc)
        return ""
```

Add `_extract_frontmatter_text` to the existing `from .skills import (...)` block. If `http_server.py` has no such block, add:

```python
from .skills import _extract_frontmatter_text
```

next to the other `from .` imports at the top.

- [ ] **Step 4: Complete the wire payload**

In `_schedule_to_dict`, add these entries to the returned dict after `"required_skills"`:

```python
        "shell_patterns": list(task.shell_patterns),
        "email_recipients": list(task.email_recipients),
        "pre_script": task.pre_script,
        "unknown_keys": list(task.unknown_keys),
        "frontmatter_raw": _read_frontmatter_raw(task),
```

- [ ] **Step 5: Run to verify it passes**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui
.venv/bin/python -m pytest tests/test_schedule_wire_drift.py -q -n0
```

Expected: PASS, all tests including one parametrized case per editable field.

- [ ] **Step 6: Verify the guard has teeth**

Temporarily delete the `"pre_script": task.pre_script,` line, re-run, confirm `test_every_dataclass_field_reaches_the_wire` fails naming `pre_script`, then restore the line with an editor — **do not** `git checkout` the file, that would also discard the rest of this task's work.

- [ ] **Step 7: Full check and commit**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui
make check && make test
git add src/decafclaw/http_server.py tests/test_schedule_wire_drift.py
git commit -m "feat(schedules): complete the wire payload, guard against drift

_schedule_to_dict now returns shell_patterns, email_recipients,
pre_script, unknown_keys and frontmatter_raw. frontmatter_raw is the
file's own bytes rather than a re-serialization, so unrecognized keys
stay visible.

Adds a guard deriving its expectations from dataclasses.fields, so a
new ScheduleTask field fails the suite until it is wired through or
explicitly exempted."
```

---

### Task 4: `GET /api/models`

**Files:**
- Modify: `src/decafclaw/http_server.py` (handler near the schedule handlers; route in the `routes = [` list around line 2254)
- Test: `tests/test_web_schedules_api.py` (its `client` fixture already provides an authenticated `AsyncClient`)

**Interfaces:**
- Consumes: nothing.
- Produces: `GET /api/models` → `{"models": [str, ...], "default": str}`. `models` is `sorted(config.model_configs)`; `default` is `config.default_model` (empty string when unset). Requires authentication.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web_schedules_api.py`. The file's `client` fixture is an
authenticated `httpx.AsyncClient`; `app` and `http_config` are its collaborators.
`Config` is mutated in place by the `http_config` fixture, so set the model
fields the same way rather than with `dataclasses.replace` (the app already
holds a reference to that object).

```python
class TestModelsEndpoint:
    """Backs the schedules page's model dropdown (#729 follow-on)."""

    @pytest.mark.asyncio
    async def test_lists_configured_models_sorted(self, client, http_config):
        from decafclaw.config_types import ModelConfig, ProviderConfig

        http_config.providers = {"vertex": ProviderConfig(type="vertex", project="p")}
        http_config.model_configs = {
            "vertex-gemini-pro": ModelConfig(provider="vertex", model="gemini-2.5-pro"),
            "vertex-gemini-flash": ModelConfig(provider="vertex", model="gemini-2.5-flash"),
        }
        http_config.default_model = "vertex-gemini-flash"

        res = await client.get("/api/models")
        assert res.status_code == 200
        assert res.json() == {
            "models": ["vertex-gemini-flash", "vertex-gemini-pro"],
            "default": "vertex-gemini-flash",
        }

    @pytest.mark.asyncio
    async def test_empty_config_is_not_an_error(self, client, http_config):
        """A fresh agent with no model_configs must not 500 the page."""
        http_config.model_configs = {}
        http_config.default_model = ""

        res = await client.get("/api/models")
        assert res.status_code == 200
        assert res.json() == {"models": [], "default": ""}

    @pytest.mark.asyncio
    async def test_requires_auth(self, app):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as anon:
            res = await anon.get("/api/models")
        assert res.status_code == 401
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui
.venv/bin/python -m pytest tests/test_web_schedules_api.py -q -n0 -k Models
```

Expected: FAIL with 404.

- [ ] **Step 3: Add the handler**

In `src/decafclaw/http_server.py`, immediately before `# -- Schedule handlers ---`:

```python
@_authenticated
async def models_list(request: Request, username: str) -> JSONResponse:
    """GET /api/models — named model configs for UI pickers.

    A standalone route rather than a field on the schedules payload: the
    schedules page is reachable with no conversation open, and the WS
    `available_models` push is only populated once one loads.
    """
    config = request.app.state.config
    return JSONResponse({
        "models": sorted(config.model_configs),
        "default": config.default_model,
    })
```

- [ ] **Step 4: Register the route**

In the `routes = [` list, next to the schedules routes:

```python
        Route("/api/models", models_list, methods=["GET"]),
```

- [ ] **Step 5: Run to verify it passes**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui
.venv/bin/python -m pytest tests/test_web_schedules_api.py -q -n0 -k Models
```

Expected: PASS.

- [ ] **Step 6: Full check and commit**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui
make check && make test
git add src/decafclaw/http_server.py tests/test_web_schedules_api.py
git commit -m "feat(web): add GET /api/models for UI model pickers"
```

---

### Task 5: Extract `<chip-list>` and refactor `wiki-metadata` onto it

**Files:**
- Create: `src/decafclaw/web/static/components/chip-list.js`, `chip-list.test.js`, `src/decafclaw/web/static/styles/chip-list.css`
- Modify: `src/decafclaw/web/static/components/wiki-metadata.js:174-201` (`#renderChipInput`), its imports
- Modify: `src/decafclaw/web/static/styles/wiki-metadata.css` (remove moved rules), `style.css` (add import)

**Interfaces:**
- Consumes: nothing.
- Produces: `<chip-list>` custom element. Properties: `label: string`, `items: string[]`, `readonly: boolean`. Emits `chips-change` with `detail: { items: string[] }` (bubbles, composed). Renders light DOM with classes `dc-chip`, `button.dc-chip-x`, `dc-chip-input`.

- [ ] **Step 1: Write the failing test**

Create `src/decafclaw/web/static/components/chip-list.test.js`:

```js
import { afterEach, describe, expect, it } from 'vitest';

await import('./chip-list.js');

/** @returns {any} */
function mount(items = [], readonly = false) {
  const el = /** @type {any} */ (document.createElement('chip-list'));
  el.label = 'tags';
  el.items = items;
  el.readonly = readonly;
  document.body.appendChild(el);
  return el;
}

describe('chip-list', () => {
  afterEach(() => { document.body.innerHTML = ''; });

  it('renders one chip per item', async () => {
    const el = mount(['a', 'b']);
    await el.updateComplete;
    expect(el.querySelectorAll('.dc-chip')).toHaveLength(2);
  });

  it('emits chips-change without the removed item', async () => {
    const el = mount(['a', 'b']);
    await el.updateComplete;
    /** @type {any} */ let detail = null;
    el.addEventListener('chips-change', (/** @type {any} */ e) => { detail = e.detail; });

    /** @type {HTMLButtonElement} */
    (el.querySelector('button.dc-chip-x')).click();
    expect(detail.items).toEqual(['b']);
  });

  it('emits chips-change with an appended item on Enter', async () => {
    const el = mount(['a']);
    await el.updateComplete;
    /** @type {any} */ let detail = null;
    el.addEventListener('chips-change', (/** @type {any} */ e) => { detail = e.detail; });

    const input = /** @type {HTMLInputElement} */ (el.querySelector('.dc-chip-input'));
    input.value = 'b';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(detail.items).toEqual(['a', 'b']);
  });

  it('ignores a duplicate', async () => {
    const el = mount(['a']);
    await el.updateComplete;
    let fired = 0;
    el.addEventListener('chips-change', () => { fired += 1; });

    const input = /** @type {HTMLInputElement} */ (el.querySelector('.dc-chip-input'));
    input.value = 'a';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(fired).toBe(0);
  });

  it('hides the input and the remove buttons when readonly', async () => {
    const el = mount(['a'], true);
    await el.updateComplete;
    expect(el.querySelector('.dc-chip-input')).toBeNull();
    expect(el.querySelector('button.dc-chip-x')).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui/src/decafclaw/web/static
npx vitest run chip-list
```

Expected: FAIL — cannot resolve `./chip-list.js`.

- [ ] **Step 3: Write the component**

Create `src/decafclaw/web/static/components/chip-list.js`:

```js
/**
 * Chip list — a set of short string values with add/remove controls.
 *
 * Presentational: performs no I/O and never mutates `items`. Emits
 * `chips-change` with the full next array; the host decides what that
 * means (vault treats an empty array as "remove the key", schedules
 * writes it through as an empty list).
 */

import { LitElement, html, nothing } from 'lit';

export class ChipList extends LitElement {
  static properties = {
    label: { type: String },
    items: { attribute: false },
    readonly: { type: Boolean },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    /** @type {string} */ this.label = '';
    /** @type {string[]} */ this.items = [];
    this.readonly = false;
  }

  /** @param {string[]} next */
  #emit(next) {
    this.dispatchEvent(new CustomEvent('chips-change', {
      detail: { items: next },
      bubbles: true,
      composed: true,
    }));
  }

  /** @param {string} item */
  #remove(item) {
    this.#emit(this.items.filter(i => i !== item));
  }

  /** @param {KeyboardEvent} e */
  #onKey(e) {
    if (e.key !== 'Enter' && e.key !== ',') return;
    e.preventDefault();
    const input = /** @type {HTMLInputElement} */ (e.target);
    const value = input.value.trim().replace(/,$/, '');
    if (!value) return;
    if (this.items.includes(value)) { input.value = ''; return; }
    this.#emit([...this.items, value]);
    input.value = '';
  }

  render() {
    return html`
      ${this.items.map(item => html`
        <span class="dc-chip">
          ${item}
          ${this.readonly ? nothing : html`
            <button
              type="button"
              class="dc-chip-x"
              title="Remove ${item}"
              aria-label="Remove ${item}"
              @click=${() => this.#remove(item)}
            >&times;</button>
          `}
        </span>
      `)}
      ${this.readonly ? nothing : html`
        <input
          class="dc-chip-input"
          type="text"
          placeholder="add…"
          aria-label="Add ${this.label}"
          @keydown=${(/** @type {KeyboardEvent} */ e) => this.#onKey(e)}
        />
      `}
    `;
  }
}

customElements.define('chip-list', ChipList);
```

- [ ] **Step 4: Add its styles**

Create `src/decafclaw/web/static/styles/chip-list.css` by moving the chip rules out of `styles/wiki-metadata.css` (currently `.wiki-md-chip` at ~line 51, `.wiki-md-chip-input` at ~line 110, `button.wiki-md-chip-x` at ~line 127) and renaming the selectors:

```css
/* Chip list — shared by wiki-metadata and schedule-metadata. */

.dc-chip {
  padding: 0.0625rem 0.375rem;
  border: 1px solid var(--pico-muted-border-color);
  border-radius: 1rem;
  color: var(--pico-muted-color);
  white-space: nowrap;
}

.dc-chip-input {
  width: 6rem;
  margin: 0;
  padding: 0 0.25rem;
  font-size: 0.75rem;
}

/* Tag-qualified so Pico's button:not(...) (0,1,1) doesn't win. */
button.dc-chip-x {
  width: auto;
  margin: 0;
  padding: 0 0 0 0.25rem;
  border: none;
  background: none;
  color: inherit;
  font-size: 0.875rem;
  line-height: 1;
}
```

Delete `.wiki-md-chip`, `.wiki-md-chip-input` and `button.wiki-md-chip-x` from `styles/wiki-metadata.css`. Leave `button.wiki-md-raw-toggle` — it is still used — but it currently shares a selector list with `button.wiki-md-chip-x`, so split that rule:

```css
button.wiki-md-raw-toggle {
  width: auto;
  margin: 0;
  border: none;
  background: none;
  color: inherit;
  padding: 0.25rem 0;
  font-size: 0.75rem;
  color: var(--pico-muted-color);
}
```

Add to `src/decafclaw/web/static/style.css` next to the other component imports:

```css
@import './styles/chip-list.css';
```

- [ ] **Step 5: Refactor wiki-metadata onto it**

In `wiki-metadata.js`, add to the imports:

```js
import './chip-list.js';
```

Replace the whole `#renderChipInput` method with:

```js
  /** @param {string} field @param {string} label */
  #renderChipInput(field, label) {
    return html`
      <dt>${label}</dt>
      <dd>
        <chip-list
          .label=${label}
          .items=${this.#list(field)}
          @chips-change=${(/** @type {any} */ e) => this.#emitList(field, e.detail.items)}
        ></chip-list>
      </dd>
    `;
  }
```

`#emitList` already maps an empty array to `null` (key removal), so vault semantics are unchanged. `#removeTag` and `#addTagKey` are now unused — delete both.

**Also update the read-only chip renderer.** `wiki-metadata` has a *second*
chip renderer: `#renderChips` (around line 292) emits `<span class="wiki-md-chip">`
for the collapsed strip and the read-only detail view, and it is not going
away. Since Step 4 deletes the `.wiki-md-chip` rule, this method must move to
the shared class or those chips lose their styling:

```js
  /** @param {string[]} tags */
  #renderChips(tags) {
    return tags.map(tag => html`<span class="dc-chip">${tag}</span>`);
  }
```

After this, `grep -n "wiki-md-chip" src/decafclaw/web/static/components/wiki-metadata.js`
must return nothing. Run it as a check.

- [ ] **Step 6: Run all JS tests**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui
make test-js && make check-js
```

Expected: PASS, including the pre-existing `wiki-metadata.test.js` raw-editor race test.

- [ ] **Step 7: Commit**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui
git add src/decafclaw/web/static/components/chip-list.js \
        src/decafclaw/web/static/components/chip-list.test.js \
        src/decafclaw/web/static/components/wiki-metadata.js \
        src/decafclaw/web/static/styles/chip-list.css \
        src/decafclaw/web/static/styles/wiki-metadata.css \
        src/decafclaw/web/static/style.css
git commit -m "refactor(web): extract <chip-list> from wiki-metadata

Vault needs two chip fields, the schedules panel needs four. Same
add/remove behaviour, now in one presentational component."
```

---

### Task 6: The `<schedule-metadata>` panel

**Files:**
- Create: `src/decafclaw/web/static/components/schedule-metadata.js`, `schedule-metadata.test.js`, `src/decafclaw/web/static/styles/schedule-metadata.css`
- Modify: `src/decafclaw/web/static/style.css`

**Interfaces:**
- Consumes: `<chip-list>` from Task 5; the wire payload from Task 3; `/api/models` from Task 4.
- Produces: `<schedule-metadata>` custom element. Properties: `data: object` (the schedule dict), `models: string[]`, `readonly: boolean`, `error: string` (server message to display, `''` for none). Emits `metadata-change` with `detail: { fields: Record<string, unknown> }` (bubbles, composed) — one key per edit, matching `write_overlay`'s patch keys.

**No conflict banner.** Unlike `wiki-metadata`, this panel does not offer
Reload/Overwrite. `schedules_update` discards the `modified` hint and never
returns 409 — `docs/web-ui.md:93` documents the surface as last-write-wins — so
the affordance would imply a guarantee the server does not make. A plain
`error` string covers the 400s `write_overlay` does raise.

- [ ] **Step 1: Write the failing tests**

Create `src/decafclaw/web/static/components/schedule-metadata.test.js`:

```js
import { afterEach, describe, expect, it } from 'vitest';

await import('./schedule-metadata.js');

const BASE = {
  name: 'dream',
  schedule: '0 3 * * *',
  channel: '',
  model: 'vertex-gemini-pro',
  enabled: true,
  pre_script: '',
  required_skills: ['dream', 'vault'],
  allowed_tools: [],
  shell_patterns: [],
  email_recipients: [],
  unknown_keys: [],
  frontmatter_raw: 'schedule: "0 3 * * *"',
};

/** @returns {any} */
function mount(overrides = {}) {
  const el = /** @type {any} */ (document.createElement('schedule-metadata'));
  el.data = { ...BASE, ...overrides };
  el.models = ['vertex-gemini-flash', 'vertex-gemini-pro'];
  document.body.appendChild(el);
  return el;
}

/** @param {any} el */
function changes(el) {
  /** @type {any[]} */ const seen = [];
  el.addEventListener('metadata-change', (/** @type {any} */ e) => seen.push(e.detail.fields));
  return seen;
}

describe('schedule-metadata', () => {
  afterEach(() => { document.body.innerHTML = ''; });

  it('renders a control for every editable field', async () => {
    const el = mount();
    await el.updateComplete;
    expect(el.querySelector('.sched-md-cron')).toBeTruthy();
    expect(el.querySelector('.sched-md-channel')).toBeTruthy();
    expect(el.querySelector('.sched-md-model')).toBeTruthy();
    expect(el.querySelector('.sched-md-enabled')).toBeTruthy();
    expect(el.querySelector('.sched-md-pre-script')).toBeTruthy();
    expect(el.querySelectorAll('chip-list')).toHaveLength(4);
  });

  it('emits metadata-change when the cron field changes', async () => {
    const el = mount();
    await el.updateComplete;
    const seen = changes(el);

    const input = /** @type {HTMLInputElement} */ (el.querySelector('.sched-md-cron'));
    input.value = '*/5 * * * *';
    input.dispatchEvent(new Event('change', { bubbles: true }));

    expect(seen).toEqual([{ schedule: '*/5 * * * *' }]);
  });

  it('emits metadata-change when a model is picked', async () => {
    const el = mount();
    await el.updateComplete;
    const seen = changes(el);

    const select = /** @type {HTMLSelectElement} */ (el.querySelector('.sched-md-model'));
    select.value = 'vertex-gemini-flash';
    select.dispatchEvent(new Event('change', { bubbles: true }));

    expect(seen).toEqual([{ model: 'vertex-gemini-flash' }]);
  });

  it('emits the enabled checkbox as a boolean', async () => {
    const el = mount();
    await el.updateComplete;
    const seen = changes(el);

    const box = /** @type {HTMLInputElement} */ (el.querySelector('.sched-md-enabled'));
    box.checked = false;
    box.dispatchEvent(new Event('change', { bubbles: true }));

    expect(seen).toEqual([{ enabled: false }]);
  });

  it('forwards chip edits under the right patch key', async () => {
    const el = mount();
    await el.updateComplete;
    const seen = changes(el);

    const chips = /** @type {any} */ (el.querySelector('chip-list[data-field="shell_patterns"]'));
    chips.dispatchEvent(new CustomEvent('chips-change', {
      detail: { items: ['curl *'] }, bubbles: true, composed: true,
    }));

    expect(seen).toEqual([{ shell_patterns: ['curl *'] }]);
  });

  it('flags a stored model that is not configured', async () => {
    // #729: a blank field would read as "no model set", which is the
    // ambiguity that kept the bug invisible.
    const el = mount({ model: 'strong' });
    await el.updateComplete;
    const select = /** @type {HTMLSelectElement} */ (el.querySelector('.sched-md-model'));
    expect(select.value).toBe('strong');
    expect(select.textContent).toContain('not configured');
  });

  it('names unrecognized keys', async () => {
    const el = mount({ unknown_keys: ['modle', 'efort'] });
    await el.updateComplete;
    const warning = el.querySelector('.sched-md-unknown');
    expect(warning?.textContent).toContain('modle');
    expect(warning?.textContent).toContain('efort');
  });

  it('shows no warning when every key is recognized', async () => {
    const el = mount();
    await el.updateComplete;
    expect(el.querySelector('.sched-md-unknown')).toBeNull();
  });

  it('marks the permissions group', async () => {
    const el = mount();
    await el.updateComplete;
    expect(el.querySelector('.sched-md-permissions')).toBeTruthy();
  });

  it('renders raw frontmatter read-only', async () => {
    const el = mount();
    await el.updateComplete;
    /** @type {HTMLButtonElement} */
    (el.querySelector('.sched-md-raw-toggle')).click();
    await el.updateComplete;
    const raw = el.querySelector('.sched-md-raw-body');
    expect(raw?.textContent).toContain('0 3 * * *');
    expect(el.querySelector('.sched-md-raw-body textarea')).toBeNull();
  });

  it('shows a server error and hides it again when cleared', async () => {
    const el = mount();
    el.error = 'invalid cron expression: \'nope\'';
    await el.updateComplete;
    expect(el.querySelector('.sched-md-error')?.textContent).toContain('invalid cron');

    el.error = '';
    await el.updateComplete;
    expect(el.querySelector('.sched-md-error')).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui/src/decafclaw/web/static
npx vitest run schedule-metadata
```

Expected: FAIL — cannot resolve `./schedule-metadata.js`.

- [ ] **Step 3: Write the component**

Create `src/decafclaw/web/static/components/schedule-metadata.js`:

```js
/**
 * Schedule metadata panel — every frontmatter field a schedule supports.
 *
 * Presentational: performs no I/O. The host (schedule-page) owns every PUT
 * so metadata writes serialise against wiki-editor's body autosave, the
 * same division of labour wiki-metadata uses for vault pages.
 *
 * Emits `metadata-change` with one patch key per edit. Key names match
 * write_overlay's patch keys exactly.
 *
 * The raw view is read-only, unlike the vault panel's. Schedule
 * frontmatter maps onto a fixed dataclass and serialize_to_markdown
 * writes only recognised fields, so an editable box would accept a key
 * and silently drop it on the next write.
 */

import { LitElement, html, nothing } from 'lit';
import './chip-list.js';

/** Chip-backed fields that pre-approve actions past confirmation. */
const PERMISSION_LISTS = [
  ['allowed_tools', 'Allowed tools'],
  ['shell_patterns', 'Shell patterns'],
  ['email_recipients', 'Email recipients'],
];

export class ScheduleMetadata extends LitElement {
  static properties = {
    data: { attribute: false },
    models: { attribute: false },
    readonly: { type: Boolean },
    error: { type: String },
    _rawOpen: { state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    /** @type {any} */ this.data = null;
    /** @type {string[]} */ this.models = [];
    this.readonly = false;
    /** Server message for the last failed write; '' when clear. */
    this.error = '';
    this._rawOpen = false;
  }

  /** @param {string} field @param {unknown} value */
  #emit(field, value) {
    this.dispatchEvent(new CustomEvent('metadata-change', {
      detail: { fields: { [field]: value } },
      bubbles: true,
      composed: true,
    }));
  }

  /** @param {string} field @param {Event} e */
  #onText(field, e) {
    this.#emit(field, /** @type {HTMLInputElement} */ (e.target).value);
  }

  /** @param {string} field @param {string} label */
  #renderChips(field, label) {
    return html`
      <label>
        <span>${label}</span>
        <chip-list
          data-field=${field}
          .label=${label}
          .items=${this.data?.[field] ?? []}
          ?readonly=${this.readonly}
          @chips-change=${(/** @type {any} */ e) => this.#emit(field, e.detail.items)}
        ></chip-list>
      </label>
    `;
  }

  #renderModel() {
    const current = this.data?.model ?? '';
    // A stored value absent from model_configs renders as a flagged
    // option rather than a blank field — blank reads as "unset", which
    // is exactly how #729 stayed invisible.
    const unconfigured = current && !this.models.includes(current);
    return html`
      <label>
        <span>Model</span>
        <select
          class="sched-md-model"
          ?disabled=${this.readonly}
          .value=${current}
          @change=${(/** @type {Event} */ e) =>
            this.#emit('model', /** @type {HTMLSelectElement} */ (e.target).value)}
        >
          <option value="">(default)</option>
          ${unconfigured ? html`
            <option value=${current} selected>⚠ ${current} (not configured)</option>
          ` : nothing}
          ${this.models.map(m => html`<option value=${m}>${m}</option>`)}
        </select>
      </label>
    `;
  }

  #renderUnknown() {
    const keys = this.data?.unknown_keys ?? [];
    if (!keys.length) return nothing;
    const plural = keys.length === 1 ? 'key is' : 'keys are';
    return html`
      <div class="sched-md-unknown">
        ⚠ ${keys.length} ${plural} not recognized and ${keys.length === 1 ? 'is' : 'are'}
        ignored: ${keys.join(', ')}
      </div>
    `;
  }

  #renderRaw() {
    return html`
      <div class="sched-md-raw">
        <button
          type="button"
          class="sched-md-raw-toggle"
          @click=${() => { this._rawOpen = !this._rawOpen; }}
        >${this._rawOpen ? '▾' : '▸'} raw (read-only)</button>
        ${this._rawOpen ? html`
          <pre class="sched-md-raw-body">${this.data?.frontmatter_raw ?? ''}</pre>
        ` : nothing}
        ${this.#renderUnknown()}
      </div>
    `;
  }

  render() {
    if (!this.data) return nothing;
    return html`
      <div class="sched-md">
        ${this.error ? html`
          <div class="sched-md-error" role="alert">${this.error}</div>
        ` : nothing}
        <div class="sched-md-form">
          <label>
            <span>Cron</span>
            <input
              class="sched-md-cron"
              type="text"
              ?disabled=${this.readonly}
              .value=${this.data.schedule ?? ''}
              @change=${(/** @type {Event} */ e) => this.#onText('schedule', e)}
            />
          </label>
          <label>
            <span>Channel</span>
            <input
              class="sched-md-channel"
              type="text"
              placeholder="(default channel)"
              ?disabled=${this.readonly}
              .value=${this.data.channel ?? ''}
              @change=${(/** @type {Event} */ e) => this.#onText('channel', e)}
            />
          </label>
          ${this.#renderModel()}
          <label class="inline">
            <input
              class="sched-md-enabled"
              type="checkbox"
              ?disabled=${this.readonly}
              .checked=${Boolean(this.data.enabled)}
              @change=${(/** @type {Event} */ e) =>
                this.#emit('enabled', /** @type {HTMLInputElement} */ (e.target).checked)}
            />
            <span>Enabled</span>
          </label>
          <label>
            <span>Pre-script</span>
            <input
              class="sched-md-pre-script"
              type="text"
              placeholder="(none)"
              ?disabled=${this.readonly}
              .value=${this.data.pre_script ?? ''}
              @change=${(/** @type {Event} */ e) => this.#onText('pre_script', e)}
            />
          </label>
          ${this.#renderChips('required_skills', 'Required skills')}
        </div>

        <div class="sched-md-permissions">
          <div class="sched-md-permissions-title">
            ⚠ Permissions — these bypass confirmation
          </div>
          ${PERMISSION_LISTS.map(([f, l]) => this.#renderChips(f, l))}
        </div>

        ${this.#renderRaw()}
      </div>
    `;
  }
}

customElements.define('schedule-metadata', ScheduleMetadata);
```

- [ ] **Step 4: Add its styles**

Create `src/decafclaw/web/static/styles/schedule-metadata.css`:

```css
/* Schedule metadata panel. */

.sched-md-form,
.sched-md-permissions {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.sched-md-permissions {
  margin-top: 0.75rem;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--pico-del-color);
  border-radius: var(--pico-border-radius);
}

.sched-md-permissions-title {
  color: var(--pico-del-color);
  font-size: 0.75rem;
}

.sched-md-unknown {
  margin-top: 0.5rem;
  color: var(--pico-del-color);
  font-size: 0.75rem;
}

.sched-md-error {
  margin-bottom: 0.5rem;
  padding: 0.375rem 0.5rem;
  border: 1px solid var(--pico-del-color);
  border-radius: var(--pico-border-radius);
  color: var(--pico-del-color);
  font-size: 0.75rem;
}

.sched-md-raw {
  margin-top: 0.75rem;
}

.sched-md-raw-body {
  margin: 0.25rem 0 0;
  padding: 0.5rem;
  overflow-x: auto;
  background: var(--pico-card-background-color);
  font-size: 0.75rem;
}

/* Tag-qualified so Pico's button:not(...) (0,1,1) doesn't win. */
button.sched-md-raw-toggle {
  width: auto;
  margin: 0;
  padding: 0.25rem 0;
  border: none;
  background: none;
  color: var(--pico-muted-color);
  font-size: 0.75rem;
}
```

Add to `src/decafclaw/web/static/style.css`:

```css
@import './styles/schedule-metadata.css';
```

- [ ] **Step 5: Run to verify it passes**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui
make test-js && make check-js
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui
git add src/decafclaw/web/static/components/schedule-metadata.js \
        src/decafclaw/web/static/components/schedule-metadata.test.js \
        src/decafclaw/web/static/styles/schedule-metadata.css \
        src/decafclaw/web/static/style.css
git commit -m "feat(web): add <schedule-metadata> panel

Every schedule frontmatter field gets a control, with the three that
pre-approve actions past confirmation grouped and marked. Raw view is
read-only because schedule frontmatter is a closed set; unrecognised
keys are named rather than silently dropped."
```

---

### Task 7: Host the panel in `schedule-page`

**Files:**
- Modify: `src/decafclaw/web/static/components/schedule-page.js`

**Interfaces:**
- Consumes: `<schedule-metadata>` (Task 6); `GET /api/models` (Task 4); the existing `#patchField` PUT path.
- Produces: nothing downstream.

- [ ] **Step 1: Write the failing test**

Create `src/decafclaw/web/static/components/schedule-page.test.js`:

```js
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

await import('./schedule-page.js');

const SCHEDULE = {
  name: 'dream', schedule: '0 3 * * *', channel: '', model: '',
  enabled: true, pre_script: '', required_skills: [], allowed_tools: [],
  shell_patterns: [], email_recipients: [], unknown_keys: [],
  frontmatter_raw: '', source_tier: 'bundled', has_overlay: false,
  body: 'Body.', modified: 1,
};

describe('schedule-page', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (/** @type {string} */ url) => {
      if (url.startsWith('/api/models')) {
        return { ok: true, json: async () => ({ models: ['a', 'b'], default: 'a' }) };
      }
      return { ok: true, json: async () => ({ schedule: SCHEDULE }) };
    }));
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  it('renders the metadata panel and feeds it the model list', async () => {
    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    const panel = /** @type {any} */ (el.querySelector('schedule-metadata'));
    expect(panel).toBeTruthy();
    expect(panel.models).toEqual(['a', 'b']);
  });

  it('PUTs the patch when the panel emits metadata-change', async () => {
    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    /** @type {any} */ (globalThis.fetch).mockClear();
    const panel = /** @type {any} */ (el.querySelector('schedule-metadata'));
    panel.dispatchEvent(new CustomEvent('metadata-change', {
      detail: { fields: { model: 'b' } }, bubbles: true, composed: true,
    }));
    await new Promise(r => setTimeout(r, 0));

    const [url, init] = /** @type {any} */ (globalThis.fetch).mock.calls[0];
    expect(url).toBe('/api/schedules/dream');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(init.body)).toEqual({ model: 'b' });
  });

  it('surfaces a 400 on the panel instead of only logging it', async () => {
    const el = /** @type {any} */ (document.createElement('schedule-page'));
    el.name = 'dream';
    document.body.appendChild(el);
    await el.updateComplete;
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    /** @type {any} */ (globalThis.fetch).mockImplementation(async () => ({
      ok: false,
      status: 400,
      json: async () => ({ error: "invalid cron expression: 'nope'" }),
    }));

    const panel = /** @type {any} */ (el.querySelector('schedule-metadata'));
    panel.dispatchEvent(new CustomEvent('metadata-change', {
      detail: { fields: { schedule: 'nope' } }, bubbles: true, composed: true,
    }));
    await new Promise(r => setTimeout(r, 0));
    await el.updateComplete;

    expect(/** @type {any} */ (el.querySelector('schedule-metadata')).error)
      .toContain('invalid cron');
  });
});
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui/src/decafclaw/web/static
npx vitest run schedule-page
```

Expected: FAIL — no `schedule-metadata` element in the page's output.

- [ ] **Step 3: Fetch the model list**

In `schedule-page.js`, add `import './schedule-metadata.js';` next to the `wiki-editor` import, add `_models: { state: true }` to `static properties`, and `this._models = [];` to the constructor.

Add this method next to `#fetchSchedule`:

```js
  async #fetchModels() {
    try {
      const res = await fetch('/api/models');
      if (!res.ok) return;
      const data = await res.json();
      this._models = data.models || [];
    } catch (e) {
      // Non-fatal: the panel falls back to whatever is stored.
      console.warn('schedule-page: model list fetch failed:', e);
    }
  }
```

Call it from `updated` alongside `#fetchSchedule`:

```js
  updated(changedProps) {
    if (changedProps.has('name') && this.name) {
      this.#fetchSchedule();
      if (!this._models.length) this.#fetchModels();
    }
  }
```

- [ ] **Step 4: Swap the inline controls for the panel**

Add `_saveError: { state: true }` to `static properties` and `this._saveError = '';`
to the constructor, then add a handler next to `#patchField`:

```js
  /** @param {CustomEvent} e */
  async #onMetadataChange(e) {
    const fields = e.detail?.fields ?? {};
    for (const [field, value] of Object.entries(fields)) {
      await this.#patchField(field, value);
    }
  }
```

`#patchField` currently swallows failures with a `console.warn`, which would
leave an invalid cron looking like it saved. Set `_saveError` on the non-ok
branch and clear it on success. In `#patchField`, replace the existing
non-ok warn with:

```js
      if (!res.ok) {
        let message = `save failed (${res.status})`;
        try {
          const err = await res.json();
          if (err?.error) message = err.error;
        } catch {
          // Non-JSON error body; the status line is all we have.
        }
        this._saveError = message;
        console.warn(`schedule-page: PUT ${field} failed:`, res.status);
        return;
      }
      this._saveError = '';
```

Match the surrounding code's existing variable names when splicing this in —
the current block already has `res` and `field` in scope.

In `render()`, replace the entire `<div class="schedule-page-form"> … </div>` block (the Cron, Channel and Enabled labels) with:

```html
        <schedule-metadata
          .data=${d}
          .models=${this._models}
          .error=${this._saveError}
          @metadata-change=${(/** @type {CustomEvent} */ e) => this.#onMetadataChange(e)}
        ></schedule-metadata>
```

- [ ] **Step 5: Run to verify it passes**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui
make test-js && make check-js
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui
git add src/decafclaw/web/static/components/schedule-page.js \
        src/decafclaw/web/static/components/schedule-page.test.js
git commit -m "feat(web): host <schedule-metadata> in the schedule page

Replaces the three inline controls. The page keeps ownership of every
PUT, fanning a metadata-change patch out through the existing
#patchField path."
```

---

### Task 8: Documentation

**Files:**
- Modify: `docs/schedules.md`, `docs/web-ui.md`

- [ ] **Step 1: Complete the frontmatter reference**

In `docs/schedules.md`, the field table around line 36 lists `model` but not the newer fields. Add rows so every `ScheduleTask` field appears:

```markdown
| `shell_patterns` | list | no | — | Derived from `shell(...)` entries in `allowed-tools`. Pre-approves matching commands. |
| `email-recipients` | list | no | — | Addresses `send_email` may use without confirmation, merged with `config.email.allowed_recipients`. |
| `pre_script` | string | no | — | Python script run before the turn; stdout is injected into the prompt. |
```

- [ ] **Step 2: Document unrecognized keys**

Add after the frontmatter table:

```markdown
### Unrecognized keys

The parser reads only the keys above. Anything else in a schedule's
frontmatter is ignored, logged at WARNING on load, and listed in the web
UI's raw section:

```
⚠ 2 keys in this file are not recognized and are ignored: modle, efort
```

This exists because a silently-dropped key is indistinguishable from a
key that took effect — the failure mode behind #729, where `model: strong`
was accepted and discarded for months.
```

- [ ] **Step 3: Document the UI**

Add a section to `docs/schedules.md`:

```markdown
## Editing from the web UI

The schedules page exposes every frontmatter field. Edits PUT to
`/api/schedules/{name}`, which writes an admin overlay for skill-supplied
schedules and edits standalone files in place.

`allowed-tools`, shell patterns and `email-recipients` are grouped and
marked separately: they pre-approve actions that would otherwise require
confirmation.

The raw section is read-only. Schedule frontmatter maps onto a fixed set
of fields, so once each has a control there is nothing an editable raw
box could reach — and a key typed there would be dropped on the next
write.
```

- [ ] **Step 4: Update the web UI doc**

`docs/web-ui.md:92` currently reads:

```markdown
- **Form row**: cron expression input, channel input, and an enabled checkbox. Each field saves on `change` — no separate Save button needed.
```

Replace it with:

```markdown
- **Metadata panel** (`<schedule-metadata>`): every frontmatter field the schedule format supports — cron, channel, model, enabled, pre-script, and chip lists for required skills, allowed tools, shell patterns and email recipients. Each field saves on `change`; no separate Save button. The last three are grouped and marked, because they pre-approve actions that would otherwise need confirmation.
- **Raw section**: a read-only view of the frontmatter as it sits on disk, plus a warning naming any key the parser does not recognize. Read-only because the field set is closed — anything typed there that is not a known field would be dropped on the next write.
```

Leave line 93's last-write-wins note alone; it is accurate and this work does not change it.

- [ ] **Step 5: Verify no stale claims remain**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui
grep -rn "cron, channel\|channel, enabled\|model: strong" docs/ || echo "clean"
```

Fix anything that turns up.

- [ ] **Step 6: Final verification and commit**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/schedule-frontmatter-ui
make check && make test && make test-js
git add docs/
git commit -m "docs: schedule frontmatter reference and UI editing"
```

---

## Final verification

- [ ] `make check` — ruff, pyright (0 errors), tsc, message-type drift
- [ ] `make test` — full suite green, count above the 3612 baseline
- [ ] `make test-js` — vitest green
- [ ] `.venv/bin/python -m pytest --durations=25 2>&1 | head -30` — no new test in the top 25
- [ ] Manual smoke: start the server on port 18897, open the schedules page, confirm the model dropdown populates, a change persists across reload, and a schedule with a deliberately typo'd key shows the warning
- [ ] Open the PR with `Closes` referencing the follow-on issue if one was filed; note #731 as related-but-separate

## Notes for the implementer

- Task 3's guard is the point of the whole plan. If a later task adds a `ScheduleTask` field, that guard should fail — wire the field through rather than adding it to an exemption set, unless it is genuinely read-only diagnostic like `unknown_keys`.
- Do not fold #731 (workspace-tier preapproval) into this work. If its fix lands first, the permissions group may want tier-dependent labelling; that is a follow-up either way.
- The `wiki-metadata.test.js` raw-editor race test guards a real bug that was hard to find. If the Task 5 refactor breaks it, the refactor is wrong — do not adjust the test.
