"""Structural guard for the blog-develop contrib skill.

The skill is prose (no tools.py), so this is the automated rot-guard:
it asserts the frontmatter contract and that the body still documents
the load-bearing rules (delegate the workers, children can't write the
vault, one-question-at-a-time interview, blog/drafts output). Prompt
quality itself is validated by live smoke testing, not here.
"""

from pathlib import Path

from decafclaw.skills import validate_skill_md

SKILL = (
    Path(__file__).resolve().parents[1]
    / "contrib" / "skills" / "blog-develop" / "SKILL.md"
)


def test_blog_develop_frontmatter_contract():
    result = validate_skill_md(SKILL)
    assert result.ok is True, result.first_failure
    meta = result.meta
    assert meta["name"] == "blog-develop"
    assert meta["user-invocable"] is True
    assert meta["context"] == "inline"
    assert "vault" in meta["required-skills"]
    assert "tabstack" in meta["required-skills"]
    # Every tool a worker needs must be in allowed-tools (it constrains
    # children too). delegate_task + research + vault read/write.
    allowed = meta["allowed-tools"]
    for tool in ("delegate_task", "tabstack_research", "vault_write", "vault_read"):
        assert tool in allowed, f"{tool} missing from allowed-tools"


def test_blog_develop_body_documents_contract():
    body = validate_skill_md(SKILL).body.lower()
    # The reliability-critical rules must stay in the prose.
    assert "delegate_task" in body
    assert "cannot write" in body            # children can't write the vault
    assert "blog/drafts" in body             # output location
    assert "one question" in body            # interview discipline
    for phase in ("scout", "interview", "research", "draft"):
        assert phase in body
