# Plan — #666

Branch `fix/666-wiki-editor-reload-endpoint`, worktree
`.claude/worktrees/666-wiki-editor-reload-endpoint`, based on `00334d7`
(post-#672 main).

## Phase 1 — Server: satisfy the GET contract on schedules

**Test first** (`tests/test_web_schedules_api.py`): `GET /api/schedules/{name}`
exposes `body` and `modified` at the top level, matching the values nested
under `schedule`. Expected failure: `KeyError`/absent keys.

**Fix** (`http_server.py`, `schedules_get`): mirror the aliasing comment and
shape that `schedules_update` already uses.

```python
sched = _schedule_to_dict(config, task)
# wiki-editor's reload reads body/modified at the top level (see
# schedules_update for the matching PUT-side alias)
return JSONResponse({
    "schedule": sched,
    "body": sched["body"],
    "modified": sched["modified"],
})
```

Verify `schedule-page.js` still reads `data.schedule` — the aliases are
additive, nothing is removed.

## Phase 2 — Client: reload the endpoint the editor actually saves to

**Test first** — first component test in this repo. `vitest.config.js`
already includes `components/**/*.test.js`; no config change needed.

`components/wiki-editor.test.js`:

- `vi.mock('@milkdown/kit')` so no real editor boots in jsdom. `#reload()`
  guards on `this.#editor`, so a null editor exercises the fetch path fine.
- Per host endpoint (`/api/vault/`, `/api/schedules/`, `/api/config/files/`):
  mount the element, force `_status = 'conflict'`, click the Reload button,
  assert the stubbed `fetch` received `{saveEndpoint}{page}`.
- One test per body field so the `body ?? content ?? ''` fallback is
  genuinely covered: vault/schedules return `body`, config returns `content`.

Expected failure: every URL assertion sees `/api/vault/...`.

**Fix** (`wiki-editor.js`, `#reload()`): fetch
`${this.saveEndpoint}${encodePagePath(this.page)}`, and rewrite the stale
comment — it currently documents the bug ("hardcoded to /api/vault
regardless of `saveEndpoint`") as if it were permanent.

## Phase 3 — Docs

`docs/web-ui.md`: state the wiki-editor GET/PUT contract the three hosts
share, and note the schedules aliases. Cross-check `docs/schedules.md` for a
response-shape table that needs the new keys.

## Verification

`make check` · `make test` · `make test-js`, then squash + PR with
`Closes #666`.
