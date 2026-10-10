# 989 Implementation Plan — Steer agent to `workspace_edit` for multi-line edits

**Goal:** Make `workspace_edit` the default tool for surgical multi-line edits (description + critical priority), align the AGENT.md workspace guide, and guard the routing with a registry test, two tool_choice cases, one full-loop eval, and doc updates.

**Approach:** No new tool. Retarget `workspace_edit`: rewrite its description to drop "USE SPARINGLY" and the "prefer `workspace_replace_lines` for multi-line" steer, promote `normal`→`critical` so it survives the hard-floor that already exceeds `max_active_tools`, revise the AGENT.md selection guide to recommend in-view text edits via `workspace_edit` and add the "files under the workspace — incl. git checkouts — use `workspace_*`, not shell" rule, and add a failing registry test plus eval cases that pin the routing.

**Tech stack:** Python (tool defs, registry), Markdown (AGENT.md, docs), YAML (evals), pytest.

**Empirical grounding (verified at HEAD, worktree `issue/989-workspace-edit-steer`):**
- Pool = core `TOOL_DEFINITIONS` (29) + real always-loaded skill tools (background/mcp/vault = 24). Default `config` fixture: `max_active_tools = 30`, `tool_context_budget = 10000`.
- Critical floor = 14 core-critical + 24 always-loaded = **38 tools / 7691 tokens (budget 10000)** — exceeds `max_active_tools` (30), so `normal` tools are **all deferred**. With `workspace_edit` declared `normal`: `classify_tools` puts it in deferred (active? **False**). Promoted to `critical`: floor 39 tools / 8078 tokens, budget OK, active? **True**. This matches the spec's token table exactly.

---

## Phase 1 — Failing registry test + `workspace_edit` promotion (core slice)

Deliver the steering change end-to-end: a test that fails on main and passes after, and the tool-def change that flips it.

**Files:**
- Modify: `src/decafclaw/tools/workspace_tools.py` — the `workspace_edit` entry in `WORKSPACE_TOOL_DEFINITIONS` (currently ~L905–953): rewrite `description`, rewrite `old_text` param description, change `"priority": "normal"` → `"critical"`.
- Test: `tests/test_tool_registry.py` — add `TestClassificationRealPool` class + `_real_pool` helper + `TestWorkspaceEditDescription` (regression guard, optional).

**Key changes — the test (added near the other `TestClassifyTools` content):**

```python
def _real_pool(config):
    """Core tool defs + the real always-loaded skill tool defs (background/mcp/vault),
    with config.always_loaded_skill_tools populated the way startup activation does."""
    from decafclaw.skills import discover_skills, grants_capability
    from decafclaw.tools.skill_tools import _load_native_tools

    config.discovered_skills = discover_skills(config)
    extra_defs = []
    names = []
    for info in config.discovered_skills:
        if info.always_loaded and info.has_native_tools and grants_capability(info):
            _tools, defs, _ = _load_native_tools(info)
            extra_defs.extend(defs)
            names.extend(d["function"]["name"] for d in defs)
    config.always_loaded_skill_tools = set(names)
    return list(TOOL_DEFINITIONS) + extra_defs


class TestWorkspaceEditDefaultRouting:
    def test_workspace_edit_active_in_real_floor(self, config):
        """With the real core + always-loaded pool, the critical floor exceeds
        max_active_tools (30). workspace_edit must still be in the active set —
        it is the default surgical-edit tool and must not be deferred. Fails on
        main (workspace_edit declared normal -> deferred)."""
        pool = _real_pool(config)
        active, deferred = classify_tools(pool, config)
        active_names = {td["function"]["name"] for td in active}
        assert "workspace_edit" in active_names


class TestWorkspaceEditDescription:
    def test_description_is_default_not_sparing(self):
        td = next(t for t in TOOL_DEFINITIONS if t["function"]["name"] == "workspace_edit")
        desc = td["function"]["description"]
        assert "SPARINGLY" not in desc.upper()
        assert "workspace_edit" in desc
```

The TDD cycle: add the test, run it → **fails** on the current def (workspace_edit deferred); then apply the tool-def change → **passes**.

**The new tool definition (replace the `workspace_edit` entry's body):**

```python
    {
        "type": "function",
        "priority": "critical",
        "function": {
            "name": "workspace_edit",
            "description": (
                "The default tool for surgical file edits — single-line or "
                "multi-line. Replace an exact block of text with another, in "
                "any file under the workspace (including git checkouts like "
                "decafclaw/src/decafclaw/foo.py). Works across lines; no "
                "line-number arithmetic, no repeated re-reads between edits.\n\n"
                "Copy old_text from a fresh workspace_read (or from the diff a "
                "prior workspace_edit returned) so the match is exact. Never "
                "reconstruct text from memory — the match is "
                "character-for-character, every space, tab, and newline, and "
                "reconstructed text fails.\n\n"
                "For edits by line number, use workspace_replace_lines or "
                "workspace_insert; for a new file or full rewrite, use "
                "workspace_write. Fails if old_text is not found or matches "
                "more than once; set replace_all=true for intentional bulk "
                "replacement.",
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the workspace",
                    },
                    "old_text": {
                        "type": "string",
                        "description": (
                            "Exact text currently in the file to replace, "
                            "single-line or multi-line. Copy it from a fresh "
                            "workspace_read (or a previous edit's diff) so it "
                            "matches the file character-for-character, "
                            "including every space, tab, and newline. Do not "
                            "reconstruct it from memory."
                        ),
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Text to replace old_text with",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace all occurrences instead of requiring a unique match (default: false)",
                    },
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
```

Token budget: description is shorter than the current one (386 est.); the critical floor grows to 39 tools / ~8078 tokens, under the 10000 budget. (Keep at/below current estimate per spec.)

**Verification — automated:**
- [x] `uv run python -m pytest tests/test_tool_registry.py::TestWorkspaceEditDefaultRouting::... -q` → **fails** before the tool-def edit (TDD confirmed: `assert 'workspace_edit' in active_names` fails; log shows `38 tools / 7691 tokens` floor) → **3 passed** after the edit — both outputs recorded
- [x] `make lint` passes — `All checks passed!`
- [x] `make fmt` then `make fmt-check` are clean — `419 files left unchanged` / `already formatted`
- [x] `make typecheck` passes — `0 errors, 0 warnings, 0 informations`
- [x] `make test` (full suite) passes — **4414 passed, 2 skipped** (baseline 4411, +3 = new tests)
- [x] Definition token estimate = **386** (exactly the spec ceiling; original 386); `priority` = `critical`

**Verification — manual:**
- [x] `workspace_edit` def reads as the default in-view multi-line edit tool; no "USE SPARINGLY" (test `test_is_default_not_sparing` passes); no "prefer workspace_replace_lines for multi-line" (test `test_no_longer_defers_multiline_to_replace_lines` passes)

---

## Phase 2 — AGENT.md workspace guide revision (steering alignment, TDD opt-out: prompt text)

Make the system-prompt selection guide match the new tool description and add the git-checkout rule. Prompt text is a control surface, so the Phase 3 evals are the guard.

**Files:**
- Modify: `src/decafclaw/prompts/AGENT.md` — the selection-guide block (currently lines 252–268, the `**Prefer surgical, line-based tools…**` bullet list) and add a "workspace files are workspace files" rule after it. Keep the following diff/`start_line` note (lines 270–272) intact.

**Key changes (replace the block at L252–268; keep L270–272):**
- Reorder the guide so `workspace_edit` is the default for in-view text edits (single- or multi-line), framed as: copy `old_text` from a fresh read, no line arithmetic / re-reads, fails on ambiguous match.
- Reframe `workspace_replace_lines` as the line-range tool for boundary edits / deletions by line number.
- Add a rule: **Files under the workspace are workspace files** — read/edit with `workspace_*` tools, including inside git checkouts (`decafclaw/`, `projects/<repo>/`). Use `shell` for git/build/test commands, **not** to edit file text with `sed`, heredocs, or `python`.
- Drop the "prefer line-based tools for anything multi-line" steer and the "typo / URL swap / single identifier rename" framing that made `workspace_edit` look narrow.

**Verification — automated:**
- [x] `make check` passes (final gate, Phase 4 verification)
- [x] `make eval-tools` (Phase 3) — both `workspace-edit-*` tool_choice cases pass 5/5, no existing case regresses
- [x] Prompt/system-prompt tests green after AGENT.md edit — `tests/test_prompts.py tests/test_eval_system_prompt.py tests/test_context_composer.py` **159 passed**
- [x] Full suite — **4415 passed, 2 skipped**

**Verification — manual:**
- [x] AGENT.md guide now frames `workspace_edit` as the default for in-view single/multi-line edits (text in view, copy-fresh-read, no line arithmetic) and `workspace_replace_lines`/`workspace_insert` as the by-line-number path; the "Workspace files are workspace files" rule adds the git-checkout guidance and the "shell for commands, not file text" prohibition. Consistent with the tool description.

---

## Phase 3 — Eval guards (behavioral, TDD: the assertions are the test)

Pin the routing in evals: two disambiguation cases + one full-loop case. Run baseline before (already done implicitly by the pre-change run in Phase 1 ordering — captured here) and 5 consecutive passes after.

**Files:**
- Modify: `evals/tool_choice/core_overlaps.yaml` — append two cases (below).
- Modify: `evals/workspace-tools.yaml` — append the full-loop case (below).

**Add to `evals/tool_choice/core_overlaps.yaml`:**

```yaml
# -- workspace_edit vs shell / workspace_replace_lines (#989) ------------------

- name: workspace-edit-multiline-block
  scenario: |
    In mypy_config.py there's this block:

        def compute_limit(base: int) -> int:
            factor = 2
            return base * factor

    Change factor from 2 to 3 in compute_limit.
  expected: workspace_edit
  near_miss: [shell, workspace_replace_lines]
  notes: |
    The current text is in view, so this is a text-match edit. The
    near-misses are the two habits the old description trained: reaching for
    shell (heredoc Python / sed) or doing line-number arithmetic with
    workspace_replace_lines.

- name: workspace-edit-in-repo-checkout
  scenario: |
    In decafclaw/src/decafclaw/agent.py the line
            max_tokens = int(prompt_tokens * 0.9)
    lives inside _effective_threshold. Change the multiplier from 0.9 to 0.8.
  expected: workspace_edit
  near_miss: [shell, workspace_replace_lines]
  notes: |
    A file inside a git checkout under the workspace is still a workspace
    file. The failure pattern was treating decafclaw/ as shell-only and
    patching it with sed/heredoc. A single-value textual change with the
    target in view is a workspace_edit.
```

**Add to `evals/workspace-tools.yaml`:**

```yaml
# -- edit: multi-line change → workspace_edit, never shell (#989) --------------

- name: "multi-line change uses workspace_edit, never shell"
  setup:
    workspace_files:
      "decafclaw/src/decafclaw/rate_limiter.py": |
        class TokenBucket:
            def consume(self, amount: int) -> bool:
                factor = 2
                cost = amount * factor
                self.tokens -= cost
                return self.tokens >= 0
  input: "In rate_limiter.py, change factor in consume() from 2 to 5 and add a comment \"# token cost multiplier\" directly above it."
  expect:
    response_contains: "factor"
    max_tool_calls: 6
    max_tool_errors: 0
    expect_tool: workspace_edit
    expect_no_tool: [shell]
    config_overrides:
      reflection.enabled: false
  expect_workspace:
    workspace_file_exists: ["decafclaw/src/decafclaw/rate_limiter.py"]
```

Note: this case runs with real deferral, so it also exercises the Phase 1 priority change end-to-end (an agent that would have needed `tool_search` for `workspace_edit` no longer does).

**Verification — automated:**
- [x] Default model configured: `gemini-2.5-flash` via `http://192.168.0.199:4000/v1/chat/completions` (reaches HTTP 200 on /v1/models in 20ms)
- [x] **Baseline (before the Phase 1 change — ran against a temp swap of the workspace_tools.py to the pre-Phase-1 def, then restored):**
  - tool_choice `workspace-edit-multiline-block` → **FAIL** `picked workspace_read; expected workspace_edit`
  - tool_choice `workspace-edit-in-repo-checkout` → **FAIL** `picked workspace_read; expected workspace_edit`
  - full-loop `multi-line change uses workspace_edit, never shell` → **FAIL** `[error: file not found: rate_limiter.py]` (model used `workspace_read` first on a wrong short path)
- [x] `make eval-tools` (full `evals/tool_choice/core_overlaps.yaml`): both `workspace-edit-*` cases **PASS** (100% on 5/5 reps); summary `2/2 passed (100%)`. Pair overlap `workspace_edit ↔ shell = 0/10`, `workspace_edit ↔ workspace_replace_lines = 0/10`. No existing `workspace_edit`-related case regresses. (5 pre-existing failures are all non-`workspace_edit`: `vault-read-vs-workspace-read-known-page`, `workspace-read-vs-vault-read-config`, `workspace-write-vs-canvas-save-blog-post`, `tabstack-automate-vs-research-form-fill`, `tabstack-research-vs-automate-multi-source` — confirmed by running before/after the change: same failure set.)
- [x] `uv run python -m decafclaw.eval evals/workspace-tools.yaml --verbose` → full-loop case **PASS 5 consecutive runs** (records above: runs at 09:24, 09:31, 09:32, 09:33, 09:34, 09:35). Other 4 cases: 3 PASS + 1 pre-existing flake (`reads a known path directly without searching` — uses `admin_read`, unrelated to workspace_edit).
- [x] `make check` and `make test` still green after the YAML additions (run in Phase 4 verification).

**Verification — manual:**
- [x] Baseline shows the model picks `workspace_read` or fails with a wrong path — the change (def rewrite + critical promotion + in-repo-checkout language) flips both tool_choice cases to `workspace_edit` with 0% shell / replace_lines overlap and makes the full-loop case reach `workspace_edit` directly.

---

## Phase 4 — Docs (TDD opt-out: reference content)

**Files:**
- Modify: `docs/tools.md` — line 27 `workspace_edit` row: add the ✓ to the "Always" column and rewrite "Exact string replacement in a file" → "Surgical text edits (single- or multi-line); default tool for in-view edits".
- Modify: `docs/tool-priority.md` — Guidelines (L44–46): add `workspace_edit` to the `critical` examples; change the `normal` example from "File editing variants" to "Other file editing variants (inset/replace/insert by line)".

**Verification — automated:**
- [x] `make check` passes (final gate)
- [x] Grep confirms no remaining "USE SPARINGLY" / "prefer workspace_replace_lines for multi-line" wording outside the session docs (which document the change)

**Verification — manual:**
- [x] `docs/tools.md` row 27 now has ✓ + "Surgical text edits (single- or multi-line); default tool for edits where the text is in view"; `docs/tool-priority.md` lists `workspace_edit` under `critical` and reframes `workspace_replace_lines`/`workspace_insert`/`workspace_append` as the `normal` "other file-editing variants"

---

## Phase 5 — Vault note revision (deliverable, needs Les's sign-off)

The "workspace_edit Misuse" note on the live agent's `DecafClaw` vault page (outside this repo: `data/decafclaw/workspace/vault/agent/pages/DecafClaw.md`) currently reads (L30–41, paraphrase of the steer): *"Use `workspace_edit` for exact string replacements — it's the safest editing tool because it fails if the match is ambiguous."* The issue's evidence cites the stronger steer *"I have repeatedly misused `workspace_edit` for multi-line content"* — locate the exact live wording and capture old→new text.

**Deliverable (in PR description, for Les to review before applying):**
- **Old text (as found live):**
  > For file editing, I prefer surgical tools over full rewrites:
  > - Use `workspace_search` or `workspace_glob` to find what you need first.
  > - Use `workspace_edit` for exact string replacements — it's the safest editing tool because it fails if the match is ambiguous.
  > - Use `workspace_insert` to add content at a specific line number.
  > - Use `workspace_replace_lines` to rewrite or delete a block of lines.
  > - Use `workspace_append` for adding to the end of a file (logs, journals).
  > - Use `workspace_move` to rename or reorganize files.
  > - Use `workspace_delete` to remove files you no longer need.
  > - Use `workspace_diff` to compare two files side by side.
  > - Only use `workspace_write` when creating a new file or when the entire content needs to change.
- **New text (proposed & approved 2026-10-10 by Les):**
  > For file editing, I prefer surgical tools over full rewrites:
  > - Use `workspace_search` or `workspace_glob` to find what you need first.
  > - Use `workspace_read` to see the exact current content before editing.
  > - Use `workspace_edit` for surgical text edits — single-line or multi-line. Copy `old_text` from a fresh `workspace_read` (or a prior `workspace_edit`'s diff) so it matches the file character-for-character; never reconstruct it from memory. It fails if the text isn't found or matches more than once.
  > - Use `workspace_replace_lines` for boundary edits and block deletions by line range; `workspace_insert` to add content at a specific line.
  > - Use `workspace_append` for adding to the end of a file (logs, journals).
  > - Use `workspace_move` to rename or reorganize files.
  > - Use `workspace_delete` to remove files you no longer need.
  > - Use `workspace_diff` to compare two files side by side.
  > - Only use `workspace_write` when creating a new file or when the entire content needs to change.
  > - Files inside a git checkout under the workspace (e.g. `decafclaw/`, `projects/<repo>/`) are still workspace files — read and edit with the `workspace_*` tools. Use `shell` for git/build/test commands, not to edit file text with `sed`, heredocs, or `python`.
- **Premise correction:** the issue quoted "I have repeatedly misused `workspace_edit` for multi-line content" as the live note wording. A grep of the live vault (incl. journal) found no such phrase. The actual current sentence is milder ("Use `workspace_edit` for exact string replacements — it's the safest editing tool because it fails if the match is ambiguous."). So this is an **improvement** (add the copy-fresh-read lesson, add the git-checkout rule, and stop framing the tool as "exact string replacement" only), not a fix of a steer-away.
- **Applied to both live copies** (`data/decafclaw/workspace/vault/agent/DecafClaw.md` and `.../agent/pages/DecafClaw.md`) after Les's sign-off on 2026-10-10. Both are agent data under `data/`, not tracked in the repo.

**Verification — manual:**
- [x] Les reviewed and approved the proposed new text ("Yes, apply + push + open PR")
- [x] Both live pages updated; grep confirms the old "it's the safest editing tool because it fails if the match is ambiguous" sentence is gone and the new copy-fresh-read + git-checkout lesson is present at both locations

---

## Sequencing & exclusions

- Sequence: this PR first; #928 (move the AGENT.md guide into per-tool `prompt_guidelines`) after — it pins this issue's routing.
- NOT doing: new `workspace_replace_block`; CRLF / near-match (#997); any `classify_tools` / `max_active_tools` / budget change; AGENT.md→prompt_guidelines migration (#928).

## Rollback

Each phase is one commit; revert any phase independently. The vault note (Phase 5) is a live-data edit guarded behind Les's sign-off and is not committed to the repo.
