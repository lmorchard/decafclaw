# Notes: self-improving vault (#197)

Session working log. Final summary goes here before the PR.

## Measurement (2026-07-23, post-Phase-5, HEAD abe4ff1)

Because all five phases ran back-to-back in one continuous session, we never
captured a true pre-arc baseline snapshot of `make retrieval-report` or a
"before" eval run — there was no stable checkpoint between phases where
telemetry had accrued real traffic. What follows is the best HONEST
measurement achievable now, done as a dedicated Measurement step rather than
faked as a before/after comparison that never actually happened.

### Eval-framework assessment (can we seed vault pages with frontmatter?)

Read `src/decafclaw/eval/runner.py` (`_setup_workspace`) and
`docs/eval-loop.md`. Findings:

- `setup.workspace_files` **can** seed literal vault page files, frontmatter
  and all (already used this way by `evals/vault.yaml` and
  `evals/vault-frontmatter.yaml`). The file lands on disk with real
  `---\nsummary: ...\n---` YAML — genuinely frontmatter-rich.
- `setup.memories` seeds **journal** entries only, and auto-indexes them into
  `embeddings.db` when `search_strategy: semantic` — this is the *only*
  setup path that pre-populates the embedding index. It has no equivalent
  for vault pages.
- Consequence: a vault **page** seeded via `workspace_files` exists on disk
  with real frontmatter, but is **not** automatically embedded —
  `_setup_workspace` never calls `index_entry`/`build_composite_text` for
  pages the way it does for journal `memories`. It's invisible to
  *proactive* (embedding-based) retrieval until something indexes it.
- What DOES consume frontmatter during retrieval, confirmed by reading the
  code (not assumed): `memory_context._enrich_results` re-reads
  `importance`/`summary` **live from the page file on disk** at
  retrieval-composition time (not baked into the embedding), so importance
  really does feed the composite `w_importance` scoring term whenever a
  page is a retrieval candidate at all. `frontmatter.build_composite_text`
  prepends summary/keywords/tags to the body before embedding, so
  frontmatter genuinely shapes the embedding text too, at *index* time.
  Both mechanisms are real, not aspirational — they're just not reachable
  from a `workspace_files`-only seed.
- One real lever that closes the gap without any framework change:
  `tool_vault_search`'s lazy auto-reindex (`embeddings.search_similar`) —
  the first semantic search against an empty index runs a real
  `reindex_vault()` pass over **whatever's on disk**, including
  `workspace_files`-seeded pages, using the real composite-text builder.
  So a `vault_search` call in turn 1 gives us genuine embeddings for a
  frontmatter-rich seeded page, and a later turn can then exercise real
  proactive (embedding-based) retrieval against them.

**Verdict:** seeding *files* with frontmatter is fully supported today.
Seeding *pre-built embeddings for those files* (needed for a controlled,
LLM-write-independent A/B of frontmatter-rich vs. bare content under equal
distractor load) is **not** supported without a framework extension — see
follow-up below.

### `evals/retrieval-quality.yaml` — built, run, real output

Two-turn eval (see file comments for full reasoning): seeds one vault page
with rich frontmatter (`summary`/`keywords`/`tags`/`importance: 0.9`) via
`workspace_files`. Turn 1 asks the agent to search the vault for the topic
(forces `vault_search` → real lazy auto-reindex → composite-text embedding
of the seeded page, frontmatter included). Turn 2 asks about a *different*
detail from the same page, not restated in turn 1's response (so the model
can't just recall it from conversation history) and asserts **no** vault
tool call — i.e. the answer must come from real proactive/composite-scored
retrieval, not an explicit search.

Real run (`uv run python -m decafclaw.eval evals/retrieval-quality.yaml
--verbose`, model `vertex-gemini-flash` via the LiteLLM proxy), 3 consecutive
runs, all PASS, no wording changes needed:

```
[1/1] vault page frontmatter is embedded and proactively retrieved later . PASS  (8.2s, 6413 tokens, 1 tools)
         Response: The shift lead has 15 minutes to acknowledge the alert before it auto-escalates to the VP of Engineering.
1 tests, 1 passed, 0 failed
```

(Repeated: 9.3s/6416 tokens and 9.4s/6596 tokens, both PASS, 1 tool call —
`vault_search` in turn 1, zero tool calls in turn 2 as asserted.)

**Honest limitation:** this proves the real production pipeline (frontmatter
→ composite embedding → proactive retrieval) works end-to-end for a page
that does get indexed. It is **not** a controlled A/B proving frontmatter
*improves* retrieval over bare content — with only one page in the vault and
no distractor crowding, we can't isolate whether `importance: 0.9` was
load-bearing for the pass, or whether a bare page would have passed too.
See follow-up below.

**Follow-up (not built here, filed per task scope):** a rigorous
frontmatter/importance A/B needs a framework extension — a
`setup.vault_pages` (or similar) fixture path in `eval/runner.py` that seeds
page files **and** deterministically computes/writes their composite
embeddings at setup time (mirroring what `tool_vault_write` does on a real
write), the way `setup.memories` already does for journal entries. That
would let a test seed two otherwise-identical pages (one frontmatter-rich,
one bare) plus a shared distractor pool, and assert the frontmatter-rich one
wins under budget pressure while the bare one doesn't.

### Tool-choice: `vault_recompute_importance` vs. `vault_update_frontmatter`

Added two cases to `evals/tool_choice/core_overlaps.yaml`:
`recompute-importance-vs-frontmatter-whole-vault` (deterministic, vault-wide
recompute from measured signals → `vault_recompute_importance`, near-miss
`vault_update_frontmatter`/`vault_write`) and
`frontmatter-vs-recompute-single-page-importance` (an explicit value on one
named page → `vault_update_frontmatter`, near-miss
`vault_recompute_importance`).

`uv run python -m decafclaw.eval.tool_choice evals/tool_choice/` — both new
cases **PASS**, 0% swap rate in both directions
(`vault_recompute_importance ↔ vault_update_frontmatter`,
`vault_recompute_importance ↔ vault_write`). Overall: 25/30 (83%) — the 5
failures (`workspace-write-vs-canvas-save-blog-post`,
`tabstack-automate-vs-research-form-fill`,
`tabstack-research-vs-automate-multi-source`,
`ask-choice-vs-text-deploy-target`, `delegate-vs-activate-research`) are
pre-existing and unrelated to #197 — confirmed by running the suite against
the pre-change tree (`git stash`), which shows the identical 5 failures at
23/28 (82%) before these two cases were added.

### Proxy snapshot: `make retrieval-report`

```
# Retrieval telemetry report

Total candidate appearances: 0 across 0 pages

file_path                                retrieved  included   inc% drop-score drop-budget
------------------------------------------------------------------------------------------

## Vault health
  Pages: 247
  With importance frontmatter: 1 (0%)
  Missing importance frontmatter: 246
  Graph orphans (zero inbound links): 136
```

As expected and noted up front: `Total candidate appearances: 0` because the
`retrieval_telemetry.jsonl` stream (Phase 0) only accrues from real
conversation traffic, and this worktree's `.env` points at the main clone's
`data/` directory, which hasn't had real interactive traffic hit the new
telemetry path yet. The **production before/after delta on this report is a
deploy-and-wait item** — it needs roughly a week of real Mattermost/web-UI
traffic post-deploy before `retrieval_telemetry.aggregate()` has anything to
show, and only then will `vault_recompute_importance`'s weekly garden sweep
have real retrieval-frequency + inbound-link signal to compute against
(right now 246 of 247 pages have no `importance` frontmatter at all — the
backfill CLI from Phase 3 hasn't been run against production data yet
either).

## Phase log

- Phase 0 — retrieval telemetry: done (`2b42907`) — `retrieval_telemetry.jsonl` stream + `make retrieval-report`
- Phase 1 — vault_update_frontmatter: done (`f1140ae`, gated `742efa4`) — merge tool for single-page frontmatter fields
- Phase 2 — dream generation: done (`6f9d55a`) — dream consolidation now calls `vault_update_frontmatter` after writing/revising a page
- Phase 3 — backfill CLI: done (`5ba079a`) — one-shot CLI to backfill frontmatter across existing pages (not yet run against production vault — see proxy snapshot above)
- Phase 4 — backlink index: done (`ef4a2ed`, fix `4ba7334`) — persistent inbound-link index (`backlinks.json`)
- Phase 5 — importance recompute: done (`f010ca6`) — deterministic `vault_recompute_importance` (garden skill) driven by retrieval frequency + inbound links
- Measurement — this step: `evals/retrieval-quality.yaml` (pipeline-validation eval, 3/3 real runs PASS), `evals/tool_choice/core_overlaps.yaml` (+2 cases, both PASS, pre-existing 5 failures unaffected), `make retrieval-report` snapshot captured (sparse — deploy-and-wait for the real before/after)
