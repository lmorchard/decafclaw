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

from .llm import call_llm
from .tools import TOOL_DEFINITIONS, execute_tool

log = logging.getLogger(__name__)


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

    # Add user message to history
    history.append({"role": "user", "content": user_message})

    # Build the messages array: system prompt + history
    messages = [{"role": "system", "content": config.system_prompt}] + history
    # Expose messages on context so debug tools can inspect them
    ctx.messages = messages

    for iteration in range(config.max_tool_iterations):
        log.debug(f"Agent iteration {iteration + 1}")

        # Call the LLM
        await ctx.publish("llm_start", iteration=iteration + 1)
        response = await call_llm(config, messages, tools=TOOL_DEFINITIONS)
        await ctx.publish("llm_end", iteration=iteration + 1)

        # If there are tool calls, execute them
        tool_calls = response.get("tool_calls")
        if tool_calls:
            # Add the assistant's tool-call message to history
            assistant_msg = {"role": "assistant", "content": response.get("content")}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            history.append(assistant_msg)
            messages.append(assistant_msg)

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

            # Loop back to call the LLM again with tool results
            continue

        # No tool calls — we have a final response
        content = response.get("content", "")
        history.append({"role": "assistant", "content": content})
        return content

    # Hit max iterations
    msg = "[Agent reached max tool iterations without a final response]"
    history.append({"role": "assistant", "content": msg})
    return msg


async def run_interactive(ctx):
    """Run the agent in interactive terminal mode (stdin/stdout)."""
    config = ctx.config

    # Populate context defaults for interactive mode
    ctx.user_id = getattr(ctx, "user_id", None) or config.agent_user_id
    ctx.channel_id = getattr(ctx, "channel_id", "") or "interactive"
    ctx.channel_name = getattr(ctx, "channel_name", "") or "interactive"
    ctx.thread_id = getattr(ctx, "thread_id", "") or ""

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

    sub_id = ctx.event_bus.subscribe(on_progress)

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
