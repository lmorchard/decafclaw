# Plan: eval coverage audit

See [spec.md](./spec.md) for scope, deliverables, and acceptance criteria.

This is a research/audit session, not feature-building. Most steps produce analysis output (notes in this dir or the final audit doc), not code. Only two steps produce commits: the inline-doc-rot fix (step 7) and the audit doc itself (step 8). Issue-filing (step 10) mutates GitHub, not the repo.

## Commit / branch strategy

- Branch: `eval-coverage` (already created; spec already committed).
- Commit 1 already landed: session scaffolding + spec.
- Commit 2: inline doc/comment rot fixes (step 7).
- Commit 3: audit doc at `docs/dev-sessions/2026-04-24-0941-eval-coverage/evals-audit.md` (see Step 8 for the location decision — point-in-time artifact, not a living doc).
- No other code changes on this branch.
- Rebase against origin/main before opening the final PR.

## Steps

### Step 1: Harness capability survey

**Build on:** Already partially done during brainstorm (read `runner.py`, `memory.yaml`, `project-skill.yaml`, `docs/eval-loop.md`). This step completes it.

**Action:** Read the remaining eval harness files — `src/decafclaw/eval/__main__.py`, `src/decafclaw/eval/reflect.py`, `src/decafclaw/eval/__init__.py`. Read `Makefile` targets related to evals (`make build-eval-fixtures`, anything calling `decafclaw.eval`).

**Output:** Write findings to `notes.md` under a "Harness capabilities" section. Include:
- Supported `setup` fields (skills, memories, workspace_files, embeddings_fixture, auto_confirm)
- Supported assertions (response_contains, response_not_contains, max_tool_calls, max_tool_errors)
- CLI flags (model, verbose, concurrency, judge-model, etc.)
- Concurrency model and per-test isolation
- What's missing vs what #240 implies is needed (expect_tool-by-name, multi-model matrix, pass-rate history, cancel/effort/context-budget hooks, tool-arg inspection)

**Commit:** None — notes are scratch.

---

### Step 2: Static per-file audit

**Build on:** Step 1's harness survey.

**Action:** For each of the 6 YAML files (`ingest.yaml`, `memory.yaml`, `memory-multi-turn.yaml`, `memory-semantic.yaml`, `postmortem.yaml`, `project-skill.yaml`):
- Read the file.
- Cross-reference each test's assertions against current tool names, skill SKILL.md wording, and runner support. Tools to cross-reference against: `src/decafclaw/tools/` + `src/decafclaw/skills/*/SKILL.md` + `src/decafclaw/skills/*/tools.py`.
- For each test, classify: still-valid / stale-but-fixable (e.g. tool renamed, SKILL.md text changed) / fundamentally-broken (assertion impossible or meaningless now) / needs-runtime-evidence (can't tell without running).

**Output:** Per-file verdict table in `notes.md` under "Static audit".

**Commit:** None — notes are scratch.

**Parallelization:** This is cheap enough sequentially; all 6 files total ~250 lines. One pass in one message.

---

### Step 3: Full eval run (default model)

**Build on:** Step 2. We know what to expect.

**Action:**
- From the worktree venv: `.venv/bin/python -m decafclaw.eval evals/ --verbose`.
- Capture stdout + the resulting `evals/results/{timestamp}-{model}/results.json`.
- If widespread failures that look rate-limit-shaped (timeouts, 429s, identical failure patterns across independent tests), abort and re-run with `--concurrency 1`.
- Run in the background so we can keep working on steps 4/5/7 while it runs.

**Output:** Pass/fail summary per file in `notes.md` under "Runtime results", plus the JSON bundle path. Note the model used and wall-clock duration.

**Commit:** None — the results bundle is in `evals/results/` which is already gitignored.

**Risk:** If the harness itself is broken (crashes, doesn't start), that's its own finding — log it and fall back to static analysis only.

**`make dev` note:** Les likely has `make dev` running. The eval run uses the same LLM provider / credentials but not the same state (per-test tempdir). Shouldn't conflict. Do NOT start a second `make run` / `make dev` / `make debug`.

---

### Step 4: Failure analysis

**Build on:** Steps 2 + 3.

**Action:** For each failure from step 3, categorize:
- (a) **Genuine bit-rot** — assertion no longer matches current behavior. Inline-fix the test assertion? Or leave for the per-subsystem issue? Judgment call per case.
- (b) **Harness limitation** — test is trying to assert something the runner can't cleanly check (usually via fragile `response_contains` strings). Note as harness-gap.
- (c) **Flaky / model variance** — one-off, consider re-running sequentially to confirm.
- (d) **Real coverage bug** — tool/skill behavior has a real issue that the eval exposed. File a separate bug, not part of the audit scope.

**Output:** Failure triage table in `notes.md` under "Failure triage".

**Commit:** None.

---

### Step 5: Coverage gap walk

**Build on:** Step 1 (harness capabilities) + #240 scope list.

**Action:** Walk the 15-bullet scope of #240. For each bullet:
- Does an eval file already cover it (from step 2)? If yes, is coverage complete or partial?
- Which eval file *should* it live in (per the "one issue ≈ one eval file" model from spec)?
- Minimum viable test set — 3–5 test ideas per subsystem, without writing them in detail.
- Harness support needed — does the runner already support the assertions this subsystem needs? If not, flag the missing capability.

**Output:** Coverage gap table in `notes.md` under "Coverage gaps", grouped by proposed eval file.

**Commit:** None.

---

### Step 6: Harness gap list

**Build on:** Steps 1, 4, 5.

**Action:** Consolidate harness-level issues from steps 1, 4, 5. Expected candidates (from spec + what I already saw during brainstorm):
- `expect_tool` / `expect_tool_count_by_name` assertions
- `expect_no_tool` assertion
- Tool-args inspection (assert a specific tool was called with specific args)
- Multi-model matrix runner + combined report
- Pass-rate trend tracking over time
- Post-turn workspace state assertion (file X should exist with content Y)
- Cancel / stop-mid-turn hook
- Effort-level switching hook
- Context-budget probe (deferred tool fetched / not fetched)

**Output:** Harness-gap table in `notes.md` under "Harness gaps". Each row: title, one-line rationale, rough size estimate.

**Commit:** None.

---

### Step 7: Inline fix pass

**Build on:** Steps 1, 2, 4.

**Action:** Apply all sentence-level doc/comment rot caught so far. Known targets (from brainstorm):
- `docs/eval-loop.md`: field names (`prompt`/`expect_contains`/`expect_tool` as flat fields) → replace with actual runner format (`input`/`expect.response_contains`, no `expect_tool`). Remove the `expect_tool` row from the field table (or note it as not-yet-implemented → spin out).
- `docs/eval-loop.md`: examples should match runner format.

Plus anything discovered during steps 2/4. Bar per spec: sentence-level fixes only; anything bigger goes to the harness-gap list (step 6).

**Output:** A commit titled `docs(eval-loop): correct stale field names and examples`.

**Verification:** `grep -n "expect_contains\|expect_tool\b" docs/eval-loop.md` after the fix should return only intentional mentions (e.g. in a "not supported, tracked in #XXX" callout).

**Commit:** Yes — Commit 2.

---

### Step 8: Synthesize audit doc

**Build on:** Everything above.

**Action:** Write the final audit doc. **Location decision:** I'm going to put it at `docs/dev-sessions/2026-04-24-0941-eval-coverage/evals-audit.md` rather than `docs/evals-audit.md` — the audit is a point-in-time artifact, not a living doc. Future readers find it via git log. Anything that belongs in a living doc (e.g. updated guidance in `docs/eval-loop.md`) is inlined in step 7, not duplicated here.

Structure:
- Executive summary (pass rate across all files, count of stale tests, count of spun-out issues).
- Harness capabilities (from step 1).
- Per-file verdict (from steps 2 + 4, merged).
- Coverage gaps (from step 5).
- Harness gaps (from step 6).
- Prioritized issue list: one row per spun-out issue with title, priority, size, one-line summary. This table drives step 10.

**Commit:** Yes — Commit 3, titled `docs(eval-coverage): audit of existing evals and split-out plan`.

---

### Step 9: Verify project-board filing mechanism

**Build on:** None — can run early as background prep.

**Action:**
- `gh auth status` to confirm creds.
- Find the project's numeric ID: `gh project list --owner lmorchard` → locate "decafclaw".
- Examine an existing issue on the board (e.g. #240 itself) to see how it was added: `gh issue view 240 --json projectItems`.
- Determine the right incantation. Candidates:
  - `gh issue create --project "decafclaw" ...`
  - `gh issue create` then `gh project item-add <PROJECT_NUMBER> --owner lmorchard --url <ISSUE_URL>`
  - `gh project item-add` with `--field` flags for priority/size.
- Probe with **one** drafted issue: file it, verify it landed on the board with the right priority+size, THEN use the same pattern for the rest.

**Output:** One-line incantation in `notes.md` under "Filing recipe".

**Commit:** None.

---

### Step 10: File spun-out issues

**Build on:** Steps 8 + 9.

**Action:** For each row in the audit doc's prioritized issue list, file an issue via `gh`:
- Title per audit doc.
- Body includes: problem statement (lifted from audit), scope bullets, acceptance criteria, link back to `#240` with `(split from #240)`, link to the audit doc in the dev-session dir.
- Priority + Size fields set per audit doc (defaults P2/M).
- Add to project board using the verified incantation from step 9.

Keep a running list of filed issue numbers in `notes.md` under "Filed issues" so we can verify step 11 doesn't miss any.

**Output:** Issues filed on GitHub. Running list in `notes.md`.

**Commit:** None — this mutates GitHub, not git.

---

### Step 11: Close #240

**Build on:** Step 10.

**Action:** Post a comment on #240 with:
- One-line summary: "Split into per-subsystem issues per audit in `<path-to-audit-doc>`."
- Checklist linking every filed child issue.
- Close the issue.

Use `gh issue comment 240 --body-file -` with a heredoc, then `gh issue close 240`.

**Output:** #240 closed.

**Commit:** None.

---

### Step 12: Final sync, PR, retro

**Build on:** All prior steps.

**Action:**
- `git fetch origin && git rebase origin/main` on the `eval-coverage` branch. Resolve conflicts if any (unlikely — we only touched `docs/`).
- Push branch: `git push -u origin eval-coverage`.
- Open PR with `gh pr create`. PR body summarizes the audit and links every spun-out issue.
- **Request Copilot review** per memory: `gh pr edit <N> --add-reviewer copilot-pull-request-reviewer`.
- Move to retro phase (write `notes.md` session retrospective; squash-merge when Les approves).

**Commit:** No new commits unless rebase produces conflicts requiring resolution.

---

## Stopping rules / when to bail out

- **Harness itself broken.** If step 3 reveals the eval runner doesn't even start, shift scope: the session becomes "fix harness + audit". Flag before continuing.
- **Pass rate catastrophically low (<30%).** Not automatic stop, but reconsider whether inline fixes can patch many of these before filing per-subsystem issues. Possibly a separate "stabilize current evals" issue before layering new coverage.
- **Gh project add isn't scriptable.** If step 9 reveals board add requires interactive UI only, fall back to: file issues via `gh`, then Les adds them to the board manually from a list in the audit doc. Spec says "board add must happen"; the mechanism is negotiable.

## Open risks

- **Model cost/time of step 3.** Full eval run across 6 files (~30+ tests) at default concurrency. Budget assumption: <5 min wall-clock, <$0.50 API spend. If much longer, sequential fallback will be 4× slower — still acceptable.
- **Semantic eval requires `cat-facts-embeddings.db` fixture.** If fixture is bit-rotted vs current embeddings schema, that's a real harness-gap finding and step 3 will partially fail.
