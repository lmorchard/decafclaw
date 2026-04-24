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

# Type check with pyright
typecheck:
	uv run pyright

# Install all dependencies (Python + JS)
install:
	uv sync
	cd src/decafclaw/web/static && npm install

# Install JS dependencies in static/
install-js:
	cd src/decafclaw/web/static && npm install

# Type check JS (runs npm install if needed)
check-js: install-js
	cd src/decafclaw/web/static && npx tsc --noEmit

# Lint + type check (Python + JS)
check: install-js
	uv run ruff check src/ tests/
	uv run pyright
	cd src/decafclaw/web/static && npx tsc --noEmit

# Auto-fix lint issues
lint-fix:
	uv run ruff check --fix src/ tests/

# Format with ruff
fmt:
	uv run ruff format src/ tests/

# Run tests (pytest, excludes integration tests by default — see pyproject.toml addopts)
test:
	uv run pytest tests/

# Run integration tests only (requires provider credentials).
# Override the default `-m "not integration"` from addopts, and disable
# xdist so parallel workers don't hammer the real APIs concurrently.
test-integration:
	uv run pytest tests/ -v -m integration -n 0

# Run all tests including integration. Matches parallel + default policy,
# but opts back in to integration by OR-ing the markers.
test-all:
	uv run pytest tests/ -m "integration or not integration"

# Rebuild production embedding index
reindex:
	uv run decafclaw-reindex

# Migrate wiki/memories to unified vault structure
migrate-vault:
	uv run python scripts/migrate_to_vault.py

# Dry-run vault migration (show what would change)
migrate-vault-dry:
	uv run python scripts/migrate_to_vault.py --dry-run

# Build web UI vendor bundle (npm + esbuild)
# Run after changing JS dependencies in src/decafclaw/web/static/package.json
# Requires Node.js. Output is committed to git, so only needed for dev.
vendor:
	cd src/decafclaw/web/static && npm install && npm run build

# Show resolved config
config:
	uv run decafclaw config show

# Run all evals against the default model
eval:
	uv run python -m decafclaw.eval evals/

# Rebuild eval embedding fixtures (run when changing embedding models)
build-eval-fixtures:
	uv run python scripts/build-eval-fixtures.py
