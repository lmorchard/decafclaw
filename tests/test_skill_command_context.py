"""Guard: a user-invocable skill that requires the vault must run inline.

A `/command` with `context: fork` runs as a child agent, which by the #396
vault-access policy gets no vault read access and has vault writes categorically
blocked. So any user-invocable skill that declares `vault` in `required-skills`
(i.e. needs to read/write the vault) must use `context: inline` — otherwise its
command invocation silently can't touch the vault. The scheduled path is
unaffected by `context`.
"""

from decafclaw.config import load_config
from decafclaw.skills import discover_skills


def test_vault_requiring_commands_run_inline():
    skills = discover_skills(load_config())
    offenders = [
        s.name
        for s in skills
        if s.user_invocable
        and "vault" in (s.requires_skills or [])
        and s.context == "fork"
    ]
    assert not offenders, (
        "user-invocable skills that require the vault must use `context: inline`, "
        "not `fork` — a forked command runs as a child agent with no vault read "
        f"and blocked vault writes (#396): {offenders}"
    )
