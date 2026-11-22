"""Global scope-keyed shell rulebook and decision ledger (#1641 phase 4)."""

import json

from decafclaw.activity import Activity
from decafclaw.media import ToolResult
from decafclaw.shell_scope_rules import (
    ScopeRule,
    ledger_counts,
    load_scope_rules,
    matching_rules,
    promotion_candidates,
    promotion_proposal,
    record_human_decision,
    render_scope_rules,
    rule_shape,
    scope_ledger_path,
    scope_rules_path,
)
from decafclaw.tools.shell_tools import (
    _record_approval,
    build_aux_approval_prompt,
    resolve_scope_rule_guidance,
    tool_shell_guidance,
)


def _activity(domain: str = "homelab", *extra: str) -> Activity:
    return Activity(domain=domain, established=True, domains=(domain, *extra))


def _write_rules(config, rules: list[ScopeRule]) -> None:
    path = scope_rules_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_scope_rules(rules), encoding="utf-8")


def _seed_ledger(config, *, command: str, activity: Activity, approvals: int, denials: int = 0) -> None:
    for _ in range(approvals):
        record_human_decision(config, conv_id="c1", command=command, cwd=None, decision="allow", activity=activity)
    for _ in range(denials):
        record_human_decision(config, conv_id="c1", command=command, cwd=None, decision="deny", activity=activity)


def test_rule_shape_uses_the_existing_suggestion_vocabulary():
    assert rule_shape("make -n check") == "make -n *"
    assert rule_shape("pytest tests/test_x.py -q") == "pytest tests/test_x.py *"
    # Interpreters are never wildcarded by the existing suggestion helper.
    assert rule_shape("sh -c 'rm -rf x'") == "sh -c 'rm -rf x'"


def test_storage_is_admin_tier_not_the_workspace(ctx):
    assert scope_rules_path(ctx.config).parent == ctx.config.agent_path
    assert scope_ledger_path(ctx.config).parent == ctx.config.agent_path
    assert not scope_rules_path(ctx.config).is_relative_to(ctx.config.workspace_path)


def test_promotion_needs_two_approvals_and_no_denials(config):
    activity = _activity("homelab")
    _seed_ledger(config, command="make -n check", activity=activity, approvals=1)
    assert promotion_candidates(config) == []

    _seed_ledger(config, command="make -n check", activity=activity, approvals=1)
    candidates = promotion_candidates(config)
    assert len(candidates) == 1
    assert candidates[0].scope == "homelab"
    assert candidates[0].pattern == "make -n *"
    assert candidates[0].approvals == 2

    # A denial for the same shape disqualifies it at the default thresholds.
    _seed_ledger(config, command="make -n deploy", activity=activity, approvals=0, denials=1)
    assert promotion_candidates(config) == []


def test_existing_rules_are_not_reproposed(config):
    _write_rules(config, [ScopeRule(scope="homelab", pattern="make -n *", rule="pre-approved")])
    _seed_ledger(config, command="make -n check", activity=_activity("homelab"), approvals=3)
    assert promotion_candidates(config) == []
    candidates, content = promotion_proposal(config)
    assert candidates == []
    assert content.strip() == json.dumps(
        {
            "scope": "homelab",
            "pattern": "make -n *",
            "rule": "pre-approved",
            "approvals": 0,
            "denials": 0,
            "updated": "",
        },
        sort_keys=True,
    )


def test_matching_rules_are_scoped_by_activity_and_shape(config):
    _write_rules(
        config,
        [
            ScopeRule(scope="homelab", pattern="make -n *", rule="homelab deploy is fine"),
            ScopeRule(scope="blog", pattern="make -n *", rule="blog deploy is fine"),
        ],
    )
    activity = _activity("homelab")
    assert matching_rules(config, "make -n deploy", activity) == ["homelab deploy is fine"]
    assert matching_rules(config, "ls -la", activity) == []
    assert matching_rules(config, "make -n deploy", _activity("vault")) == []

    config.shell.scope_rules_enabled = False
    assert matching_rules(config, "make -n deploy", activity) == []


def test_ledger_counts_ignore_unknown_scope(config):
    unknown = Activity(domain="unknown", established=False, domains=())
    record_human_decision(config, conv_id="c1", command="make -n check", cwd=None, decision="allow", activity=unknown)
    assert ledger_counts(config) == {}
    assert load_scope_rules(config).rules == []


async def test_only_human_decisions_reach_the_ledger(ctx):
    activity = _activity("homelab")
    await _record_approval(ctx, command="make -n check", decision="allow", tier="read_only", activity=activity)
    assert ledger_counts(ctx.config) == {}

    await _record_approval(ctx, command="make -n check", decision="allow", tier="human", activity=activity)
    assert ledger_counts(ctx.config) == {("homelab", "make -n *"): (1, 0)}


def test_guidance_resolution_includes_only_matching_global_rules(ctx):
    _write_rules(ctx.config, [ScopeRule(scope="homelab", pattern="make -n *", rule="homelab deploy is fine")])
    activity = _activity("homelab")
    assert "homelab deploy is fine" in resolve_scope_rule_guidance(ctx, command="make -n deploy", activity=activity)
    assert "homelab deploy is fine" not in resolve_scope_rule_guidance(ctx)


async def test_judge_prompt_includes_the_matching_global_rule(ctx):
    _write_rules(ctx.config, [ScopeRule(scope="homelab", pattern="make -n *", rule="homelab deploy is fine")])
    activity = _activity("homelab")
    prompt = build_aux_approval_prompt(ctx, "make -n deploy", activity=activity)
    assert "homelab deploy is fine" in prompt
    ctx.config.shell.scope_rules_enabled = False
    prompt = build_aux_approval_prompt(ctx, "make -n deploy", activity=activity)
    assert "homelab deploy is fine" not in prompt


async def test_list_and_promote_are_read_only(ctx):
    activity = _activity("homelab")
    _seed_ledger(ctx.config, command="make -n check", activity=activity, approvals=2)

    listing = await tool_shell_guidance(ctx, action="list")
    assert isinstance(listing, str)
    assert "Global Scope Rules" in listing

    proposal = await tool_shell_guidance(ctx, action="promote_scope_rules")
    assert isinstance(proposal, ToolResult)
    assert proposal.data["content"]
    assert json.loads(proposal.data["content"].strip())["pattern"] == "make -n *"
    # Draft only: the admin_write path is returned, but nothing is written.
    assert not scope_rules_path(ctx.config).exists()


async def test_condense_scope_rules_returns_a_draft_without_writing(ctx, monkeypatch):
    _write_rules(
        ctx.config,
        [
            ScopeRule(scope="homelab", pattern="make -n *", rule="one"),
            ScopeRule(scope="homelab", pattern="make check", rule="two"),
        ],
    )
    before = scope_rules_path(ctx.config).read_text(encoding="utf-8")

    async def fake_call(_config, _messages, model_name="", **_kwargs):
        return {
            "content": json.dumps(
                {"rules": [{"scope": "homelab", "pattern": "make *", "rule": "make commands are fine"}]}
            )
        }

    monkeypatch.setattr("decafclaw.llm.call_llm", fake_call)
    result = await tool_shell_guidance(ctx, action="condense_scope_rules")
    assert isinstance(result, ToolResult)
    assert result.data["before"] == 2
    assert result.data["after"] == 1
    assert json.loads(result.data["content"].strip())["scope"] == "homelab"
    assert scope_rules_path(ctx.config).read_text(encoding="utf-8") == before


async def test_condense_requires_two_rules(ctx):
    result = await tool_shell_guidance(ctx, action="condense_scope_rules")
    assert isinstance(result, str)
    assert "at least two" in result
