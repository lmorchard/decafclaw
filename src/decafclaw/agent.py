"""The agent loop — the core of DecafClaw.

This is where the interesting stuff happens. The loop:
1. Receives a message (from stdin or Mattermost)
2. Builds a prompt with system prompt + history + tools
3. Calls the LLM
4. If the LLM wants to use tools, executes them and loops
5. Returns the final text response
"""

import asyncio
import json
import logging

from .archive import append_message
from .compaction import compact_history
from .llm import call_llm
from .tools import TOOL_DEFINITIONS, execute_tool

log = logging.getLogger(__name__)


def _conv_id(ctx):
    """Get conversation ID from context."""
    return getattr(ctx, "conv_id", None) or getattr(ctx, "channel_id", "unknown")


def _archive(ctx, msg):
    """Archive a message, logging errors but never raising."""
    try:
        append_message(ctx.config, _conv_id(ctx), msg)
    except Exception as e:
        log.error(f"Archive write failed: {e}")


async def _maybe_compact(ctx, config, history, prompt_tokens):
    """Trigger compaction if token budget is exceeded."""
    if prompt_tokens and prompt_tokens > config.compaction_max_tokens:
        log.info(f"Token budget exceeded ({prompt_tokens} > {config.compaction_max_tokens}), "
                 f"triggering compaction")
        try:
            await compact_history(ctx, history)
        except Exception as e:
            log.error(f"Compaction failed: {e}")


async def run_agent_turn(ctx, user_message: str, history: list) -> str:
    """Process a single user message through the agent loop.

    Args:
        ctx: Runtime context (carries config, event bus, etc.)
        user_message: The user's message text
        history: Conversation history (list of message dicts, mutated in place)

    Returns:
        The agent's text response
    """
    config = ctx.config
    # Expose history on context so tools (e.g., compact_conversation) can access it
    ctx.history = history
    ctx.total_prompt_tokens = getattr(ctx, "total_prompt_tokens", 0)
    ctx.total_completion_tokens = getattr(ctx, "total_completion_tokens", 0)

    # Add user message to history
    user_msg = {"role": "user", "content": user_message}
    history.append(user_msg)
    _archive(ctx, user_msg)

    # Build the messages array: system prompt + history
    messages = [{"role": "system", "content": config.system_prompt}] + history
    # Expose messages on context so debug tools can inspect them
    ctx.messages = messages

    prompt_tokens = 0

    for iteration in range(config.max_tool_iterations):
        log.debug(f"Agent iteration {iteration + 1}")

        # Call the LLM
        await ctx.publish("llm_start", iteration=iteration + 1)
        response = await call_llm(config, messages, tools=TOOL_DEFINITIONS)
        await ctx.publish("llm_end", iteration=iteration + 1)

        # Track token usage
        usage = response.get("usage")
        if usage:
            prompt_tokens = usage.get("prompt_tokens", 0)
            ctx.total_prompt_tokens += usage.get("prompt_tokens", 0)
            ctx.total_completion_tokens += usage.get("completion_tokens", 0)

        # If there are tool calls, execute them
        tool_calls = response.get("tool_calls")
        if tool_calls:
            # Add the assistant's tool-call message to history
            assistant_msg = {"role": "assistant", "content": response.get("content")}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            history.append(assistant_msg)
            messages.append(assistant_msg)
            _archive(ctx, assistant_msg)

            # Execute each tool and add results
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = json.loads(tc["function"]["arguments"])
                log.info(f"Tool call: {fn_name}({fn_args})")

                await ctx.publish("tool_start", tool=fn_name, args=fn_args)
                result = await execute_tool(ctx, fn_name, fn_args)
                await ctx.publish("tool_end", tool=fn_name)
                log.debug(f"Tool result: {result[:200]}...")

                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                }
                history.append(tool_msg)
                messages.append(tool_msg)
                _archive(ctx, tool_msg)

            # Loop back to call the LLM again with tool results
            continue

        # No tool calls — we have a final response
        content = response.get("content", "")
        final_msg = {"role": "assistant", "content": content}
        history.append(final_msg)
        _archive(ctx, final_msg)
        await _maybe_compact(ctx, config, history, prompt_tokens)
        return content

    # Hit max iterations
    msg = "[Agent reached max tool iterations without a final response]"
    final_msg = {"role": "assistant", "content": msg}
    history.append(final_msg)
    _archive(ctx, final_msg)
    await _maybe_compact(ctx, config, history, prompt_tokens)
    return msg


async def run_interactive(ctx):
    """Run the agent in interactive terminal mode (stdin/stdout)."""
    config = ctx.config

    # Populate context defaults for interactive mode
    ctx.user_id = getattr(ctx, "user_id", None) or config.agent_user_id
    ctx.channel_id = getattr(ctx, "channel_id", "") or "interactive"
    ctx.channel_name = getattr(ctx, "channel_name", "") or "interactive"
    ctx.thread_id = getattr(ctx, "thread_id", "") or ""
    ctx.conv_id = "interactive"

    print("DecafClaw interactive mode. Type 'quit' to exit.")
    print(f"Model: {config.llm_model}")
    print(f"Tools: {', '.join(t['function']['name'] for t in TOOL_DEFINITIONS)}")
    print()

    def on_progress(event):
        event_type = event.get("type")
        if event_type == "tool_status":
            print(f"  [{event.get('tool', 'tool')}] {event['message']}")
        elif event_type == "tool_start":
            print(f"  [running {event.get('tool', 'tool')}...]")
        elif event_type == "llm_start" and event.get("iteration", 1) > 1:
            print("  [thinking...]")
        elif event_type == "compaction_start":
            print("  [compacting conversation...]")
        elif event_type == "compaction_end":
            print("  [compaction complete]")

    sub_id = ctx.event_bus.subscribe(on_progress)

    # Resume from archive if available
    from .archive import read_archive
    history = read_archive(config, ctx.conv_id)
    if history:
        log.info(f"Resumed interactive session from archive ({len(history)} messages)")
        print(f"  (resumed {len(history)} messages from previous session)")
    else:
        history = []

    try:
        while True:
            try:
                user_input = (await asyncio.to_thread(input, "you> ")).strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye!")
                break

            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit"):
                break

            response = await run_agent_turn(ctx, user_input, history)
            print(f"\nagent> {response}\n")
    finally:
        ctx.event_bus.unsubscribe(sub_id)
