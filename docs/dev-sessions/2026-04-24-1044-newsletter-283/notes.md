# Newsletter — retrospective notes

Session: 2026-04-24, 10:44 → ~14:00 PT
Scope: Implement issue #283 (periodic agent newsletter) — full `/dev-session` flow on a worktree branch. PR: https://github.com/lmorchard/decafclaw/pull/356

## Recap

1. Added worktree advice to the global `/dev-session` skill + matching worktree-root clarification in the `git-commit` skill.
2. Started the dev session on issue #283 in `.claude/worktrees/newsletter-283/`.
3. Brainstormed the spec interactively — two major pivots from the original issue description (see below).
4. Wrote spec, self-reviewed, wrote plan, self-reviewed.
5. Executed 11-task plan via `superpowers:subagent-driven-development` — fresh subagent per task with spec + code-quality review gates.
6. Branch self-review caught 3 runtime-critical blockers that the per-task reviews didn't.
7. Squashed, pushed, opened PR, requested Copilot review.
8. Copilot returned 5 comments — all legitimate, all fixed.
9. Re-squashed and force-pushed.

## Divergences from the original plan

- **Push → pull/observer model.** The issue described an inbox file (`workspace/newsletter/inbox.jsonl`) with contributing skills opting in via `newsletter: daily` frontmatter and calling a `newsletter_contribute` tool. Les flipped it during brainstorming: "the newsletter system has to seek out the skills' conversations and perform its own reading and summarization." Much better decoupling — contributing skills stay pure, new scheduled skills are automatically observed.
- **Channel-adapter pattern → skill-only implementation.** Initial architecture mirrored the notifications-channel pattern (new `newsletter_channels/` package, new `newsletter_published` event, new `NewsletterConfig` in core). Les pivoted: "can we isolate all changes into the newsletter skill and not modify agent core?" Skill owns delivery inline. Zero new core subsystems.
- **Email promoted to Phase 1, Mattermost channel deferred.** Les decision for tighter Phase 1 scope and because Mattermost-channel delivery is better paired with a reusable notification adapter (separate concern).

## Key insights

### Branch self-review catches what per-task reviews miss

The per-task code reviewers approved each task in isolation. The branch self-review (by a fresh Opus subagent with the full diff) caught **three runtime-critical blockers** that were only visible at the whole-feature level:

1. **Missing `TOOLS` dict + `TOOL_DEFINITIONS` list.** Without these, the three newsletter tools weren't registered at runtime — the skill would activate but the agent would see SKILL.md telling it to call tools that didn't exist. Tests passed because they imported functions directly, bypassing the loader. The blocker was invisible to every per-task review because each task added correctly-shaped code in isolation.
2. **Scheduled-path skill self-activation gap.** Scheduler pre-activates `required-skills` but not the owning skill. Fixed with `required-skills: [newsletter]` self-reference — idempotent, no core change needed.
3. **Writing the skill-loader integration test (suggested by the branch reviewer) immediately caught a fourth bug:** `from __future__ import annotations` + `@dataclass` + loading via `importlib.spec_from_file_location` fails at runtime on Python 3.13 because `sys.modules.get(cls.__module__)` returns `None` and the dataclass machinery dereferences it. Dropped the future import.

**Lesson:** always do a branch-level review before PR, even if every task was individually reviewed. Cross-task issues only show up at branch scope.

### Plan should include a runtime-discovery sanity check

The plan had 11 tasks covering tools, tests, SKILL.md body, docs. It did NOT have a task asking "how does this get loaded at runtime?" That's the first question a branch reviewer asks, and it was the one that caught the blockers. Worth a standing plan-template addition: for any bundled skill, include an explicit "verify tools register via the skill-loader pipeline" test.

### Copilot's timezone catch was worth the review

Comment 1 on PR #356: `_parse_conv_id` parsed naive local-time timestamps as UTC, but `schedules.py` generates them with naive `datetime.now()` (local). On my Pacific-time host, the 24h window filter would silently drop 8 hours of scheduled activity. I wouldn't have caught it in my own testing — test timestamps used `datetime.now(timezone.utc)` which happened to align with "recent" for the fixture setup. Would have only surfaced in production when the real window didn't match the agent's sense of "the last 24 hours."

### Git mechanics on the squash

Used `git commit --amend` after `git reset --soft origin/main` — the wrong incantation. `--amend` rewrites the parent commit, so I ended up with a squash-commit that rolled in d3ebc61 (a main commit from another branch) as part of my change. Caught it immediately on `git show --stat HEAD` — the file count (18) and presence of eval-coverage files was the signal. Recovered with `git reset --soft d3ebc61` + `git commit -m`.

**Takeaway:** after any squash-like operation, `git show --stat HEAD` is the one-command verification. The correct idiom is `reset --soft <base> && commit -m <msg>`; don't mix `--amend` into the squash sequence.

### Re-rebase before final squash paid off

Main advanced twice during the session: once during brainstorming (notifications push-over-ws PR #337) and once during execution (eval-coverage audit #338). The "re-rebase right before the final PR squash" memory note was exactly right — both times.

## Efficiency observations

- **Subagent-driven execution worked well.** Roughly 35 subagent invocations across the 11 tasks (implementer + spec reviewer + code quality reviewer each). Kept my own context clean of file-read noise; each subagent had a focused context.
- **Bundling related tasks (4+5, 6+7) in a single dispatch** was efficient without compromising review quality. The tasks were tightly coupled (same function in different states; two delivery targets with the same try/except shape). Would do again.
- **Skipping spec+code review for minor fix commits** was fine. The Copilot-response commit and the post-branch-review fix commit got a `make test` + `make lint` gate instead of the full two-stage review. Proportional discipline.
- **Opus for branch self-review, Sonnet for everything else.** The heavier model for the cross-task reasoning + sonnet for mechanical task execution was the right split.

## Process improvements to carry forward

- **Plan template should include a "how does this load at runtime" verification step** for new bundled features (skills, tools, channel adapters). This session's #1 gap.
- **Treat branch self-review as mandatory, not optional.** It's in the `/dev-session pr` flow, but I've seen sessions skip it. Don't.
- **Tighten the spec after implementation.** Copilot comment #5 flagged spec drift — the committed spec described nested config dataclasses and `last_run.json`-based windows that the implementation didn't match. The spec is a design-record artifact going forward; it should reflect what was built, not what was initially imagined. A brief "reconcile spec with reality" pass before PR would prevent this.

## Other observations

- `/dev-session` + `using-git-worktrees` + `brainstorming` + `writing-plans` + `subagent-driven-development` stacked well together in one session. Each skill had a clear role. No friction from overlap.
- Copilot review quality on this PR was 5/5 legitimate comments, no noise. Notable.
- 28 tests at final count (24 from the plan + 4 added in Copilot-response). All passing. `make test` full suite clean throughout — no regressions introduced in other modules.

## Smoke-test addendum

After the retro commit, Les ran `!newsletter` manually in the web UI and got:

> `[error: delegate_task requires a ConversationManager; no manager on parent ctx]`

**Root cause:** `context: fork` on a user-invocable skill routes the interactive invocation through `commands.py:419-429` → `_run_child_turn` in `tools/delegate.py`, which bails at `parent_ctx.manager is None`. The manager is not always set on the parent ctx at command-dispatch time.

**Fix applied:** Changed `context: fork` → `context: inline` in `SKILL.md`. For newsletter, inline is the correct semantic anyway — the skill is meant to reply in the user's conversation, and the `newsletter_publish` tool's interactive short-circuit already provides the "no side effects" guarantee. `fork` was misguided "cleanup" from the branch self-review (I added it to match `dream`/`garden`).

**Broader latent concern:** `src/decafclaw/skills/dream/SKILL.md` and `src/decafclaw/skills/garden/SKILL.md` both have `user-invocable: true` + `context: fork`. `!dream` and `!garden` would hit the same error under the same code path. Likely those commands haven't been interactively invoked recently — their value is as scheduled tasks. Worth a separate issue to either (a) fix the ctx.manager propagation so `context: fork` works for user-invocable commands generally, or (b) flip dream/garden to `context: inline` as well.

**Process lesson:** "Match the pattern of existing skills" was not safe advice in this case. The existing skills' pattern was latently broken in the same way I was about to be. A manual smoke test caught it before merge. **The plan's "test plan" section in the PR description flagged this as a manual step** — noting it was checked but also that the check surfaced real behavior.

## Second smoke-test finding — double user-message bubble on inline commands

Les reported: running `!newsletter` rendered the `!newsletter` bubble twice in the web UI, but only while the turn was live — reloading the page showed a single bubble. Not newsletter-specific; would hit `!health`, `!ingest`, `!postmortem` the same way.

**Root cause:** `src/decafclaw/web/static/lib/message-store.js:158` deduped the server's `user_message` echo by checking *only the last message in the store*. For inline commands, the event sequence is:

1. Frontend adds `{role: 'user', content: '!newsletter'}` optimistically on send.
2. Server emits `command_ack` → frontend pushes `{role: 'command', content: 'Running skill: …'}`.
3. Server emits `user_message` echo with `text: '!newsletter'` — frontend's dedup checks the last message (which is the `command`, not a user), sees no match, pushes the user message a second time.

Reload fixes it because history replay doesn't include the optimistic add; the archive has one user message.

**Fix applied:** in `message-store.js`, scan backwards past non-`user` roles (command, system, etc.) to find the nearest actual user message, then dedup against that. One-liner change in the event handler, no behavior change for ordinary (non-command) messages. `make check-js` clean.

**Third-party surface aside:** this fix happens to live in the web UI while the rest of the PR is backend. It's in-scope because:

- Les triggered the surfacing via smoke-testing this very PR.
- Without the fix, every inline-command user experience is polluted on this branch.
- The change is small and self-contained (~15 lines + comment).

If a reviewer asks why a web-UI dedup fix is in a newsletter PR, the answer is "smoke-test finding; affects all inline commands equally; cheap to fix in place."

## Counts

- Plan tasks: 11
- Commits before final squash: 14 (then re-squashed to 1 after Copilot fixes)
- Subagent dispatches: ~35
- Copilot review comments: 5 (all fixed)
- Times main advanced during the session: 2
- Runtime-critical bugs caught by branch self-review: 3 (plus 1 bonus via integration test)
- Conversation turns: ~90 (estimate)
