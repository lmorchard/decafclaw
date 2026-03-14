# Run interactively (no Mattermost, stdin/stdout)
run:
	uv run decafclaw

# Run with auto-restart on source changes
dev:
	uv run --extra dev watchfiles --filter python "decafclaw.main" src/

# Run with debug logging
debug:
	LOG_LEVEL=DEBUG uv run decafclaw

# Run with a specific model
run-pro:
	LLM_MODEL=gemini-2.5-pro uv run decafclaw

# Lint (ruff if available, else basic syntax check)
lint:
	uv run python -m py_compile src/decafclaw/__init__.py
	uv run python -m py_compile src/decafclaw/agent.py
	uv run python -m py_compile src/decafclaw/llm.py
	uv run python -m py_compile src/decafclaw/config.py
	uv run python -m py_compile src/decafclaw/context.py
	uv run python -m py_compile src/decafclaw/events.py
	uv run python -m py_compile src/decafclaw/mattermost.py
	uv run python -m py_compile src/decafclaw/tools/__init__.py
	uv run python -m py_compile src/decafclaw/tools/core.py
	uv run python -m py_compile src/decafclaw/tools/tabstack_tools.py
	uv run python -m py_compile src/decafclaw/tools/memory_tools.py
	uv run python -m py_compile src/decafclaw/memory.py
	@echo "All files compile OK"

# Smoke test — imports and basic sanity
test:
	uv run python -c "from decafclaw.events import EventBus; print('EventBus OK')"
	uv run python -c "from decafclaw.context import Context; print('Context OK')"
	uv run python -c "from decafclaw.agent import run_agent_turn, run_interactive; print('Agent OK')"
	uv run python -c "from decafclaw.tools import TOOL_DEFINITIONS, execute_tool; print('Tools OK')"
	uv run python -c "from decafclaw.llm import call_llm; print('LLM OK')"
	uv run python -c "from decafclaw.memory import save_entry, search_entries, recent_entries; print('Memory OK')"
	uv run python -c "from decafclaw.tools.memory_tools import MEMORY_TOOLS, MEMORY_TOOL_DEFINITIONS; print('Memory Tools OK')"
	@echo "All smoke tests passed"
