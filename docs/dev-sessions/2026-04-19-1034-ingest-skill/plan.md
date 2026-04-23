# Plan: `/ingest` skill

Spec: [spec.md](./spec.md). Decisions locked after brainstorm.

Branch: `session/ingest-skill` (checked out).

## Pre-flight (verified or deferred)

- Bundled skill discovery: auto via `src/decafclaw/skills/__init__.py` (confirmed in postmortem session).
- `context: inline` + `$ARGUMENTS` semantics: confirmed in postmortem session.
- `vault_write` param name is `page`, auto-creates parent folders: confirmed.
- Eval harness now dispatches `/` commands end-to-end (shipped in PR #284).
- Workspace-file fetch: `workspace_read` tool name needs verification during Phase 1.
- Attachment syntax for `$ARGUMENTS`: the spec assumes `@attachment:foo.pdf` may not be the right convention. Alternative: empty `$ARGUMENTS` + `list_attachments` triggers attachment flow. Verify during Phase 1 — if the convention is unclear, drop any `@attachment:` syntax from the body and rely on `list_attachments` heuristic.
- Binary attachments (PDF, images) in `get_attachment`: behavior unverified. If bytes-only, the skill body acknowledges the limitation. If PDF text extraction exists, incorporate.
- Les has `make dev` running; I will not start a second bot instance.

## Phase 1: Skill scaffold

1. Create `src/decafclaw/skills/ingest/SKILL.md`.
2. Frontmatter:
   ```yaml
   name: ingest
   description: Fetch a URL, workspace file, or attachment and integrate its content into the vault — one primary page plus linked updates
   user-invocable: true
   context: inline
   required-skills:
     - tabstack
   allowed-tools: tabstack_extract_markdown, web_fetch, workspace_read, list_attachments, get_attachment, vault_search, vault_read, vault_write, vault_list, vault_backlinks, current_time
   ```
3. Before writing the body, grep to confirm tool names: `workspace_read` / `workspace_read_file` / other. Adjust `allowed-tools` to match reality.
4. Body follows the synthesis workflow in the spec, written in second-person imperative (inline context makes the body a user-voiced instruction). Keep prose tight — this is a control surface.

**Verify**: `make lint`; skill shows up in the catalog (needs agent restart in dev).

**Commit**: `feat: bundle ingest skill (scaffold)`.

## Phase 2: Eval — workspace-file ingest

The workspace-file path is the one we can test reliably in evals. URL fetching is flaky; attachment flow needs web UI plumbing. Focus the eval on workspace-file ingest.

1. Create `evals/ingest.yaml`.
2. Eval setup should seed a workspace file with known content. Check whether `_setup_workspace` supports this; if not, extend it with a `workspace_files:` fixture (dict of `path: content`). This is a small harness change on the same justification as the `dispatch_command` fix — but only if needed. First preference: work with what we have.
3. Eval assertion: after `/ingest workspace/<fixture>.md`, check:
   - At least one `vault_write` call.
   - Response contains a "Primary page" reference (by regex).
   - Response contains a `## Sources` section written to the vault (check via `workspace_read` in a follow-up turn, or assert on the response text).
   - No apology/error phrasing.
   - `max_tool_calls` with generous headroom (ingest is multi-step).
4. Run: `uv run python -m decafclaw.eval evals/ingest.yaml`.
5. Tune skill body if assertions fail; the body is the control surface.

**Verify**: eval passes.

**Commit**: `test: eval case for ingest skill` (possibly combined with workspace_files fixture addition).

## Phase 3: Docs

1. Create `docs/ingest-skill.md` describing: purpose, input shapes, fetch mechanism selection, output folder policy, relationship to `linkding-ingest` / `mastodon-ingest` / `garden`, deferred pairings (#287/#288/#289).
2. Add an entry to `docs/index.md` in the Tools & Skills section.
3. Update `CLAUDE.md` bundled skills list.

**Verify**: `make lint` still clean.

**Commit**: `docs: ingest skill`.

## Phase 4: PR + session notes

1. Fill in `notes.md` with what needed tuning, any surprises, what's still open.
2. Push, open PR with a test-plan checklist covering:
   - Workspace file path (eval-covered)
   - URL fetch (Les tests live; tabstack happy path + failure fallback to web_fetch)
   - Attachment upload via web UI
   - Focus arg effect on output
   - Verify no writes outside `agent/pages/`
3. Link deferred issues (#287, #288, #289) in the PR body so their relation to this skill is traceable.

## Out of scope

- Multiple URLs per invocation (see spec non-goals).
- Pasted text as source.
- `log.md` / `index.md` integration (#289 / #288).
- Cross-graph link backfill (#287).
- Scheduled operation (owned by aggregator-ingest skills).

## Risks

- **Skill body drift**: prompt wording is the whole feature. Eval is the guardrail. Expect 2–3 body tunings.
- **Fetch-mechanism fallback**: if tabstack returns a soft error that looks like valid content, we write garbage. Step 3 of the workflow ("verify the fetched text looks like real content") is the mitigation. If this proves unreliable in Phase 2, add a regex check for common error strings.
- **Folder sprawl**: "agent chooses a folder" can produce many singleton folders. Document the preference for existing folders; garden will eventually consolidate. Acceptable v1 risk.
- **Multi-page updates in one turn**: exceeding the 5-page cap is a drift risk if the source is dense. Watch in Phase 2; tighten wording if it over-runs.
- **Binary attachments**: if `get_attachment` returns bytes for PDFs, the skill fails silently. Phase 1 verification should make this visible.
