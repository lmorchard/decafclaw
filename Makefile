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

# Lint — compile-check all Python source files
lint:
	@uv run python -c "import py_compile, glob, sys; files = glob.glob('src/**/*.py', recursive=True); errors = [f for f in files if not py_compile.compile(f, doraise=False)]; sys.exit(1) if errors else print(f'All {len(files)} files compile OK')"

# Run tests (pytest)
test:
	uv run pytest tests/ -v

# Rebuild eval embedding fixtures (run when changing embedding models)
build-eval-fixtures:
	uv run python scripts/build-eval-fixtures.py
