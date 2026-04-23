# Session Notes

## 2026-04-23

- Session started. Tracking [#292](https://github.com/lmorchard/decafclaw/issues/292) Phase 2.
- Phase 1 recap: inbox + bell UI + 3 producers (heartbeat / schedule / background) shipped in #293.
- Phase 2 focus: **channel adapter abstraction** + first concrete adapter (Mattermost DM, #96).
- Agreed to run a lightweight session — brainstorm + spec, then execute in phases with commits per adapter. Skip the full plan doc unless the brainstorm surfaces unexpected complexity.

## Brainstorm — Q&A trail

- **Q1: Adapter interface shape.** Considered (A) lightweight function registry vs (B) typed Protocol+registry. **Landed on: EventBus subscribers** — neither A nor B. Natural fit with existing bus; no new abstraction; web UI could also subscribe later for WebSocket push.
- **Follow-on: shift the JSONL write into an event subscriber too?** Pushed back. Inbox is the durable record; channels are best-effort. Conflating them breaks the "`notify()` returns a persisted record" contract and the failure-mode asymmetry (inbox failures should raise, channel failures shouldn't). **Decision: inbox write stays synchronous in `notify()`, event publishes after.**
- **Q2: Routing policy.** Per-adapter internal filter (A) vs central router (B) vs declarative filters on subscribe (C). **Landed on A** — matches decentralized subscriber model, keeps policy close to capability, minimal boilerplate.
- **Q3: Dispatch timing.** Subscribers block `notify()` (A) vs fire-and-forget via `asyncio.create_task` (B). **Landed on B** — producers shouldn't wait on delivery; inbox already guarantees durability; errors stay log-local.
- **Q4: Mattermost adapter specifics.**
  - 4a: Recipient — single configurable username, skip if empty. No "DM whole channel" for this adapter.
  - 4b: Client access — **closure-over-client at subscribe time** (runner.py wires adapter with `MattermostClient` ref). Not a module-level singleton, not a recursive `send_dm` event.
- **Q5: Config surface + module layout.**
  - 5a: **Typed dataclasses in `config_types.py`** (mirrors providers/models). Not skill-style per-module config, not bag-of-dicts.
  - 5b: **`src/decafclaw/notification_channels/` package**, one file per adapter.
  - Default `min_priority` for Mattermost DM: **`"high"`**.
- **Q6: Observability.** Log-only for this session (i). Meta-notifications (ii) rejected — loop risk. `health_status` integration (iii) deferred to a follow-on once 2+ adapters exist.
- **Event payload shape confirmed:** full `record.to_dict()` — adapters don't fetch from inbox.

All six questions landed in ~15 minutes; spec finalized, skipping `plan.md` as agreed.
