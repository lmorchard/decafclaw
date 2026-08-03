"""Every consumer of `discovered_skills` must have made a trust-tier decision.

The forcing function #741 asks for. Fixing #731 turned up five
capability-granting vectors reachable from an agent-writable file across three
rounds of review, and then #737 / #739 / #740 / #744 turned up four more. That
is not nine independent bugs; it is one structural property with many exits:

    `workspace/skills/` is agent-writable AND the highest-precedence skill
    scan entry, and every consumer of `discovered_skills` reads from it.

Gate-by-gate fixing does not converge, because the next person to read
`discovered_skills` gets no protection by default. This test makes "did you
decide?" a build failure rather than a code-review hope.

WHAT THIS PROVES, AND WHAT IT DOES NOT
--------------------------------------
It proves a decision was **recorded** for each consumer. It does NOT prove the
decision is **correct** — a wrong reason string passes. That is the same bound
as `tests/test_schedule_tier_trust.py`, which pins a tier partition rather than
a policy. The value is that a new consumer cannot appear silently; someone has
to come here and write down what they concluded.

SCAN RULES
----------
- **Reads only.** `ast.Load` context on `x.discovered_skills` or on a bare
  `discovered_skills` name (function parameters). Writes cannot leak, so
  assignments like `config.discovered_skills = [...]` are excluded — that is
  deliberate, and it is why `delegate.run_child_turn`'s
  `child_config.discovered_skills = []` (children inherit nothing) is not
  listed as a separate entry.
- String literals are `ast.Constant`, so `config_cli.py`'s
  `"discovered_skills"` entry is not a consumer.

KNOWN BLIND SPOT
----------------
The scan keys on the *name*. A helper that receives the list under a different
parameter name is invisible to it — `build_skill_tool_owners(skills)` is
exactly that shape. Such helpers are reached through a call site that *does*
read `config.discovered_skills`, and that call site IS listed here, so the
requirement is that its reason names where the decision actually lands
("delegates to X"). The chain is the mitigation; it is not automatic, so
reasons must be written to preserve it.
"""

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "decafclaw"

# (module path relative to src/decafclaw, enclosing function) -> why this read
# is safe, or where the tier decision is made.
#
# Adding a consumer? Decide what it should do with a workspace-tier (that is,
# possibly agent-authored) skill, then record the decision here. If it grants
# any capability, route it through `skills.grants_capability` rather than
# spelling out its own `!= "workspace"` check.
REVIEWED_CONSUMERS: dict[tuple[str, str], str] = {
    # -- gates: these consult grants_capability --------------------------------
    ("schedules.py", "_resolve_skill_dir"):
        "GATE (#739): grants_capability filter; a workspace candidate falls "
        "through to the next name and then to task.path.parent, so an "
        "agent-writable dir can never anchor a pre-approved shell pattern.",
    ("schedules.py", "_render_required_skill_bodies"):
        "GATE (#740): grants_capability skip. <loaded_skills> is the full body "
        "presented as instructions in force on a turn that installs real "
        "pre-approvals; mirrors prompts/__init__.py.",
    ("commands.py", "execute_command"):
        "GATE (#737): grants_capability gates BOTH the pre-approval install "
        "and requires_skills activation. Restriction still applies at every "
        "tier; the command's own activation stays ungated because the human "
        "typed its name.",
    ("tool_definitions.py", "collect_all_tool_defs"):
        "GATE (#744): grants_capability before _load_native_tools, which "
        "imports tools.py and therefore execs module-level code.",
    ("__init__.py", "main"):
        "DELEGATES to skills.build_skill_tool_owners, which applies "
        "grants_capability before importing any tools.py (#744). This is the "
        "startup call site; the decision lands in the callee.",
    ("schedules.py", "setup_schedule_ctx"):
        "GATE (#731 vector 5): skips trust_tier == 'workspace' before "
        "activate_skill_internal, which execs the skill's tools.py.",
    ("skills/__init__.py", "activate_always_loaded"):
        "GATE: skips trust_tier == 'workspace' outright. Workspace skills also "
        "have always_loaded stripped at discovery; this is defense in depth.",

    # -- deliberate permits ----------------------------------------------------
    ("skills/__init__.py", "activate_skills_for_workflow"):
        "PERMITS workspace tier deliberately — a workflow author named the "
        "skill explicitly in code, which is a human decision at authoring "
        "time. Documented in the function's own docstring.",
    ("tools/skill_tools.py", "tool_activate_skill"):
        "GATE (#649): the confirmation gate itself lives here — "
        "is_trusted_tier = trust_tier != 'workspace', denied outright on an "
        "unattended turn. NOTE the gate is in this wrapper, not in "
        "activate_skill_internal, so a caller that skips the wrapper skips the "
        "gate; that is the shape #744 exploited.",
    ("tools/skill_tools.py", "restore_skills"):
        "DECISION UPSTREAM: only restores names already in "
        "ctx.skills.activated, so each passed the activation gate when it was "
        "first approved. Caveat recorded in the session notes: a standing "
        "'always' grant plus a later rewrite of that skill's tools.py would "
        "re-import new code without a fresh confirmation. Pre-existing "
        "'always'-permission semantics, out of scope for #741.",

    # -- no grant: name/description only (catalog-equivalent) ------------------
    ("context_composer.py", "_compose_preempt_skill_matches"):
        "NO GRANT: scores skill name + description for a pre-emptive "
        "suggestion. Catalog-equivalent data; acting on the suggestion still "
        "goes through activate_skill's gate.",
    ("tools/search_tools.py", "tool_search"):
        "NO GRANT: reads name + description to build search results. A "
        "workspace skill can be surfaced, but activating it still goes "
        "through tool_activate_skill's confirmation.",
    ("interactive_terminal.py", "_print_banner"):
        "NO GRANT: prints skill names in the startup banner.",
    ("commands.py", "format_help"):
        "NO GRANT: renders names, descriptions and argument hints for !help.",
    ("commands.py", "list_invokable_commands"):
        "NO GRANT: renders names, descriptions and argument hints for the web "
        "UI's autocomplete menu — the same catalog-equivalent data format_help "
        "prints. Picking an entry only types text into the composer; invoking "
        "it still goes through dispatch_command, and every grant is decided "
        "downstream in execute_command.",
    ("web/websocket.py", "_handle_list_commands"):
        "DELEGATES to commands.list_invokable_commands (NO GRANT: name, "
        "description and argument hint only). Answering this frame runs no "
        "skill code and activates nothing; the reply is display data for the "
        "composer menu.",
    ("skills/__init__.py", "list_commands"):
        "NO GRANT: filters on user_invocable for the help listing. Whether an "
        "invoked command GRANTS anything is decided in execute_command.",
    ("skills/__init__.py", "find_command"):
        "NO GRANT: name lookup only. The tier decision for what an invoked "
        "command may do lands in execute_command.",
    ("commands.py", "dispatch_command"):
        "NO GRANT: resolves the trigger via find_command and renders !help. "
        "All granting happens downstream in execute_command.",
    ("tools/skill_tools.py", "tool_refresh_skills"):
        "NO GRANT: diffs skill names before/after to report what changed. The "
        "rediscovery it triggers is covered by the rediscover_skills entry.",
    ("tools/skill_tools.py", "rediscover_skills"):
        "DELEGATES to build_skill_tool_owners, which applies "
        "grants_capability before importing any tools.py (#744). Rebuilding "
        "the catalog itself grants nothing.",
    ("tools/tool_registry.py", "get_critical_names"):
        "NO GRANT: promotes tool names from always_loaded skills. Workspace "
        "skills cannot be always_loaded — stripped at discovery and skipped "
        "again in activate_always_loaded — so this cannot see one.",
    ("tools/__init__.py", "execute_tool"):
        "NO GRANT: reads trust_tier to phrase an error message, and refuses "
        "workspace-skill tools that lack an 'always' grant.",
    ("tools/delegate.py", "run_child_turn"):
        "NO GRANT beyond the parent's: copies bodies of skills already in "
        "parent_ctx.skills.activated into the child prompt, so activation "
        "already made the decision. The child's own discovered_skills is set "
        "to [] (a write, so not a listed read) — children discover nothing.",

    # -- test/eval harness -----------------------------------------------------
    ("eval/runner.py", "run_test"):
        "HARNESS: populates the catalog for an eval run and delegates to "
        "build_skill_tool_owners, which applies grants_capability (#744). Not "
        "agent-reachable. The sibling loadout builder "
        "(eval/tool_choice/loadout.py) applies grants_capability directly.",
}


def _consumers() -> set[tuple[str, str]]:
    """Every (module, enclosing function) that READS `discovered_skills`."""
    found: set[tuple[str, str]] = set()
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text())
        enclosing: dict[ast.AST, str] = {}

        def walk(node: ast.AST, fname: str) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    enclosing[child] = child.name
                    walk(child, child.name)
                else:
                    enclosing[child] = fname
                    walk(child, fname)

        walk(tree, "<module>")

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if node.attr != "discovered_skills":
                    continue
            elif isinstance(node, ast.Name):
                if node.id != "discovered_skills":
                    continue
            else:
                continue
            if not isinstance(node.ctx, ast.Load):
                continue  # a write cannot leak
            found.add((
                path.relative_to(SRC).as_posix(),
                enclosing.get(node, "<module>"),
            ))
    return found


def test_every_discovered_skills_consumer_has_a_recorded_decision():
    """A new consumer fails this until its tier behavior is written down."""
    actual = _consumers()
    registered = set(REVIEWED_CONSUMERS)

    undecided = actual - registered
    assert not undecided, (
        "These read config.discovered_skills with no recorded trust-tier "
        "decision. `workspace/skills/` is agent-writable and the "
        "highest-precedence scan entry, so decide what each should do with a "
        "possibly agent-authored skill, then add an entry to "
        "REVIEWED_CONSUMERS. If it grants any capability, route it through "
        f"skills.grants_capability: {sorted(undecided)}"
    )


def test_no_stale_registry_entries():
    """The registry can't rot into a pile of dead entries.

    Without this, a consumer that gets renamed or deleted leaves a reason
    behind that reads as current review coverage but describes nothing.
    """
    actual = _consumers()
    stale = set(REVIEWED_CONSUMERS) - actual
    assert not stale, (
        "These REVIEWED_CONSUMERS entries no longer match any read of "
        f"discovered_skills — remove them or fix the key: {sorted(stale)}"
    )


def test_registry_reasons_are_substantive():
    """A reason has to say something re-auditable.

    Guards against the registry degrading into `"safe"` / `"n/a"`, which would
    make the whole test theatre. Not a correctness check — see the module
    docstring on what this file can and cannot prove.
    """
    too_short = {k: v for k, v in REVIEWED_CONSUMERS.items() if len(v) < 40}
    assert not too_short, f"reasons too terse to re-audit from: {too_short}"

    vague = {"safe", "ok", "fine", "n/a", "no grant", "gate"}
    lazy = {
        k: v for k, v in REVIEWED_CONSUMERS.items()
        if v.strip().rstrip(".").lower() in vague
    }
    assert not lazy, f"reasons must explain, not label: {lazy}"
