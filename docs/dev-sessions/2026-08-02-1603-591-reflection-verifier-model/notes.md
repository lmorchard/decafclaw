# Notes — #591 reflection verifier_model

## Setup

- Branch: `feat/591-reflection-verifier-model` (from `origin/main` @ `2fab896`)
- Worktree: `.claude/worktrees/feat-591-reflection-verifier-model`
- Baseline: `make test` → **3717 passed, 2 skipped in 27.39s** (green)
- Tier: `auto-ok` (read from the spec's `## Tier:` heading)
- Readiness variant applied: **augmented existing issue**

## Scope call made during branch self-review

Documenting the true resolution order in `docs/reflection.md` made a **pre-existing** contradiction
visible for the first time: the Quick start at `docs/reflection.md:50` recommends
`config set reflection.model gemini-2.5-flash`, which the new table 20 lines below shows is silently
discarded whenever `default_model` is set (the #752 latent bug).

I did **not** fix the bug — the issue forbids it, and fixing it would change what frozen check C2
branch (b) asserts. I did add a one-sentence caveat pointing at #752 and at `verifier_model`, on the
grounds that shipping a page which contradicts itself is worse than the scope cost of one sentence,
and that CLAUDE.md requires the feature's docs to be correct in the same PR. No behavior change, no
check affected. Flagged in the PR body so the call is visible rather than buried.

## Coverage gap carried forward (from the check-reviewer)

`tests/test_config_cli.py` is generic over `fields()`, so `config set reflection.verifier_model`
works by derivation but has no verifier-specific assertion. The env-var half is now covered by
`test_reflection_verifier_model_env_override`. Left as-is; noted so it isn't rediscovered as a
surprise.

### Setup deviation

`HTTP_PORT` was **not** added to the worktree `.env` (the append was blocked by the sandbox in
this unattended run). Harmless here — this change is unit-test-only and no server is started from
this worktree. If anyone runs `make dev` from it, set `HTTP_PORT` first to avoid colliding with
the main clone's `18884`.
