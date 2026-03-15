# Run interactively (no Mattermost, stdin/stdout)
run:
	uv run decafclaw

# Run with auto-restart on source changes
dev:
	uv run --extra dev watchfiles --filter python --sigint-timeout 10 --sigkill-timeout 15 "decafclaw.main" src/

# Run with debug logging
debug:
	LOG_LEVEL=DEBUG uv run decafclaw

# Run with a specific model
run-pro:
	LLM_MODEL=gemini-2.5-pro uv run decafclaw

# Lint with ruff
lint:
	uv run ruff check src/ tests/

# Auto-fix lint issues
lint-fix:
	uv run ruff check --fix src/ tests/

# Format with ruff
fmt:
	uv run ruff format src/ tests/

# Run tests (pytest)
test:
	uv run pytest tests/ -v

# Rebuild production embedding index from memory files
reindex:
	uv run decafclaw-reindex

# Rebuild eval embedding fixtures (run when changing embedding models)
build-eval-fixtures:
	uv run python scripts/build-eval-fixtures.py
