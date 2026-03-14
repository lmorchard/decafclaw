# Run interactively (no Mattermost, stdin/stdout)
run:
	uv run decafclaw

# Run with debug logging
debug:
	LOG_LEVEL=DEBUG uv run decafclaw

# Run with a specific model
run-pro:
	LLM_MODEL=gemini-2.5-pro uv run decafclaw

# Count lines of source code
loc:
	@wc -l src/decafclaw/*.py | tail -1
