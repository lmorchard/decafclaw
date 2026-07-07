# Session notes — #597 skill-creator authoring guide

## Summary

Third issue from the `web-lmorchard-fa5ec853` postmortem (the proactive complement to
#595/#596). Added a bundled, lazy-loaded, **text-only** `skill-creator` skill whose
`SKILL.md` body is the in-context authoring contract for decafclaw workspace skills. The
agent activates it (catalog-triggered or `/skill-creator`) when it intends to author a
skill, so the contract is in context at authoring time — instead of cargo-culting
non-decafclaw patterns (the incident's failure: `main.py`, `get_tools()` without `ctx`,
`default_api.*`).

The body teaches: the SKILL.md frontmatter rules (with the Agent Skills standard's
good/poor description example and name constraints), the **decaf-specific `tools.py`
contract** (filename, absolute imports, `get_tools(ctx)`, ctx-first, no `default_api`), a
minimal correct template, and the `skill_validate` → `refresh_skills` → `activate_skill`
workflow. Crucially it **flags where decaf diverges from the generic Agent Skills
standard** (decaf uses `tools.py` with structured tools, not a `scripts/` folder of shell
code; comma-separated `allowed-tools`, not space-separated `Bash(...)`) — that framing is
the guard against the exact cargo-cult that caused the incident.

## What shipped

| Commit | What |
|--------|------|
| `f121855` | feat: `skill-creator` SKILL.md + discovery unit test + `docs/skills.md` + `CLAUDE.md` |
| `c5b11de` | test(evals): `evals/skill-authoring.yaml` — agent activates a skill on an authoring prompt |
| `7f2c967` | docs: clarify `allowed-tools` scope + name-rule enforcement; fence language tags (final-review fixes) |

Plus spec/plan doc commits (`8767b96`, `4096942`).

## Verification

- `make check` green; full `make test` = **2982 passed**.
- Behavior eval passed on a real LLM (vertex-gemini-flash): the agent activated
  `skill-creator` AND followed its full workflow (write SKILL.md → write tools.py →
  `skill_validate` → `refresh_skills` → activate) — end-to-end proof the guide works.

## Key design decisions (verified against code, not assumed)

- **Dropped `allowed-tools` from the skill's frontmatter.** Traced it: a skill's
  `allowed-tools` only hard-restricts in `context: fork` command mode (`commands.py:419-423`)
  and otherwise just feeds `preapproved`; on inline `activate_skill` it's inert — it does NOT
  promote the deferred `skill_validate`/`refresh_skills`. So it would've implied a capability
  it doesn't deliver. The body names the tools instead (deferred catalog → `tool_search`).
  The spec pre-authorized this contingency.
- **Directory `skill-creator` (hyphen), name matching.** Text-only → no Python-module import
  of the dir → a hyphenated dir is safe, making the skill self-exemplifying for the
  "name should match the directory" rule it teaches.
- **Eval doesn't pre-activate** via `setup.skills` — the whole point is testing that the
  catalog description triggers activation. `auto_confirm` handles the bundled-skill activation.

## Lessons / carry-forward

- **`allowed-tools` is a filter/pre-approval, not a promoter.** For surfacing deferred tools
  on skill activation there is no frontmatter lever today; the agent finds them via the
  deferred catalog + `tool_search`. Worth remembering before reaching for `allowed-tools` to
  "make a tool available."
- **For teaching-content deliverables (a skill body the agent FOLLOWS), content accuracy is
  the primary review risk** — not logic. The final review's most valuable findings were two
  accuracy nuances (allowed-tools scope; name-rules-are-conventions-not-validated), both
  worth fixing precisely because following the guide as written could otherwise produce a
  subtly wrong skill.

## Deferred / related (same postmortem cluster)

- **#598** — agent diagnostic-discipline guardrails (loop-breaker, verify-tool-fired,
  apology spiral) + behavior eval. The big, fuzzy, model-conditional one; its own session.
- Possible **#596 follow-up** surfaced by this work: `skill_validate` could enforce the
  `name` format/`name == directory` rules (the Agent Skills standard requires them; the
  loader only checks `name` is present today). The skill-creator body currently teaches them
  as conventions and notes the validator doesn't enforce the format.
