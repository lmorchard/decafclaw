# Event Bus, Runtime Context, and Async Agent Loop — Plan

## Architecture Overview

The dependency order for this change:

1. **EventBus** — standalone, no dependencies
2. **Context** — depends on EventBus + Config
3. **Async agent loop + LLM** — depends on Context
4. **Tools updated with ctx** — depends on Context
5. **Tabstack async + progress events** — depends on async tools + Context
6. **Subscribers (Mattermost + terminal)** — depends on all of the above
7. **Wire everything together in `__init__.py`** — final integration

Each step builds on the previous and ends with working (testable) code.

---

## Step 1: EventBus

Create `src/decafclaw/events.py` with the `EventBus` class. This is a standalone module with no dependencies on existing code.

### Prompt

```
Create a new file src/decafclaw/events.py with an EventBus class.

Requirements:
- async def publish(self, event: dict) — calls all subscribers with the event.
  Subscribers can be sync or async callables. Await async ones, call sync ones directly.
  Catch and log any exceptions from subscribers — never propagate.
- subscribe(self, callback) -> str — registers a callback, returns a subscription ID (use uuid4).
- unsubscribe(self, subscription_id) — removes a subscriber by ID.
- Subscribers are stored in a dict keyed by subscription ID.
- Events are plain dicts, always expected to have "type" and "context_id" keys
  (but the bus doesn't enforce this — it just passes them through).
- Use asyncio.iscoroutinefunction to detect async callbacks.
- Use logging for error reporting.

Keep it simple — no filtering, no event types, no middleware. Just pub/sub.
```

---

## Step 2: Context

Create `src/decafclaw/context.py` with the `Context` class. Depends on EventBus and Config.

### Prompt

```
Create a new file src/decafclaw/context.py with a Context class.

Requirements:
- __init__(self, config, event_bus, context_id=None) — if context_id is None,
  generate one with uuid4().hex[:12].
- config property — the Config object.
- event_bus property — the EventBus instance.
- context_id property — the unique ID string.
- fork(self, **overrides) -> Context — creates a new Context that shares the same
  event_bus as the parent but gets a new context_id. If overrides include "config",
  use that instead of the parent's config (for future multi-agent support).
  Any other keyword args are stored as attributes on the new context.
- async def publish(self, event_type: str, **kwargs) — convenience method.
  Builds a dict with {"type": event_type, "context_id": self.context_id, **kwargs}
  and calls await self.event_bus.publish(event).

Import Config from decafclaw.config and EventBus from decafclaw.events.
```

---

## Step 3: Make the agent loop async

Convert `agent.py` to use async. This step does NOT add events yet — it just makes the functions async so the event loop stays free. Also convert `llm.py` to async.

### Prompt

```
Convert the agent loop and LLM client to async. Do NOT add event publishing yet —
this step is purely about making the functions async.

File: src/decafclaw/llm.py
- call_llm becomes async def call_llm(config, messages, tools=None).
- Replace httpx.post() with an async call. Use httpx.AsyncClient as a context manager
  inside the function (keep it simple, no persistent client for now).
- Everything else stays the same.

File: src/decafclaw/agent.py
- run_agent_turn becomes async def run_agent_turn(ctx, user_message, history).
  It now takes a Context instead of Config. Access config via ctx.config.
- await call_llm(ctx.config, messages, tools=...) for each LLM call.
- await execute_tool(ctx, name, arguments) for each tool call.
- run_interactive becomes async def run_interactive(ctx).
  Use await asyncio.to_thread(input, "you> ") for blocking input.
  await run_agent_turn instead of calling it synchronously.

File: src/decafclaw/tools/__init__.py
- execute_tool becomes async def execute_tool(ctx, name, arguments).
- Use asyncio.iscoroutinefunction(fn) to check if the tool is async.
  If async: await fn(ctx=ctx, **arguments).
  If sync: await asyncio.to_thread(fn, **arguments).
  (Sync tools don't get ctx yet — that comes in Step 4.)
- Import asyncio.

File: src/decafclaw/__init__.py
- In _run_mattermost's on_message: create a forked context for the request,
  call await run_agent_turn(req_ctx, text, history) instead of the sync version.
- In main(): if not Mattermost mode, use asyncio.run(run_interactive(ctx))
  where ctx is created from config + a new EventBus.
- Create the app-level Context in main() or _run_mattermost, passing config
  and a new EventBus instance.

After this step, the app should work exactly as before but fully async internally.
```

---

## Step 4: Add ctx parameter to all tool functions

Update every tool function to accept `ctx` as a parameter. No publishing yet — just threading ctx through.

### Prompt

```
Add a ctx parameter to all tool functions. No event publishing yet — just
plumbing ctx through so the next step can use it.

File: src/decafclaw/tools/core.py
- tool_shell(ctx, command: str) -> str
- tool_read_file(ctx, path: str) -> str
- tool_web_fetch(ctx, url: str) -> str
- No other changes to these functions.

File: src/decafclaw/tools/tabstack_tools.py
- All tool functions gain ctx as the first parameter:
  tool_tabstack_extract_markdown(ctx, url), tool_tabstack_extract_json(ctx, url, json_schema),
  tool_tabstack_generate(ctx, url, json_schema, instructions),
  tool_tabstack_automate(ctx, task, url=None), tool_tabstack_research(ctx, query, mode="balanced").
- No other changes yet.

File: src/decafclaw/tools/__init__.py
- execute_tool now passes ctx to all tools uniformly:
  If async: await fn(ctx=ctx, **arguments)
  If sync: await asyncio.to_thread(fn, ctx, **arguments)
  Note: for sync tools via to_thread, pass ctx as a positional arg since
  to_thread works with positional args. Use functools.partial if cleaner.

After this step, all tools receive ctx but don't use it yet. App still works as before.
```

---

## Step 5: Add event publishing to the agent loop

The agent loop now publishes `llm_start`, `llm_end`, `tool_start`, and `tool_end` events.

### Prompt

```
Add event publishing to the agent loop. The agent loop publishes lifecycle events
via ctx.publish().

File: src/decafclaw/agent.py — in run_agent_turn:
- Before each call_llm: await ctx.publish("llm_start", iteration=iteration + 1)
- After each call_llm: await ctx.publish("llm_end", iteration=iteration + 1)
- Before each execute_tool: await ctx.publish("tool_start", tool=fn_name, args=fn_args)
- After each execute_tool: await ctx.publish("tool_end", tool=fn_name)

No subscribers are wired up yet, so these events go nowhere — but the structure is in place.
```

---

## Step 6: Convert Tabstack streaming tools to async with progress events

Switch from `Tabstack` to `AsyncTabstack` and make the streaming tools async with `tool_status` events.

### Prompt

```
Convert Tabstack streaming tools to async and add progress event publishing.

File: src/decafclaw/tools/tabstack_tools.py:

1. Change init_tabstack to use AsyncTabstack instead of Tabstack:
   from tabstack import AsyncTabstack
   _client: AsyncTabstack | None = None
   Initialize with AsyncTabstack(**kwargs).

2. Convert tool_tabstack_automate to async:
   - async def tool_tabstack_automate(ctx, task, url=None) -> str
   - stream = await _get_client().agent.automate(**kwargs)
   - async for event in stream: ...
   - On each event with a message, publish:
     await ctx.publish("tool_status", tool="tabstack_automate", message=msg)
   - Keep the existing final answer extraction logic.

3. Convert tool_tabstack_research to async:
   - async def tool_tabstack_research(ctx, query, mode="balanced") -> str
   - stream = await _get_client().agent.research(query=query, mode=mode)
   - async for event in stream: ...
   - On each event with a message, publish:
     await ctx.publish("tool_status", tool="tabstack_research", message=msg)
   - Keep the existing report extraction logic.

4. Non-streaming tools (extract_markdown, extract_json, generate):
   These can also become async since AsyncTabstack methods are async.
   Convert them to async def and await the client calls.
   They don't need to publish tool_status — agent loop's tool_start is enough.

5. The tool registry (TABSTACK_TOOLS dict) now contains a mix of async functions.
   execute_tool already handles this via iscoroutinefunction check (from Step 3).

After this step, streaming tools publish progress events, but no one is listening yet.
```

---

## Step 7: Mattermost progress subscriber

Wire up the Mattermost subscriber that edits the placeholder on progress events.

### Prompt

```
Add a Mattermost progress subscriber that edits the placeholder message
based on event bus events.

File: src/decafclaw/__init__.py — in _run_mattermost's on_message:

1. After creating the placeholder and forking the request context, define
   an async subscriber callback that closes over placeholder_id and the
   Mattermost client:

   async def on_progress(event):
       if event.get("context_id") != req_ctx.context_id:
           return
       event_type = event.get("type")
       if event_type == "tool_status":
           await client.edit_message(placeholder_id, event["message"])
       elif event_type == "tool_start":
           tool_name = event.get("tool", "tool")
           await client.edit_message(placeholder_id, f"Running {tool_name}...")
       elif event_type == "llm_start":
           await client.edit_message(placeholder_id, "Thinking...")

2. Subscribe before calling run_agent_turn:
   sub_id = req_ctx.event_bus.subscribe(on_progress)

3. After run_agent_turn returns, unsubscribe:
   req_ctx.event_bus.unsubscribe(sub_id)

4. Use try/finally to ensure unsubscribe happens even on errors.

5. The final placeholder edit (replacing with the response) stays as-is after
   the subscriber is removed.

After this step, Mattermost shows live progress during tool execution.
```

---

## Step 8: Terminal progress subscriber

Add progress display in interactive mode.

### Prompt

```
Add a terminal progress subscriber for interactive mode.

File: src/decafclaw/agent.py — in run_interactive:

1. Define a sync subscriber callback that prints status:

   def on_progress(event):
       event_type = event.get("type")
       if event_type == "tool_status":
           print(f"  [{event.get('tool', 'tool')}] {event['message']}")
       elif event_type == "tool_start":
           print(f"  [running {event.get('tool', 'tool')}...]")
       elif event_type == "llm_start" and event.get("iteration", 1) > 1:
           print("  [thinking...]")

   Note: only show "thinking" on iteration > 1 (the first thinking is implicit
   from the user submitting input).

2. Subscribe at the start of run_interactive:
   sub_id = ctx.event_bus.subscribe(on_progress)

3. Unsubscribe if the user quits (in the finally/cleanup).

After this step, both Mattermost and terminal show live tool progress.
```

---

## Step 9: Smoke test and cleanup

Verify everything works end-to-end and clean up.

### Prompt

```
Review all changes for consistency and do a final cleanup pass:

1. Make sure all imports are correct and there are no circular dependencies.
2. Verify the Mattermost flow end-to-end:
   - Message received → placeholder sent → context forked → subscriber registered →
     agent turn runs → events published → placeholder edited → response replaces placeholder →
     subscriber removed.
3. Verify the interactive flow:
   - User types → subscriber prints progress → response printed.
4. Check that the initial "Thinking..." placeholder is still sent before subscribing
   (so there's no gap where the user sees nothing).
5. Make sure error handling is solid:
   - Subscriber exceptions logged but don't kill the agent.
   - Unsubscribe always happens (try/finally).
6. Run the app in both modes and verify it works.
7. Lint and fix any issues.
```

---

## Implementation Order Summary

| Step | Files Changed | What It Does |
|------|--------------|--------------|
| 1 | `events.py` (new) | EventBus pub/sub |
| 2 | `context.py` (new) | Context with fork + publish helper |
| 3 | `agent.py`, `llm.py`, `tools/__init__.py`, `__init__.py` | Make everything async |
| 4 | `tools/core.py`, `tools/tabstack_tools.py`, `tools/__init__.py` | Thread ctx through tools |
| 5 | `agent.py` | Agent loop publishes lifecycle events |
| 6 | `tools/tabstack_tools.py` | Async streaming + progress events |
| 7 | `__init__.py` | Mattermost progress subscriber |
| 8 | `agent.py` | Terminal progress subscriber |
| 9 | All | Smoke test and cleanup |

## Risk Notes

- **Step 3 is the biggest change** — touching 4 files and converting sync→async. If something breaks, it'll be here. Test after this step before moving on.
- **Step 6 depends on AsyncTabstack behaving like Tabstack** — the SDK should be compatible but watch for subtle differences in return types or error handling.
- **Mattermost API rate limits** — not handled in this session (out of scope), but if placeholder edits are too frequent, Mattermost may throttle. Monitor during testing.
