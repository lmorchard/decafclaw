---
name: broken_skill
---

# Broken Skill (eval fixture)

This is a deliberately-unloadable skill fixture for the loop-breaker eval
(#598). Its frontmatter is missing the required `description` field, so
`validate_skill_md` rejects it during discovery — `activate_skill` will
report "not found" until the frontmatter is fixed, and `refresh_skills` /
`skill_validate` will surface the real rejection reason.

If you're reading this after a fix: there's nothing else to do here. This
skill has no real capability — it exists purely to trigger a validation
failure for the eval.

Note: this file is not loaded directly by the eval harness (there's no
path-based skill-fixture mechanism in `decafclaw.eval.runner`). The eval
case in `evals/diagnostic_discipline.yaml` inlines this same content via
`setup.workspace_files` so it lands under the test's ephemeral
`{workspace}/skills/broken_skill/SKILL.md` where skill discovery scans.
Keep the two in sync if you change this fixture.
