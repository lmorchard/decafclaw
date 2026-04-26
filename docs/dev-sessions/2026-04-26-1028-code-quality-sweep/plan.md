# Plan — code-quality sweep

## Approach

One branch, one PR, multiple commits (one per phase). Each phase is mechanical and independently green; if one breaks tests, it's easy to bisect.

## Phases

### Phase 1 — `getattr` for declared fields → direct access

Files touched:
- `interactive_terminal.py`, `context_composer.py`, `commands.py`, `agent.py`, `schedules.py`, `eval/runner.py`, `tools/tool_registry.py`, `tools/delegate.py`, `tools/skill_tools.py`, `tools/health.py`, `memory_context.py`

For each replacement, confirm the attribute is declared on the type with a sensible default, so the fallback was redundant. Where the variable name was used both with `getattr(...)` and direct access in the same module, prefer the direct form.

Special cases:
- `agent.py:382` (`getattr(ctx, "manager", None)`) — `Context.manager` defaults to `None` already, so direct access is a no-op semantic-wise.
- `delegate.py:59` already uses direct `parent_ctx.context_id` as a fallback for `event_context_id` — fine.

### Phase 2 — Remove dead `_skill_shutdown_hooks` storage

Investigation showed the hook is *registered* in `skill_tools.py:205–209` but never *read* anywhere — no caller iterates `ctx._skill_shutdown_hooks`. There's no skill-deactivation lifecycle yet, so the hook is orphaned.

The smallest correct fix is to delete the dead storage block (5 lines), which simultaneously removes the convention violation (hasattr + setattr on an undeclared attr). The single skill that defines a `shutdown()` callback (`claude_code/tools.py:947`) stays in place as a marker for if/when deactivation is implemented.

### Phase 3 — `except: pass` → logged debug

For each site, replace with `except Exception as exc: log.debug(...)` (or specific exception class where one is already named, e.g., `except KeyError as exc: log.debug(...)`). Preserve the existing comment as part of the log message where it adds context.

Where `log` doesn't already exist in the module, import `logging` and define `log = logging.getLogger(__name__)` if not already present.

### Phase 4 — Extract notification-priority helpers

Create or extend `notification_channels/__init__.py` with:

```python
PRIORITY_ORDER = {"low": 0, "normal": 1, "high": 2}
PRIORITY_GLYPH = {"low": "·", "normal": "🔔", "high": "⚠️"}

def meets_priority(record_priority: str, min_priority: str) -> bool:
    return (PRIORITY_ORDER.get(record_priority, 1)
            >= PRIORITY_ORDER.get(min_priority, 1))
```

Update `vault_page.py`, `mattermost_dm.py`, `email.py` to import from there. Drop their local copies.

### Phase 5 — Async-safe attachment read in `mail.py`

```python
data = await asyncio.to_thread(path.read_bytes)
```

(Adds `import asyncio` if not present.)

### Phase 6 — Verify

- `make lint` — quick compile-check
- `make typecheck` — pyright
- `make test` — full suite
- Spot-check: `grep -rn "getattr(.*discovered_skills" src/` returns no hits in the in-scope files.

### Phase 7 — Commit per phase, push, PR

Commit messages follow project style (terse, imperative). Open PR against `main` with summary listing the five categories. Request Copilot review.

## Self-review of plan

What could go wrong?

- **`Context` field name**: dropping the underscore on `_skill_shutdown_hooks` is a small renaming. There may be tests that read the underscored name. Will grep before changing.
- **`ctx.manager` direct access vs `getattr`**: `Context.manager: Any = None` is set to None by default and assigned by `ConversationManager`. If tests build a bare `Context` without `ConversationManager`, direct access still returns `None`. Same semantics — safe.
- **`config.discovered_skills`**: if any test builds a `Config` instance without going through `load_config`, the dataclass default already gives `[]`. Safe.
- **`config.relevance`**: declared with `field(default_factory=RelevanceConfig)`; always present. Safe.
- **`except KeyError: pass` in `llm/__init__.py:127`** — the pattern is "try named default, fall through to default registry." Replacing with `except KeyError as exc: log.debug(...)` is a behavior-equivalent change that just adds a debug log. Safe.
- **Notification channels**: tests likely construct records via the public API and assert side effects (file written, post sent). Renaming the priority helper shouldn't change side effects. Safe.
- **`mail.py to_thread`**: tests of email sending probably mock `aiosmtplib.send`. The attachment read happens before that, so changing it to `to_thread` is observable only via the event loop's responsiveness, which tests don't measure. Safe.

What is *not* covered by this plan?

- The bigger structural concerns (large functions, magic-string event types, lack of a central WebSocket message catalog) are intentionally out of scope. Document as known follow-ups in `notes.md` so they don't get lost.

## Estimated complexity

Each phase is a few minutes of mechanical editing plus running checks. Total wall time: 30–45 minutes of editing, plus test runtime.
