# Acceptance Checks

## C1: Production loadout matches active/deferred classification
**Command**: `uv run pytest tests/test_eval_tool_choice_loadout.py -k test_production_loadout_classification`
**Expected to pass**: Yes

## C2: tool_choice runner includes tool_search and deferred_tools block
**Command**: `uv run pytest tests/test_eval_tool_choice_runner.py -k test_run_case_production_mode`
**Expected to pass**: Yes

## Guards
**G1**: Existing loadout contract holds
**Command**: `uv run pytest tests/test_eval_tool_choice_loadout.py`
**Expected**: Pass

**G2**: Default mode remains full loadout
**Command**: `uv run pytest tests/test_eval_tool_choice_loadout.py -k test_default_mode_full_loadout`
**Expected**: Pass

**G3**: Capability-tier gating stays enforced
**Command**: `uv run pytest tests/test_skill_native_tools_tier.py`
**Expected**: Pass

**G4**: Full test suite
**Command**: `make test`
**Expected**: Pass
