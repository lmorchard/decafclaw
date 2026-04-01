# Conversation Compaction and Archival — Plan

## Phases

Two phases, each ending in a working commit point:

**Phase 1 — Foundation:** Config, token tracking, conversation archive,
basic compaction (single-shot, no chunking). The agent can archive
conversations and compact them when they exceed the token budget.

**Phase 2 — Polish:** Chunked compaction, explicit `compact_conversation`
tool, Mattermost `delete_message`, compaction event subscribers,
COMPACTION.md prompt file.

---

## Phase 1: Foundation

### Step 1: Config additions

Add compaction and archive config to `Config`.

#### Prompt

```
Add compaction config to src/decafclaw/config.py:

New fields on Config dataclass:
- compaction_llm_url: str = ""        # default: falls back to llm_url
- compaction_llm_model: str = ""      # default: falls back to llm_model
- compaction_llm_api_key: str = ""    # default: falls back to llm_api_key
- compaction_max_tokens: int = 100000 # compact when prompt_tokens exceeds this
- compaction_llm_max_tokens: int = 0  # compaction LLM's context budget (0 = use compaction_max_tokens)
- compaction_preserve_turns: int = 5  # keep this many recent turns intact

Wire in load_config:
- compaction_llm_url=os.getenv("COMPACTION_LLM_URL", "")
- compaction_llm_model=os.getenv("COMPACTION_LLM_MODEL", "")
- compaction_llm_api_key=os.getenv("COMPACTION_LLM_API_KEY", "")
- compaction_max_tokens=int(os.getenv("COMPACTION_MAX_TOKENS", "100000"))
- compaction_llm_max_tokens=int(os.getenv("COMPACTION_LLM_MAX_TOKENS", "0"))
- compaction_preserve_turns=int(os.getenv("COMPACTION_PRESERVE_TURNS", "5"))

Add helper properties for the effective compaction LLM settings:
- compaction_url -> returns compaction_llm_url or llm_url
- compaction_model -> returns compaction_llm_model or llm_model
- compaction_api_key -> returns compaction_llm_api_key or llm_api_key
- compaction_context_budget -> returns compaction_llm_max_tokens or compaction_max_tokens
```

---

### Step 2: Token tracking in call_llm

Update `call_llm` to return usage data and accept optional overrides.

#### Prompt

```
Update src/decafclaw/llm.py:

1. call_llm now accepts optional override parameters:
   async def call_llm(config, messages, tools=None,
                      llm_url=None, llm_model=None, llm_api_key=None):
   - If llm_url is provided, use it instead of config.llm_url
   - Same for llm_model and llm_api_key
   - This allows compaction to call a different LLM without building
     a temporary config

2. Return the usage dict alongside the message:
   The return dict gains a "usage" key:
   return {
       "content": message.get("content"),
       "tool_calls": message.get("tool_calls"),
       "role": "assistant",
       "usage": data.get("usage"),  # may be None
   }

3. Log prompt_tokens if available:
   usage = data.get("usage")
   if usage:
       log.debug(f"LLM usage: prompt={usage.get('prompt_tokens')}, "
                 f"completion={usage.get('completion_tokens')}")
```

---

### Step 3: Conversation archive

Add archive writing to the agent loop. Every message appended to a
JSONL file as it's added to history.

#### Prompt

```
Create a new file src/decafclaw/archive.py with archive operations:

import json
import logging
from pathlib import Path

- def archive_path(config, conv_id: str) -> Path:
    return Path(config.data_home) / "workspace" / config.agent_id / "conversations" / f"{conv_id}.jsonl"

- def append_message(config, conv_id: str, message: dict):
    path = archive_path(config, conv_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(message) + "\n")

- def read_archive(config, conv_id: str) -> list[dict]:
    path = archive_path(config, conv_id)
    if not path.exists():
        return []
    messages = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                messages.append(json.loads(line))
    return messages

Then update src/decafclaw/agent.py to archive messages:

- Import: from .archive import append_message
- The conv_id needs to be on the context. It should already be there
  from the Mattermost layer (ctx.channel_id or similar). For archiving,
  we need a single conv_id. Add it: use getattr(ctx, "conv_id", None)
  or getattr(ctx, "channel_id", "unknown").
- After each history.append(msg) call in run_agent_turn, also call:
  conv_id = getattr(ctx, "conv_id", None) or getattr(ctx, "channel_id", "unknown")
  append_message(ctx.config, conv_id, msg)
- This applies to: the user message append, the assistant message append,
  each tool result append, and the final assistant response append.
- Wrap append_message in try/except — archive failure should be logged
  but not break the agent.
```

Also update the Mattermost layer to set conv_id on the forked context:

```
In src/decafclaw/mattermost.py, in _process_conversation, when forking
the request context, add conv_id:
  req_ctx = app_ctx.fork(
      user_id=app_ctx.config.agent_user_id,
      channel_id=channel_id,
      channel_name="",
      thread_id=root_id or "",
      conv_id=conv_id,          # <-- add this
  )
```

And for interactive mode in agent.py, set ctx.conv_id = "interactive".

---

### Step 4: Basic compaction module

Create `compaction.py` with the core logic: split archive into turns,
flatten messages, summarize via LLM, rebuild history.

#### Prompt

```
Create a new file src/decafclaw/compaction.py:

import json
import logging
from .archive import read_archive
from .llm import call_llm

log = logging.getLogger(__name__)

DEFAULT_COMPACTION_PROMPT = """Summarize the following conversation, preserving:
- Key facts and decisions made
- User preferences and corrections
- Important tool results and findings
- The current topic and any open questions

Be concise but don't lose critical details. Format as a brief narrative."""


def _load_compaction_prompt(config) -> str:
    """Load custom prompt from workspace, or use default."""
    from pathlib import Path
    prompt_path = Path(config.data_home) / "workspace" / config.agent_id / "COMPACTION.md"
    if prompt_path.exists():
        return prompt_path.read_text().strip()
    return DEFAULT_COMPACTION_PROMPT


def _split_into_turns(messages: list[dict]) -> list[list[dict]]:
    """Split a flat message list into turns.
    A turn starts with a user message and includes everything until
    the next user message."""
    turns = []
    current_turn = []
    for msg in messages:
        if msg.get("role") == "user" and current_turn:
            turns.append(current_turn)
            current_turn = []
        current_turn.append(msg)
    if current_turn:
        turns.append(current_turn)
    return turns


def _flatten_messages(messages: list[dict]) -> str:
    """Flatten messages into a readable text format for the compaction LLM."""
    lines = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content") or ""

        if role == "user":
            lines.append(f"User: {content}")
        elif role == "assistant":
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                names = [tc["function"]["name"] for tc in tool_calls]
                if content:
                    lines.append(f"Assistant: {content}")
                lines.append(f"Assistant: [called tools: {', '.join(names)}]")
            else:
                lines.append(f"Assistant: {content}")
        elif role == "tool":
            # Truncate long tool results
            preview = content[:500] + "..." if len(content) > 500 else content
            tool_id = msg.get("tool_call_id", "?")
            lines.append(f"Tool result ({tool_id}): {preview}")
        else:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


async def compact_history(ctx, history: list) -> bool:
    """Compact conversation history using the archive as source.

    Reads the full archive, splits into old/recent, summarizes old
    messages, and replaces history with [summary] + [recent].

    Returns True if compaction was performed, False if skipped.
    """
    config = ctx.config
    conv_id = getattr(ctx, "conv_id", None) or getattr(ctx, "channel_id", "unknown")

    # Read the full archive
    archive = read_archive(config, conv_id)
    if not archive:
        log.debug("No archive found, skipping compaction")
        return False

    # Split into turns
    turns = _split_into_turns(archive)
    preserve = config.compaction_preserve_turns

    if len(turns) <= preserve:
        log.debug(f"Only {len(turns)} turns, need >{preserve} to compact")
        return False

    # Split: old turns to summarize, recent turns to keep
    old_turns = turns[:-preserve]
    recent_turns = turns[-preserve:]

    old_messages = [msg for turn in old_turns for msg in turn]
    recent_messages = [msg for turn in recent_turns for msg in turn]

    # Flatten old messages for the compaction LLM
    flattened = _flatten_messages(old_messages)
    log.info(f"Compacting {len(old_messages)} messages ({len(old_turns)} turns) "
             f"into summary, preserving {len(recent_messages)} messages ({len(recent_turns)} turns)")

    # Load the summarization prompt
    prompt = _load_compaction_prompt(config)

    # Call the compaction LLM
    summary_messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": flattened},
    ]

    try:
        await ctx.publish("compaction_start")
        response = await call_llm(
            config, summary_messages,
            llm_url=config.compaction_url,
            llm_model=config.compaction_model,
            llm_api_key=config.compaction_api_key,
        )
        summary = response.get("content", "")
        if not summary:
            log.warning("Compaction LLM returned empty summary, skipping")
            return False
    except Exception as e:
        log.error(f"Compaction LLM call failed: {e}")
        return False
    finally:
        await ctx.publish("compaction_end")

    # Rebuild history: summary + recent messages
    summary_msg = {"role": "user", "content": f"[Conversation summary]: {summary}"}

    history.clear()
    history.append(summary_msg)
    history.extend(recent_messages)

    log.info(f"Compaction complete: {len(old_messages)} messages -> "
             f"1 summary + {len(recent_messages)} recent = {len(history)} total")
    return True
```

---

### Step 5: Wire compaction into the agent loop

Call compaction at the end of `run_agent_turn` when token budget
is exceeded.

#### Prompt

```
Update src/decafclaw/agent.py to trigger compaction after each turn:

1. Import: from .compaction import compact_history

2. Track prompt_tokens from the LLM response. After each call_llm,
   extract usage:
     usage = response.get("usage")
     prompt_tokens = usage.get("prompt_tokens", 0) if usage else 0

   Keep the latest prompt_tokens value (overwrite each iteration,
   since the last LLM call has the fullest context).

3. At the end of run_agent_turn, AFTER the response is added to
   history but BEFORE returning, check if compaction is needed:

     # Check if compaction is needed
     if prompt_tokens and prompt_tokens > config.compaction_max_tokens:
         log.info(f"Token budget exceeded ({prompt_tokens} > {config.compaction_max_tokens}), "
                  f"triggering compaction")
         try:
             await compact_history(ctx, history)
         except Exception as e:
             log.error(f"Compaction failed: {e}")

   This goes right before `return content` and also before
   `return msg` in the max-iterations case.

   Note: extract this to a helper to avoid duplicating:
     async def _maybe_compact(ctx, config, history, prompt_tokens):
         if prompt_tokens and prompt_tokens > config.compaction_max_tokens:
             ...
```

---

### Step 6: Phase 1 smoke test and commit

Verify archive + basic compaction work, lint, test, commit.

#### Prompt

```
Phase 1 verification:

1. make lint && make test (add compaction.py and archive.py to
   both lint and test targets)

2. Functional test: write a quick script that:
   - Creates a config and context with a test conv_id
   - Appends several messages to the archive
   - Calls compact_history
   - Verifies history is replaced with summary + recent turns

3. Verify archive files are created in data/workspace/

4. Clean up test data.

This is a commit point: "Phase 1: conversation archive + basic compaction"
```

---

## Phase 2: Polish

### Step 7: Chunked compaction

Handle archives too large for the compaction LLM's context window.

#### Prompt

```
Update src/decafclaw/compaction.py to support chunked compaction:

1. Add a helper to estimate tokens from text:
   def _estimate_tokens(text: str) -> int:
       return len(text) // 4

2. In compact_history, after flattening old messages, check if the
   flattened text exceeds the compaction LLM's context budget:

   budget = config.compaction_context_budget
   estimated = _estimate_tokens(flattened)

   if estimated > budget:
       # Chunk the old turns
       summary = await _chunked_summarize(ctx, config, old_turns, prompt, budget)
   else:
       # Single-shot summarize (existing code)
       summary = await _single_summarize(ctx, config, flattened, prompt)

3. Extract existing summarize logic into _single_summarize.

4. Implement _chunked_summarize:
   async def _chunked_summarize(ctx, config, turns, prompt, budget):
       chunks = []
       current_chunk = []
       current_size = 0

       for turn in turns:
           turn_text = _flatten_messages(turn)
           turn_size = _estimate_tokens(turn_text)
           # Leave room for the prompt (~500 tokens)
           if current_size + turn_size > budget - 500 and current_chunk:
               chunks.append(current_chunk)
               current_chunk = []
               current_size = 0
           current_chunk.extend(turn)
           current_size += turn_size

       if current_chunk:
           chunks.append(current_chunk)

       # Summarize each chunk
       chunk_summaries = []
       for i, chunk in enumerate(chunks):
           flattened = _flatten_messages(chunk)
           log.info(f"Summarizing chunk {i+1}/{len(chunks)}")
           summary = await _single_summarize(ctx, config, flattened, prompt)
           if summary:
               chunk_summaries.append(summary)

       if not chunk_summaries:
           return ""

       # Combine chunk summaries
       combined = "\n\n---\n\n".join(chunk_summaries)

       # If combined summaries are still too long, summarize them
       if _estimate_tokens(combined) > budget:
           log.info("Combined summaries too long, doing final summarize pass")
           return await _single_summarize(ctx, config, combined, prompt)

       return combined
```

---

### Step 8: compact_conversation tool

Add an explicit tool for manual compaction.

#### Prompt

```
Add a compact_conversation tool.

File: src/decafclaw/tools/core.py
- Add tool function:
  async def tool_compact_conversation(ctx) -> str:
      from ..compaction import compact_history
      history = getattr(ctx, "history", None)
      if history is None:
          return "[error: no conversation history available]"
      result = await compact_history(ctx, history)
      if result:
          return f"Conversation compacted. History now has {len(history)} messages."
      else:
          return "No compaction needed (not enough turns to compact)."

- Note: ctx.history needs to be set in run_agent_turn so this tool
  can access it. Add `ctx.history = history` alongside the existing
  `ctx.messages = messages` line.

- This tool is async (it calls the compaction LLM).

- Add to CORE_TOOLS dict: "compact_conversation": tool_compact_conversation
- Add tool definition to CORE_TOOL_DEFINITIONS:
  {
      "type": "function",
      "function": {
          "name": "compact_conversation",
          "description": "Manually compact the conversation history into
            a summary. Use when the conversation is getting long or when
            you want to consolidate context. This triggers the same
            compaction that happens automatically when the token budget
            is exceeded.",
          "parameters": {
              "type": "object",
              "properties": {},
              "required": [],
          },
      },
  }
```

---

### Step 9: Mattermost delete_message + compaction subscriber

Add delete support and wire up compaction events in Mattermost.

#### Prompt

```
File: src/decafclaw/mattermost.py

1. Add delete_message method:
   async def delete_message(self, post_id):
       resp = await self._http.delete(f"/posts/{post_id}")
       resp.raise_for_status()

2. Update _subscribe_progress to handle compaction events.
   The subscriber needs to track a compaction placeholder post ID.
   Add to the on_progress callback:

   compaction_post_id = None

   elif event_type == "compaction_start":
       nonlocal compaction_post_id
       compaction_post_id = await client.send(
           channel_id, "\U0001f4e6 Compacting conversation...",
           root_id=root_id
       )
       # Note: send returns None currently. We need it to return the post ID.
       # Update send() to return resp.json().get("id") like send_placeholder does.

   elif event_type == "compaction_end":
       nonlocal compaction_post_id
       if compaction_post_id:
           try:
               await client.delete_message(compaction_post_id)
           except Exception:
               pass  # best effort
           compaction_post_id = None

   Wait — the subscriber doesn't have channel_id/root_id since those
   are in the closure from _subscribe_progress. They're already there.

   Actually, the compaction events don't carry channel routing info
   (by design — events are channel-agnostic). The subscriber already
   closes over placeholder_id, channel_id, etc. But for compaction,
   we need to send a NEW message, not edit the existing placeholder.

   Simpler approach: have the subscriber just edit the existing
   placeholder with the compaction message, then the final response
   edit overwrites it. But compaction happens AFTER the response is
   already posted...

   Revised approach: compaction events happen after the placeholder
   is already replaced with the final response. So the subscriber
   should send a separate temporary message for compaction, then
   delete it. The closure has channel_id and root_id, so this works.

   Update send() to return the post ID:
   async def send(self, channel_id, message, root_id=None):
       body = {"channel_id": channel_id, "message": message}
       if root_id:
           body["root_id"] = root_id
       resp = await self._http.post("/posts", json=body)
       resp.raise_for_status()
       return resp.json().get("id")
```

Also update the terminal subscriber in agent.py:
```
In run_interactive's on_progress, add:
   elif event_type == "compaction_start":
       print("  [compacting conversation...]")
   elif event_type == "compaction_end":
       print("  [compaction complete]")
```

---

### Step 10: Phase 2 smoke test, cleanup, and commit

#### Prompt

```
Phase 2 verification:

1. make lint && make test
2. Verify chunked compaction works with a large archive
3. Verify compact_conversation tool works
4. Verify Mattermost delete_message works (or at least compiles)
5. Verify terminal shows compaction progress
6. Update CLAUDE.md with compaction conventions
7. Update README.md config table with new env vars

Commit: "Phase 2: chunked compaction, manual tool, compaction events"
```

---

## Implementation Order Summary

| Step | Phase | Files Changed | What It Does |
|------|-------|--------------|--------------|
| 1 | 1 | `config.py` | Compaction config |
| 2 | 1 | `llm.py` | Token usage + LLM overrides |
| 3 | 1 | `archive.py` (new), `agent.py`, `mattermost.py` | Conversation archive |
| 4 | 1 | `compaction.py` (new) | Core compaction logic |
| 5 | 1 | `agent.py` | Wire compaction into agent loop |
| 6 | 1 | `Makefile`, all | Phase 1 commit point |
| 7 | 2 | `compaction.py` | Chunked compaction |
| 8 | 2 | `tools/core.py`, `agent.py` | compact_conversation tool |
| 9 | 2 | `mattermost.py`, `agent.py` | delete_message + events |
| 10 | 2 | `CLAUDE.md`, `README.md`, all | Phase 2 commit point |

## Risk Notes

- **Phase 1 is the core.** If Phase 2 gets cut, the agent still has
  working compaction — just without chunking, manual tool, and UX polish.
- **Archive I/O on every message.** Appending one line to a file per
  message should be fast, but worth monitoring. If it becomes a bottleneck,
  could batch writes or use `aiofiles`.
- **Compaction LLM quality.** A cheap model may produce poor summaries.
  The COMPACTION.md customization (Phase 2) lets users tune the prompt,
  but the default needs to be good enough.
- **ctx.history reference.** The compact_conversation tool needs access
  to the same list object that run_agent_turn mutates. Setting
  ctx.history = history creates this link.
