# Plan: System prompt structural delimiters

Refer to `spec.md` for the section structure and acceptance criteria.

Work breaks into three ordered phases, each a separate commit. Sizing
matches the S-bucket on the tracking issue — this is a mechanical
wrapping change with tests, not an architectural rewrite.

---

## Phase 1 — Wrap `load_system_prompt` output

**What:** Rewrap the five assembled sections in
`src/decafclaw/prompts/__init__.py::load_system_prompt` so each source
gets its own XML tag. Preserve source markdown verbatim inside.

**Files:**
- `src/decafclaw/prompts/__init__.py` — replace the `sections.append(text)`
  calls with wrapped variants. Build each wrapped block via a small
  helper (`_wrap(tag, body)`) so empty body still produces `""` and the
  caller skips it naturally.
- `tests/test_prompts.py` — **new**. Covers:
  - **Default bundled load** (using the `config` fixture, no workspace
    overrides): assert all five applicable tags appear in the expected
    order — `<soul>` → `<agent_role>` → `<skill_catalog>` →
    `<loaded_skills>`. Uses the existing bundled SOUL/AGENT + the
    bundled skills the `discover_skills` scan picks up on a clean
    config (`vault`, `background`, `mcp` are always-loaded and will
    produce `<loaded_skills>` content).
  - **USER.md absent**: assert `<user_context>` tag absent.
  - **USER.md present with content** (write a file into `config.agent_path`
    for the fixture): assert `<user_context>...</user_context>` present,
    contains the content, and appears between `<agent_role>` and
    `<skill_catalog>`.
  - **Inner content preserved**: at least one assertion verifies
    source markdown (e.g. a known `##` heading in SOUL.md or an item
    from `## Available Skills`) shows up inside its wrapper intact —
    not rewritten or escaped.
  - **`<skill_catalog>` gating**: simulate zero discovered skills (by
    patching `discover_skills`) and assert `<skill_catalog>` and
    `<loaded_skills>` are both absent. Covers the empty-skill-set
    edge case even though it's unreachable in a normal run.
  - **`<loaded_skills>` per-skill structure**: assert one
    `<skill name="…">` block per always-loaded skill, attribute value
    equals the skill name, body content intact.
  - **Trust boundary preserved**: if a non-bundled skill is flagged
    always-loaded (simulate via monkeypatched `discover_skills`
    returning a skill with `location` outside the bundled dir), its
    body is NOT wrapped into `<loaded_skills>` (existing warning
    path).

**Verify:** `make lint && make test`.

### Prompt

> In `src/decafclaw/prompts/__init__.py::load_system_prompt`, wrap each
> assembled section in an XML tag per `spec.md`:
> `<soul>` for SOUL.md, `<agent_role>` for AGENT.md, `<user_context>`
> for USER.md (when present), `<skill_catalog>` for the
> `build_catalog_text` output (when non-empty), and `<loaded_skills>`
> containing one nested `<skill name="{name}">…body…</skill>` per
> bundled always-loaded skill (when at least one exists). Source
> markdown is preserved verbatim inside the tags. Empty sections still
> emit nothing — don't write empty wrappers. Factor a small `_wrap(tag,
> body)` helper if it makes the code cleaner.
>
> Add `tests/test_prompts.py` (new) covering: default load has `<soul>`
> + `<agent_role>` in order; USER.md presence gates `<user_context>`;
> skill discovery gates `<skill_catalog>`; always-loaded-skill presence
> gates `<loaded_skills>` and produces one `<skill name=…>` block per
> skill; non-bundled always-loaded skills are still excluded (trust
> boundary preserved). Use `config` fixtures already available in
> `tests/conftest.py` or equivalent; don't reinvent config scaffolding.
> `make lint && make test` must pass.

---

## Phase 2 — Wrap the deferred-tool catalog

**What:** In `src/decafclaw/tools/tool_registry.py::build_deferred_list_text`,
wrap the non-empty output in `<deferred_tools>…</deferred_tools>`. Empty
input still returns `""` (no wrapper).

**Files:**
- `src/decafclaw/tools/tool_registry.py` — one `return` change near the
  bottom of `build_deferred_list_text`.
- `tests/test_tool_registry.py` — extend existing tests:
  - Empty input still returns `""`.
  - Non-empty output is bracketed by `<deferred_tools>` / `</deferred_tools>`.
  - Inner markdown (`## Available tools`, `### Core`, `### Skills`,
    MCP server sub-sections) preserved verbatim.

**Verify:** `make lint && make test`.

### Prompt

> In `src/decafclaw/tools/tool_registry.py::build_deferred_list_text`,
> wrap the existing `"\n".join(lines)` result in `<deferred_tools>\n…
> \n</deferred_tools>`. Keep the empty-input early return producing
> `""` (no wrapper around nothing).
>
> Extend `tests/test_tool_registry.py`: add cases asserting the
> wrapper is present on non-empty output, absent on empty, and the
> inner markdown structure (`## Available tools`, `### Core`, etc.) is
> unchanged inside. `make lint && make test` must pass.

---

## Phase 3 — Docs + CLAUDE.md

**What:** Document the convention so future contributors don't undo
the wrapping by reflex. One doc page update, one CLAUDE.md bullet
nudge.

**Files:**
- `docs/context-composer.md` — the system-prompt construction section
  is already here; extend it with a short "Section delimiters"
  subsection listing the six tags (`<soul>`, `<agent_role>`,
  `<user_context>`, `<skill_catalog>`, `<loaded_skills>`,
  `<deferred_tools>`) and why (Anthropic article reference, link to
  #304).
- `CLAUDE.md` — the "System prompt from files" bullet currently says
  "SOUL.md + AGENT.md bundled in code, overridable at `data/{agent_id}/`.
  USER.md is workspace-only." Extend with a sentence noting the
  sections are wrapped in XML tags at assembly time so the model can
  navigate them.

**Verify:** Eye-check doc links and structure. No automated test.

### Prompt

> Document the system-prompt section-delimiter convention:
>
> - In `docs/context-composer.md`, add a short "Section delimiters"
>   subsection under the system-prompt construction content. List the
>   six tags (`<soul>`, `<agent_role>`, `<user_context>`,
>   `<skill_catalog>`, `<loaded_skills>`, `<deferred_tools>`) and note
>   they're emitted by `load_system_prompt` /
>   `build_deferred_list_text` respectively, reference Anthropic's
>   effective-context-engineering article, and cite #304.
> - In `CLAUDE.md`, extend the "System prompt from files" bullet with
>   one sentence: the sections are wrapped in XML tags at assembly
>   time so the model can navigate between identity, instructions,
>   per-deployment facts, available skills, loaded skill bodies, and
>   the deferred-tool catalog.
>
> No new docs pages. No automated test.

---

## Phase 4 — PR + Copilot review

**What:** Squash, push, open PR, request Copilot.

**Actions:**
1. Squash the three phase commits into one with a commit message
   referencing `Closes #304`.
2. `git push -u origin system-prompt-delimiters`.
3. `gh pr create` with Summary / Test plan. Link `Closes #304`.
4. `gh pr edit N --add-reviewer copilot-pull-request-reviewer`.
5. Verify CI green; handle any review feedback before merge.

---

## Risk register

- **Model echoes tag names in its output.** Low probability (Anthropic-
  trained models treat `<tag>` as structural by convention), but
  possible in rare cases. If it happens, strip tags at the post-
  processing layer before display. Mitigation waits for #303 eval to
  actually detect it.
- **Inner markdown headings conflict with outer wrapper.** Already
  addressed in the spec by choosing `<skill_catalog>` / `<loaded_skills>`
  instead of the collision-prone `<available_skills>` / `<active_skills>`.
- **Other prompt surfaces start drifting.** REFLECTION / MEMORY_SWEEP /
  compaction are deliberately out of scope here; #357 tracks them. The
  CLAUDE.md nudge is the canonical fix so future contributions don't
  accidentally add a new system-prompt source without wrapping it.
