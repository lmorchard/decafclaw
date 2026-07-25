# Spec — `wiki-editor#reload()` ignores `saveEndpoint`

Closes [#666](https://github.com/lmorchard/decafclaw/issues/666).

## Problem

`wiki-editor.js`'s `#reload()` fetches a hardcoded URL:

```js
const res = await fetch(`/api/vault/${encodePagePath(this.page)}`);
```

It ignores the component's own `saveEndpoint`, which the save and force-save
paths both honor. Three hosts share the component with three endpoints:

| Host | `save-endpoint` |
| --- | --- |
| `wiki-page.js` | `/api/vault/` (default) |
| `schedule-page.js` | `/api/schedules/` |
| `config-panel.js` | `/api/config/files/` |

`#reload()` backs the **Reload** button in the conflict banner (`_status ===
'conflict'`). On a schedule or config file, resolving a 409 by clicking Reload
fetches a *vault page* named after that schedule/config file — 404, or (worse)
an unrelated vault page's body silently loaded into the wrong editor.

## Correction to the issue's premise

The issue states "the vault endpoint returns `body` while schedules and config
return `content`." That is not what main returns. Actual GET shapes:

| Endpoint | Body field | Envelope |
| --- | --- | --- |
| `/api/vault/{page}` | `body` | flat |
| `/api/config/files/{path}` | `content` | flat |
| `/api/schedules/{name}` | `body` | **nested under `schedule`** |

So pointing the fetch at `saveEndpoint` is necessary but not sufficient:
`schedules_get` returns `{"schedule": {...}}`, and `data.content` /
`data.body` are both `undefined` at the top level. The existing
`data.body ?? data.content ?? ''` fallback does not reach into the envelope.

## Decision: normalize server-side

Add top-level `body` and `modified` aliases to the `schedules_get`
response. This mirrors the alias `schedules_update` already applies twenty
lines above in the same file — that handler flattens `modified` to the top
level with the comment *"wiki-editor reads data.modified at the top level of
the PUT response."* The GET side needs the same treatment for the same
consumer.

Rejected alternative: unwrap the envelope client-side
(`const doc = data.schedule ?? data`). It works, but bakes knowledge of one
host's response shape into a component shared by three, and contradicts the
established direction of adaptation in this codebase (server adapts to the
wiki-editor contract, not the reverse).

## The wiki-editor GET contract

After this change, all three endpoints satisfy one rule, which the component
can rely on without special-casing:

> A `GET {saveEndpoint}{page}` returns the editable markdown at the top level
> as `body` (preferred) or `content`, and its mtime as `modified`.

Aliasing to `body` rather than `content` puts schedules on the same shape as
the vault endpoint, so two of three hosts hit the contract's preferred branch
and only config relies on the `content` fallback.

## Scope

- `schedules_get` gains top-level `body` + `modified`.
- `#reload()` fetches `${this.saveEndpoint}${encodePagePath(this.page)}`.
- Regression coverage: a Python test for the schedules GET contract, and a
  `wiki-editor` component test per host asserting the reload URL.
- `docs/web-ui.md` records the GET contract alongside the existing PUT one.

## What we're NOT doing

- Not flattening `schedules_get` outright (would break `schedule-page.js`,
  which reads `data.schedule`) — the envelope stays, aliases are additive.
- Not touching the config endpoint; it already satisfies the contract.
- Not changing `#save()` / `#forceSave()`; they already use `saveEndpoint`.
- Not adding evals — no LLM-visible behavior changes.
- Not fixing the `modified: null` case for a config file that has never been
  written (bundled default). Reload sets `this.modified = null`, the next save
  sends no mtime, and the server skips the conflict check. That is the same
  state as a fresh page load of an unwritten config file, so reload does not
  make it worse.
