# issue-994-subagent-memory-cap

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/994
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-994-subagent-memory-cap
- Branch: issue-994-subagent-memory-cap
- Base: origin/main at 288fa0581f4a4dcbe692f1ee418f202e5f009af2
- Status: implementation complete, gates green, committed; publishing as DRAFT PR (same session, per handoff).
- Next action: publish draft via submit-pr; independent review is a LATER handoff (do not mark PR ready in this run).

## Log

### 2026-10-18 — Spec drift check (PASS)

- opencode `tools.py`: SkillConfig L341, `create_subprocess_exec` L826, exit log L895, exit_status L906 — all match spec (spec said L342 for SkillConfig, off by one, same dataclass).
- claude_code `tools.py`: SkillConfig L33, `query()` L623, except-handler L667 — match spec.
- `uv.lock`: `claude-code-sdk` 0.0.25 — matches spec.

### 2026-10-18 — SDK 0.0.25 internals (verified in worktree venv, py3.13)

- `query(prompt, options, transport=None)` — public `transport=` arg; `InternalClient.process_query` uses the provided transport as-is (connects it, does not re-wrap).
- `SubprocessCLITransport(prompt, options, cli_path=None)` in `claude_code_sdk._internal.transport.subprocess_cli`; `connect()` does `cmd = self._build_command()` then `anyio.open_process(cmd, ...)` → a subclass overriding `_build_command()` to prepend a prefix works.
- `ProcessError` is public (`claude_code_sdk.ProcessError`), has `.exit_code`; SDK raises `ProcessError(f"Command failed with exit code {rc}", exit_code=rc, ...)` when `rc != 0`.
- anyio (asyncio backend) `Process.wait()` returnscode for a SIGKILLed child is **-9** (verified with a real command in the worktree venv).

### 2026-10-18 — OOMPolicy uncertainty (RESOLVED: do not use)

- `OOMPolicy=` on scope units is documented in systemd.scope(5) with marker **Added in version 253** (verified in `man/systemd.scope.xml` on systemd main: `<xi:include href="version-info.xml" xpointer="v253"/>`).
- Deploy target is Debian 12 = systemd **252** → `OOMPolicy=` is an unknown lvalue for [Scope] there; transient scope creation (`StartTransientUnit`) would fail and break every subagent send on the target host.
- Default OOMPolicy is `stop` (v252 `src/core/main.c`: `arg_default_oom_policy = OOM_STOP`); with `stop`, a cgroup OOM kill in the scope still terminates all scope processes (SIGTERM→SIGKILL), so the daemon stays safe without the extra property.
- **Decision:** prefix is `systemd-run --user --scope --quiet -p MemoryMax=... -p MemoryHigh=... --` (no OOMPolicy). Recorded here + PR description.

### 2026-10-18 — systemd-run --scope semantics (verified from v252 source, src/run/run.c)

- `start_transient_scope()`: creates the transient scope via `StartTransientUnit` with the `-p` properties and `PIDs=[<systemd-run pid>]` (manager moves that PID into the scope cgroup), then **`execvpe()`s the command in systemd-run's own process**.
- Consequences (confirm the spec's "uncertainty"): the exec'd CLI *is* the child we wait for → `proc.returncode` is the CLI's own status (**-9** on SIGKILL, no 128+N translation anywhere); the command inherits our stdout/stderr pipes (JSON stream passes through); execution is synchronous (man page: "Execution in this case is synchronous, and will return only when the command finishes").
- This is why the spec's -9 check for opencode (and -9/137 for the SDK path) is correct.

### 2026-10-18 — Config-layering decision (empty env vars)

- `load_sub_config` (src/decafclaw/config.py L130) treats **empty env values as unset** (project-wide convention; same as `requires.env` checks). So `OPENCODE_MEMORY_MAX=` in the environment does NOT disable the cap — it falls through to config.json/default.
- Disabling a cap = empty values in `config.json` (`"skills": {"opencode": {"memory_max": "", "memory_high": ""}}`), which `load_sub_config` does apply. Helper returns [] when both SkillConfig fields are empty (spec behavior).
- Decision: follow project convention; document config.json as the disable path in docs/deployment.md; note the env-var nuance in the PR description. Spec intent (empty value ⇒ no prefix) is preserved.

### 2026-10-18 — Delegation probe design

- Probe: read `/sys/fs/cgroup/user.slice/user-<uid>.slice/user@<uid>.service/cgroup.controllers` (spec's suggested path), check `memory` token. Any read failure ⇒ not delegated (fail-open). Result cached for process lifetime; one warning per process lifetime when a cap is requested but cannot be applied (missing systemd-run OR not delegated).

### 2026-10-18 — Implementation plan

1. `src/decafclaw/subprocess_limits.py` — `memory_cap_prefix(memory_max, memory_high, label="") -> list[str]`.
2. opencode `tools.py`: SkillConfig +2 fields; prefix before `cmd_args` at create_subprocess_exec; non-zero returncode ⇒ error ToolResult (kill text for -9).
3. claude_code `tools.py`: SkillConfig +2 fields; `_CappedSubprocessCLITransport` (internal-import comment); `transport=` to `query()` when prefix non-empty; `_describe_sdk_error` kill text for ProcessError exit_code in (-9, 137).
4. Docs: deployment.md section; both SKILL.md config sections.
5. Tests: `tests/test_subprocess_limits.py`; `contrib/skills/opencode/test_opencode_memory_cap.py`; `contrib/skills/claude_code/test_claude_code_memory_cap.py`.
- Baseline `make test` running; targeted tests during implementation; full `make test` + `make check` before commit.

### 2026-10-18 — Implementation done + checks (pre-commit)

Baseline (clean, before any change): `make test` → **4575 passed, 2 skipped** (44.4s).

Changes made:
- `src/decafclaw/subprocess_limits.py` (new) — `memory_cap_prefix(memory_max, memory_high, label="")` + cached delegation probe + one-warning-per-lifetime.
- `contrib/skills/opencode/tools.py` — SkillConfig +`memory_max`/`memory_high`; prefix before `cmd_args` at `create_subprocess_exec`; non-zero returncode ⇒ error `ToolResult` (kill text for -9), session left usable.
- `contrib/skills/claude_code/tools.py` — SkillConfig +`memory_max`/`memory_high`; `_CappedSubprocessCLITransport` (subclasses internal `SubprocessCLITransport`, prepends prefix in `_build_command`); `transport=` to `query()` when prefix non-empty (constructed inside the try so `CLINotFoundError` keeps the old clean error path); `_describe_claude_error` kill text for `ProcessError.exit_code in (-9, 137)`.
- Docs: `docs/deployment.md` "Subagent Memory Limits"; both SKILL.md "Configuration" sections; CLAUDE.md key-files line.
- Tests: `tests/test_subprocess_limits.py` (8), `contrib/skills/opencode/test_opencode_memory_cap.py` (4), `contrib/skills/claude_code/test_claude_code_memory_cap.py` (7).

Test-specificity verified: with each skill's tools.py stashed, its new tests fail (opencode: 4 failed; claude_code: ImportError on `_CappedSubprocessCLITransport`).

Checks (on the committed change):
- Targeted new tests: 19 passed.
- `make test` (full): **4594 passed, 2 skipped** (14.5s).
- `make check` (full gate: install-js + check-npm-policy + check-message-types + fmt-check + lint + typecheck + check-js + check-browser-assets): **PASS**.
- Skill-loader exec path: both skills load via `_load_native_tools`; `load_sub_config` resolves new fields from defaults and from env aliases (`OPENCODE_MEMORY_MAX=300M` / `CLAUDE_CODE_MEMORY_MAX=1G` verified).
- New tests are fast (≤0.01s setup, no fixed sleeps) — checked via `--durations`.

Evals: skipped per project convention — no tool description changed (verified TOOL_DEFINITIONS untouched).

Note: the implementation commit bundles the code, tests, docs, and these notes together (no separate notes-only commit). It is the sole commit on this branch; its final hash is recorded in the PR description (a commit cannot record its own hash).
