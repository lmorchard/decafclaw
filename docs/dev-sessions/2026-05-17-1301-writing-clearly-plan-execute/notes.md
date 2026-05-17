# Session notes — writing-clearly plan-execute

## Smoke test result

Used a 3-paragraph excerpt from Les's blog post ("What I actually learned" section, abridged) and ran the new planner prompt against a general-purpose Agent. The Agent returned 5 plan entries, all of which had `before` substrings that matched the draft byte-for-byte. Piped the plan through `_apply_plan` — all 5 applied, 0 skipped, 5 progress events fired with truncated before/after.

The revision reads cleanly: passive→active, "real stuff"→"works", "are everything around it"→"surround it", etc. Voice preserved; technical terms intact.

## Observations

- **No `before`/draft mismatches.** The biggest risk going in was that the planner would drift on whitespace or markdown. The prompt's "copy VERBATIM, character-for-character" directive plus the explicit warning about silent skip seems to be enough — at least for a single Agent invocation against a small paragraph. Worth monitoring on longer drafts and with Gemini Flash specifically, since that's the more common DecafClaw parent.
- **Planner editorial judgment is independent of architecture.** A couple of the proposed edits are debatable (`"used it constantly"` → `"used it constantly thereafter"` doesn't actually omit needless words; it adds them). That's not a tool bug — it's the planner's call. The plan-then-execute structure makes these visible in `data.applied` so the parent agent can summarize or filter them; in v1 they'd have been invisibly baked into the revision.
- **All five edits were `kind: substitution`** — no `rewrite` entries in this sample. The structural-rule rewrites are where the architecture pays off most, but the planner didn't reach for them on this draft. May be sample-specific, or may indicate the planner prompt should explicitly encourage `rewrite` kinds for structural rules (Rule 18 etc.) when they apply. Worth a smoke-test iteration if the live testing surfaces it.
- **Progress events are useful.** Truncated before/after made the messages legible (~80 chars). The "Applied N edits…" gate event + per-entry events + final "Done: X applied, Y skipped" gives the UI a clear narrative.

## What I'd revisit later

- **Encourage `rewrite` kinds in the planner prompt.** Currently `substitution` is the default-ish answer. A line like "Use `rewrite` kind when applying Rule 18 (emphatic words at end) or Rule 5 (active voice across a full clause)" might shift the distribution.
- **Detection of skipped-due-to-whitespace.** If a future smoke test surfaces a `before_not_found` skip caused by a whitespace nit (e.g. double-space in the draft), worth adding a normalized retry — strip trailing whitespace from `before`, lowercase-compare, etc. Not premature now; design for the actual failure when it happens.
- **Plan-quality eval.** A `make eval-skills` case that asserts the planner emits N≥1 edits for a known-passive sentence would catch regressions. Skipped for this PR since it's the first time we have a plan structure to assert against; would be a natural follow-up.

## What didn't need iteration

- The "draft first, then rules below" ordering from v1 carries forward. Didn't have to re-smoke that lesson.
- The v1 fallback path (parse failure → prose-only) is wired but not exercised in this smoke test. The unit tests don't cover it either — would need a delegate_task mock. Trusting the existing delegate_task tests for the parse-failure half.

## Open question for review

Currently `_apply_plan` does case-sensitive exact-match. A planner that copies `before` from a draft but slightly normalizes (smart quotes → straight quotes, etc.) will silently fail. We list these as `before_not_found` in `data.skipped`, which is visible — but the parent agent might benefit from being told "this happened" in the prose response too. Right now it has to inspect `data.skipped` to notice. Not blocking; worth considering for a follow-up if live use shows many skipped entries.
