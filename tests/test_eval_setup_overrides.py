"""Unit coverage for the eval runner's per-test ``setup`` block.

``setup.config_overrides`` is a generic dotted-path mechanism for tweaking
any slice of the resolved ``Config`` for a single eval case. See
``docs/eval-loop.md`` setup-fields table.
"""

import dataclasses
import pathlib
import re

import pytest
import yaml

from decafclaw.config import Config
from decafclaw.eval.runner import (
    _KNOWN_SETUP_KEYS,
    _REMOVED_SETUP_KEYS,
    _build_test_config,
    _seed_conversation_history,
    _setup_of,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _tmp(tmp_path):
    return str(tmp_path)


def _overrides(**kv):
    return {"setup": {"config_overrides": kv}}


# -- infrastructure overrides always win --


def test_agent_data_home_and_id_are_always_set(tmp_path):
    """Every test gets an isolated data_home + the sentinel 'eval' agent id
    regardless of what ``setup`` overrides are present."""
    cfg = Config()
    out = _build_test_config(cfg, {"setup": {}}, _tmp(tmp_path))
    assert out.agent.data_home == _tmp(tmp_path)
    assert out.agent.id == "eval"


def test_config_overrides_cannot_escape_the_sandbox(tmp_path):
    """A case cannot redirect data_home out of its temp dir — the harness
    applies the sandbox fields last, so they beat any user override."""
    cfg = Config()
    out = _build_test_config(
        cfg, _overrides(**{"agent.data_home": "/etc", "agent.id": "not-eval"}),
        _tmp(tmp_path),
    )
    assert out.agent.data_home == _tmp(tmp_path)
    assert out.agent.id == "eval"


# -- basic override behaviour --


def test_nested_override_applies(tmp_path):
    cfg = Config()
    cfg.reflection.enabled = True
    out = _build_test_config(
        cfg, _overrides(**{"reflection.enabled": False}), _tmp(tmp_path),
    )
    assert out.reflection.enabled is False
    # Base config is untouched — we returned a modified copy, not a mutation.
    assert cfg.reflection.enabled is True


def test_override_works_in_both_directions(tmp_path):
    """A suite-wide setting can be forced on as well as off."""
    cfg = Config()
    cfg.reflection.enabled = False
    out = _build_test_config(
        cfg, _overrides(**{"reflection.enabled": True}), _tmp(tmp_path),
    )
    assert out.reflection.enabled is True


def test_max_tool_iterations_override(tmp_path):
    """The grace-turn budget override (#448) now rides the generic path."""
    cfg = Config()
    baseline = cfg.agent.max_tool_iterations
    out = _build_test_config(
        cfg, _overrides(**{"agent.max_tool_iterations": 3}), _tmp(tmp_path),
    )
    assert out.agent.max_tool_iterations == 3
    unset = _build_test_config(cfg, {}, _tmp(tmp_path))
    assert unset.agent.max_tool_iterations == baseline


def test_overrides_compose_across_sections(tmp_path):
    """Multiple paths into different sub-dataclasses all land in one pass."""
    cfg = Config()
    cfg.reflection.enabled = True
    out = _build_test_config(
        cfg,
        _overrides(**{
            "reflection.enabled": False,
            "agent.max_tool_iterations": 2,
        }),
        _tmp(tmp_path),
    )
    assert out.reflection.enabled is False
    assert out.agent.max_tool_iterations == 2


def test_multiple_overrides_into_same_section(tmp_path):
    """Two paths sharing a prefix merge rather than clobbering each other.

    Regression guard: a naive implementation that calls ``replace`` once per
    dotted path would drop all but the last override on a shared section.
    """
    cfg = Config()
    out = _build_test_config(
        cfg,
        _overrides(**{
            "agent.max_tool_iterations": 7,
            "agent.max_concurrent_tools": 2,
        }),
        _tmp(tmp_path),
    )
    assert out.agent.max_tool_iterations == 7
    assert out.agent.max_concurrent_tools == 2


def test_top_level_scalar_override(tmp_path):
    """A single-segment path targets a field on Config itself."""
    cfg = Config()
    out = _build_test_config(
        cfg, _overrides(**{"default_model": "some-model"}), _tmp(tmp_path),
    )
    assert out.default_model == "some-model"


def test_absent_config_overrides_is_a_noop(tmp_path):
    """No ``config_overrides`` key → config flows through unchanged."""
    cfg = Config()
    out = _build_test_config(cfg, {"setup": {}}, _tmp(tmp_path))
    for f in dataclasses.fields(Config):
        if f.name == "agent":  # sandbox fields legitimately differ
            continue
        assert getattr(out, f.name) == getattr(cfg, f.name)


# -- the mechanism must not rot as Config grows --


def test_every_config_section_is_reachable(tmp_path):
    """Any nested dataclass section can be overridden without runner changes.

    Iterates the real field list rather than a hand-written allowlist, per
    the CLAUDE.md convention — a new config section should work on arrival.
    """
    cfg = Config()
    sections = [
        f.name for f in dataclasses.fields(Config)
        if dataclasses.is_dataclass(getattr(cfg, f.name))
    ]
    assert sections, "expected Config to have nested dataclass sections"

    for section in sections:
        sub = getattr(cfg, section)
        bool_field = next(
            (f.name for f in dataclasses.fields(sub)
             if isinstance(getattr(sub, f.name), bool)),
            None,
        )
        if bool_field is None:
            continue
        path = f"{section}.{bool_field}"
        flipped = not getattr(sub, bool_field)
        out = _build_test_config(cfg, _overrides(**{path: flipped}), _tmp(tmp_path))
        assert getattr(getattr(out, section), bool_field) is flipped, path


# -- validation: a typo must fail loudly, never silently no-op --


def test_unknown_top_level_field_raises(tmp_path):
    cfg = Config()
    with pytest.raises(ValueError, match="nonesuch"):
        _build_test_config(cfg, _overrides(**{"nonesuch": 1}), _tmp(tmp_path))


def test_unknown_nested_field_raises(tmp_path):
    cfg = Config()
    with pytest.raises(ValueError, match="reflection.nonesuch"):
        _build_test_config(
            cfg, _overrides(**{"reflection.nonesuch": 1}), _tmp(tmp_path),
        )


def test_error_lists_available_fields(tmp_path):
    """The message should tell you what you *could* have written."""
    cfg = Config()
    with pytest.raises(ValueError, match="enabled"):
        _build_test_config(
            cfg, _overrides(**{"reflection.nonesuch": 1}), _tmp(tmp_path),
        )


def test_descending_into_non_dataclass_raises(tmp_path):
    """A dotted path through a scalar is a mistake, not a silent no-op."""
    cfg = Config()
    with pytest.raises(ValueError, match="not a config section"):
        _build_test_config(
            cfg, _overrides(**{"reflection.enabled.deeper": 1}), _tmp(tmp_path),
        )


def test_conflicting_paths_raise(tmp_path):
    """Setting a section and a field inside it is ambiguous — reject it."""
    cfg = Config()
    with pytest.raises(ValueError, match="conflict"):
        _build_test_config(
            cfg,
            _overrides(**{"reflection": {}, "reflection.enabled": False}),
            _tmp(tmp_path),
        )


@pytest.mark.parametrize("bad", [
    ["reflection.enabled=False"],
    # Falsy non-mappings must raise too. A bare `config_overrides:` in YAML
    # parses to None, and gating validation on truthiness would let that (and
    # `[]`, `0`, `""`) silently no-op — the exact failure this mechanism
    # exists to prevent. The repo has been bitten by null-YAML before; see
    # test_schedule_null_allowed_tools.
    None,
    [],
    0,
    "",
])
def test_config_overrides_must_be_a_mapping(tmp_path, bad):
    cfg = Config()
    with pytest.raises(ValueError, match="must be a mapping"):
        _build_test_config(
            cfg, {"setup": {"config_overrides": bad}}, _tmp(tmp_path),
        )


def test_empty_mapping_is_an_explicit_noop(tmp_path):
    """`config_overrides: {}` is unambiguous — the author wrote no paths."""
    cfg = Config()
    out = _build_test_config(
        cfg, {"setup": {"config_overrides": {}}}, _tmp(tmp_path),
    )
    assert out.reflection.enabled == cfg.reflection.enabled


def test_null_setup_is_treated_as_empty(tmp_path):
    """`setup:` with nothing under it is a natural authoring state, not an
    error — but it must not crash the runner."""
    cfg = Config()
    out = _build_test_config(cfg, {"setup": None}, _tmp(tmp_path))
    assert out.agent.data_home == _tmp(tmp_path)


def test_non_mapping_setup_raises(tmp_path):
    cfg = Config()
    with pytest.raises(ValueError, match="setup must be a mapping"):
        _build_test_config(cfg, {"setup": ["reflection_enabled"]}, _tmp(tmp_path))


def test_all_setup_consumers_tolerate_null_setup():
    """Every reader of the `setup` block goes through the same normalizer.

    Regression guard for the extraction: `_build_test_config` alone handling
    `setup: None` would still leave the other readers raising AttributeError
    on `.get()`.
    """
    assert _setup_of({"setup": None}) == {}
    assert _setup_of({}) == {}
    assert _seed_conversation_history(Config(), {"setup": None}) == []


# -- removed bespoke keys fail loudly rather than silently no-opping --


@pytest.mark.parametrize("old,new", sorted(_REMOVED_SETUP_KEYS.items()))
def test_removed_setup_keys_raise_with_migration_hint(tmp_path, old, new):
    """A YAML still using the old key must fail, not quietly run unmodified.

    Silently ignoring it would produce a green test measuring the wrong
    config — exactly what config_overrides exists to prevent.

    Parametrized off the real dict rather than a hand-listed pair, so a
    future removed key is covered on arrival.
    """
    cfg = Config()
    with pytest.raises(ValueError, match=new):
        _build_test_config(cfg, {"setup": {old: 1}}, _tmp(tmp_path))


# -- unknown setup keys fail loudly (#661) --


def test_unknown_setup_key_raises(tmp_path):
    """A typo'd setup key must not silently no-op.

    `workspace_file` (missing the 's') would otherwise return the default
    from `.get()`, the fixture would never be seeded, and the case would
    fail for a confusing reason — or pass for the wrong one.
    """
    cfg = Config()
    with pytest.raises(ValueError, match="workspace_file"):
        _build_test_config(
            cfg, {"setup": {"workspace_file": {"a.md": "x"}}}, _tmp(tmp_path),
        )


def test_unknown_setup_key_error_lists_valid_keys(tmp_path):
    cfg = Config()
    with pytest.raises(ValueError, match="workspace_files"):
        _build_test_config(cfg, {"setup": {"nonesuch": 1}}, _tmp(tmp_path))


@pytest.mark.parametrize("key", [
    # PyYAML resolves `on:` / `no:` / `yes:` / `off:` to booleans, so a
    # plausible typo yields a non-string key. Formatting the error message
    # with `', '.join(sorted(...))` would then raise TypeError instead of
    # the intended ValueError, and mixed types break `sorted` outright.
    True,
    False,
    1,
])
def test_non_string_setup_key_raises_valueerror(tmp_path, key):
    """Validation must fail with a clear ValueError, never a TypeError."""
    cfg = Config()
    with pytest.raises(ValueError, match="unknown setup key"):
        _build_test_config(cfg, {"setup": {key: 1}}, _tmp(tmp_path))


def test_mixed_type_unknown_keys_raise_valueerror(tmp_path):
    """Mixed str/non-str unknown keys must not blow up in `sorted`."""
    cfg = Config()
    with pytest.raises(ValueError, match="unknown setup key"):
        _build_test_config(
            cfg, {"setup": {"nonesuch": 1, 2: "x"}}, _tmp(tmp_path),
        )


def test_removed_keys_keep_their_migration_hint(tmp_path):
    """Removed keys must give the migration message, not generic 'unknown'.

    Both checks live in `_setup_of`, so ordering matters: the specific hint
    has to win over the catch-all.
    """
    cfg = Config()
    with pytest.raises(ValueError, match="config_overrides"):
        _build_test_config(
            cfg, {"setup": {"reflection_enabled": False}}, _tmp(tmp_path),
        )


def test_all_known_keys_accepted(tmp_path):
    """Every documented key passes validation.

    Guards against the allowlist drifting narrower than the readers.
    """
    setup = {
        "skills": [],
        "memories": [],
        "workspace_files": {},
        "conversation_history": [],
        "embeddings_fixture": "",
        "auto_confirm": True,
        "config_overrides": {},
    }
    assert set(setup) == set(_KNOWN_SETUP_KEYS)
    _build_test_config(Config(), {"setup": setup}, _tmp(tmp_path))


def test_known_setup_keys_match_docs():
    """`_KNOWN_SETUP_KEYS` must match the docs/eval-loop.md setup table.

    The allowlist is hand-maintained — the keys are consumed by five
    different functions, so there's nothing to introspect. This is its
    keeper: adding a key requires touching both the code and the table,
    and forgetting either fails here rather than rotting silently.
    """
    doc = (_REPO_ROOT / "docs" / "eval-loop.md").read_text()
    documented = set(re.findall(r"^\| `setup\.(\w+)`", doc, re.M))
    assert documented == set(_KNOWN_SETUP_KEYS), (
        f"docs-only: {sorted(documented - set(_KNOWN_SETUP_KEYS))}, "
        f"code-only: {sorted(set(_KNOWN_SETUP_KEYS) - documented)}"
    )


def _iter_eval_cases():
    root = _REPO_ROOT / "evals"
    for path in sorted(root.rglob("*.yaml")):
        cases = yaml.safe_load(path.read_text()) or []
        if not isinstance(cases, list):
            continue
        for case in cases:
            if isinstance(case, dict):
                yield path, case


def test_every_eval_yaml_setup_validates(tmp_path):
    """Run the production validator over every real eval case.

    Covers unknown keys, removed keys, non-mapping setup blocks, and
    unresolvable `config_overrides` paths in one pass — a typo'd anything
    fails here, free and instant, instead of partway through a paid eval
    run.

    Deliberately calls `_build_test_config` rather than re-checking
    `_KNOWN_SETUP_KEYS` by hand: reimplementing the rules in the test lets
    the two drift, and a hand-rolled `for key in setup` loop breaks on the
    very malformed input it is supposed to report (unhashable keys, a
    `setup` that is a list).
    """
    failures = []
    with_overrides = 0
    for path, case in _iter_eval_cases():
        setup = case.get("setup")
        if isinstance(setup, dict) and "config_overrides" in setup:
            with_overrides += 1
        try:
            _build_test_config(Config(), case, _tmp(tmp_path))
        except ValueError as exc:
            failures.append(f"{path.name}::{case.get('name')}: {exc}")
    assert not failures, "invalid setup in eval YAML:\n" + "\n".join(failures)
    # Guard against the walk going vacuous if the suite is restructured.
    assert with_overrides, "expected at least one case using config_overrides"


# -- dict-valued leaves are values, not further nesting --


def test_dict_value_is_assigned_not_traversed(tmp_path):
    """A dict on the right-hand side is a literal value.

    Nesting is expressed by dots in the key, so ``skills`` (a plain dict
    field) can be set wholesale without the walker trying to recurse into it.
    """
    cfg = Config()
    out = _build_test_config(
        cfg, _overrides(**{"skills": {"demo": {"enabled": True}}}), _tmp(tmp_path),
    )
    assert out.skills == {"demo": {"enabled": True}}
