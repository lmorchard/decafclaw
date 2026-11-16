"""Per-conversation shell approval rules panel endpoints (#1159, #1267)."""

import pytest

from decafclaw.config_types import ModelConfig, ShellConfig
from decafclaw.confirmations import ConfirmationAction, ConfirmationRequest
from decafclaw.conversation_manager import ConversationManager
from decafclaw.events import EventBus
from decafclaw.http_server import create_app
from decafclaw.web.auth import create_token
from decafclaw.web.conversations import ConversationIndex


async def _login(client, app) -> None:
    token = create_token(app.state.config, "testuser")
    resp = await client.post("/api/auth/login", json={"token": token})
    assert resp.status_code == 200
    client.cookies = resp.cookies


@pytest.fixture
def approval_config(http_config):
    http_config.shell = ShellConfig(aux_approval_enabled=True)
    return http_config


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def manager(approval_config, bus):
    return ConversationManager(approval_config, bus)


@pytest.fixture
def app(manager, bus):
    return create_app(manager.config, bus, manager=manager)


@pytest.fixture
def conv(approval_config):
    """A web conversation owned by the authenticated user."""
    index = ConversationIndex(approval_config)
    return index.create("testuser", "Approval Panel")


@pytest.mark.asyncio
async def test_conversation_shell_approval_get_returns_defaults(client, approval_config, manager, conv, app):
    await _login(client, app)
    resp = await client.get(f"/api/conversations/{conv.conv_id}/shell-approval")
    assert resp.status_code == 200
    data = resp.json()
    assert data["conv_id"] == conv.conv_id
    assert data["status"] == "idle"
    assert data["conversation_rules"] == []
    assert data["presets"] == []
    assert data["config_guidance"] == ""
    assert data["cached_patterns"] == []


@pytest.mark.asyncio
async def test_conversation_shell_approval_get_reflects_state_and_tags_presets(
    client, approval_config, manager, conv, app
):
    st = manager.ensure_state(conv.conv_id)
    st.persisted.aux_approval_guidance = ["Auto-approve make test in this repo"]
    st.persisted.active_aux_approval_presets = ["custom_preset"]
    st.persisted.disabled_aux_approval_presets = ["developer"]
    st.persisted.llm_approved_shell_patterns = ["pytest *"]
    approval_config.shell.active_aux_approval_presets = ["developer"]
    # Materialize a custom preset through the shared store so the panel can tag it.
    from decafclaw.shell_presets import ShellPresetStore

    await ShellPresetStore(approval_config).save_preset("custom_preset", ["Auto-approve make test in this repo"])

    await _login(client, app)
    resp = await client.get(f"/api/conversations/{conv.conv_id}/shell-approval")
    assert resp.status_code == 200
    data = resp.json()
    assert data["conversation_rules"] == ["Auto-approve make test in this repo"]
    assert data["cached_patterns"] == ["pytest *"]

    presets = {p["name"]: p for p in data["presets"]}
    # Disabled presets are excluded even when config-activated.
    assert "developer" not in presets
    custom = presets.get("custom_preset")
    assert custom is not None
    assert custom["source"] == "custom"
    assert custom["activated_via"] == ["session"]
    assert custom["active"] is True


@pytest.mark.asyncio
async def test_conversation_shell_approval_get_includes_config_activated_preset(
    client, approval_config, manager, conv, app
):
    st = manager.ensure_state(conv.conv_id)
    st.persisted.disabled_aux_approval_presets = []
    approval_config.shell.active_aux_approval_presets = ["developer"]

    await _login(client, app)
    resp = await client.get(f"/api/conversations/{conv.conv_id}/shell-approval")
    assert resp.status_code == 200
    data = resp.json()
    presets = {p["name"]: p for p in data["presets"]}
    dev = presets.get("developer")
    assert dev is not None
    assert dev["source"] == "builtin"
    assert dev["activated_via"] == ["config"]
    assert dev["active"] is True


@pytest.mark.asyncio
async def test_conversation_shell_approval_put_adds_rule_and_clears_cache(client, approval_config, manager, conv, app):
    st = manager.ensure_state(conv.conv_id)
    st.persisted.aux_approval_guidance = ["Existing rule"]
    st.persisted.llm_approved_shell_patterns = ["pytest *"]

    await _login(client, app)
    resp = await client.put(
        f"/api/conversations/{conv.conv_id}/shell-approval/rules", json={"rule": "Auto-approve ruff check"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["conversation_rules"] == ["Existing rule", "Auto-approve ruff check"]
    # Editing a rule clears the cached reviewer approval patterns.
    assert st.persisted.llm_approved_shell_patterns == []


@pytest.mark.asyncio
async def test_conversation_shell_approval_put_returns_409_when_busy(client, approval_config, manager, conv, app):
    st = manager.ensure_state(conv.conv_id)
    st.busy = True

    await _login(client, app)
    resp = await client.put(f"/api/conversations/{conv.conv_id}/shell-approval/rules", json={"rule": "Auto-approve ls"})
    assert resp.status_code == 409
    assert "busy" in resp.json()["error"]


@pytest.mark.asyncio
async def test_conversation_shell_approval_put_returns_409_when_waiting(client, approval_config, manager, conv, app):
    st = manager.ensure_state(conv.conv_id)
    st.pending_confirmation = ConfirmationRequest(action_type=ConfirmationAction.RUN_SHELL_COMMAND)

    await _login(client, app)
    resp = await client.put(f"/api/conversations/{conv.conv_id}/shell-approval/rules", json={"rule": "Auto-approve ls"})
    assert resp.status_code == 409
    assert "waiting" in resp.json()["error"]


@pytest.mark.asyncio
async def test_conversation_shell_approval_delete_rule_removes_and_clears_cache(
    client, approval_config, manager, conv, app
):
    st = manager.ensure_state(conv.conv_id)
    st.persisted.aux_approval_guidance = ["Keep me", "Drop me"]
    st.persisted.llm_approved_shell_patterns = ["pytest *"]

    await _login(client, app)
    resp = await client.request(
        "DELETE", f"/api/conversations/{conv.conv_id}/shell-approval/rules", json={"rule": "Drop me"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["conversation_rules"] == ["Keep me"]
    assert st.persisted.llm_approved_shell_patterns == []


@pytest.mark.asyncio
async def test_conversation_shell_approval_delete_pattern_removes_cached_pattern(
    client, approval_config, manager, conv, app
):
    st = manager.ensure_state(conv.conv_id)
    st.persisted.llm_approved_shell_patterns = ["pytest *", "ruff *"]

    await _login(client, app)
    resp = await client.request(
        "DELETE",
        f"/api/conversations/{conv.conv_id}/shell-approval/patterns",
        json={"pattern": "ruff *"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["cached_patterns"] == ["pytest *"]
    assert st.persisted.llm_approved_shell_patterns == ["pytest *"]


@pytest.mark.asyncio
async def test_conversation_shell_approval_promote_rejects_builtin_and_config_presets(
    client, approval_config, manager, conv, app
):
    approval_config.shell.aux_approval_presets = {"cfg_preset": "Auto-approve echo hello"}

    await _login(client, app)
    resp = await client.post(
        f"/api/conversations/{conv.conv_id}/shell-approval/promote",
        json={"rule": "Auto-approve cargo test", "preset": "developer"},
    )
    assert resp.status_code == 400
    assert "cannot promote to built-in or config preset" in resp.json()["error"]

    resp = await client.post(
        f"/api/conversations/{conv.conv_id}/shell-approval/promote",
        json={"rule": "Auto-approve cargo test", "preset": "cfg_preset"},
    )
    assert resp.status_code == 400
    assert "cannot promote to built-in or config preset" in resp.json()["error"]


@pytest.mark.asyncio
async def test_conversation_shell_approval_promote_creates_and_appends_custom_preset(
    client, approval_config, manager, conv, app, bus
):
    await _login(client, app)
    resp = await client.post(
        f"/api/conversations/{conv.conv_id}/shell-approval/promote",
        json={"rule": "Auto-approve cargo test", "preset": "issue_1159_panel"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["is_new"] is True

    resp2 = await client.post(
        f"/api/conversations/{conv.conv_id}/shell-approval/promote",
        json={"rule": "Auto-approve cargo test", "preset": "issue_1159_panel"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["is_new"] is False
    assert len(resp2.json()["rules"]) == 1

    presets_resp = await client.get("/api/shell-approval/presets")
    assert presets_resp.status_code == 200
    presets = presets_resp.json()
    created = next((p for p in presets if p["name"] == "issue_1159_panel"), None)
    assert created is not None
    assert created["source"] == "custom"
    assert created["rules"].count("Auto-approve cargo test") == 1


# ---------------------------------------------------------------------------
# Rules-replace endpoint (#1267) — bulk-edit draft save path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rules_replace_full_replacement_and_clears_cache(client, approval_config, manager, conv, app):
    st = manager.ensure_state(conv.conv_id)
    st.persisted.aux_approval_guidance = ["Old rule A", "Old rule B"]
    st.persisted.llm_approved_shell_patterns = ["pytest *"]

    await _login(client, app)
    resp = await client.put(
        f"/api/conversations/{conv.conv_id}/shell-approval/rules/replace",
        json={"rules": ["New rule 1", "New rule 2", "New rule 3"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    # The full list is replaced atomically — old rules are gone, order preserved.
    assert data["conversation_rules"] == ["New rule 1", "New rule 2", "New rule 3"]
    # The on-disk state holds exactly the saved list…
    assert st.persisted.aux_approval_guidance == ["New rule 1", "New rule 2", "New rule 3"]
    # …and the cached reviewer patterns are cleared, same as single-rule edits.
    assert st.persisted.llm_approved_shell_patterns == []


@pytest.mark.asyncio
async def test_rules_replace_empty_list_clears_all(client, approval_config, manager, conv, app):
    st = manager.ensure_state(conv.conv_id)
    st.persisted.aux_approval_guidance = ["Rule A", "Rule B"]

    await _login(client, app)
    resp = await client.put(f"/api/conversations/{conv.conv_id}/shell-approval/rules/replace", json={"rules": []})
    assert resp.status_code == 200
    assert resp.json()["conversation_rules"] == []
    assert st.persisted.aux_approval_guidance == []


@pytest.mark.asyncio
async def test_rules_replace_strips_and_drops_blank_rows(client, approval_config, manager, conv, app):
    st = manager.ensure_state(conv.conv_id)

    await _login(client, app)
    resp = await client.put(
        f"/api/conversations/{conv.conv_id}/shell-approval/rules/replace",
        json={"rules": ["  Keep me  ", "", "   "]},
    )
    assert resp.status_code == 200
    # Whitespace is trimmed; blanked rows are dropped (a blank row removes a rule).
    assert resp.json()["conversation_rules"] == ["Keep me"]


@pytest.mark.asyncio
async def test_rules_replace_returns_409_when_busy(client, approval_config, manager, conv, app):
    st = manager.ensure_state(conv.conv_id)
    st.busy = True

    await _login(client, app)
    resp = await client.put(f"/api/conversations/{conv.conv_id}/shell-approval/rules/replace", json={"rules": ["x"]})
    assert resp.status_code == 409
    assert "busy" in resp.json()["error"]


@pytest.mark.asyncio
async def test_rules_replace_returns_409_when_waiting(client, approval_config, manager, conv, app):
    st = manager.ensure_state(conv.conv_id)
    st.pending_confirmation = ConfirmationRequest(action_type=ConfirmationAction.RUN_SHELL_COMMAND)

    await _login(client, app)
    resp = await client.put(f"/api/conversations/{conv.conv_id}/shell-approval/rules/replace", json={"rules": ["x"]})
    assert resp.status_code == 409
    assert "waiting" in resp.json()["error"]


@pytest.mark.asyncio
async def test_rules_replace_rejects_non_list_body(client, approval_config, manager, conv, app):
    await _login(client, app)
    resp = await client.put(
        f"/api/conversations/{conv.conv_id}/shell-approval/rules/replace", json={"rules": "not-a-list"}
    )
    assert resp.status_code == 400
    assert "list" in resp.json()["error"]


@pytest.mark.asyncio
async def test_rules_replace_rejects_non_string_item(client, approval_config, manager, conv, app):
    st = manager.ensure_state(conv.conv_id)
    st.persisted.aux_approval_guidance = ["Existing"]

    await _login(client, app)
    resp = await client.put(
        f"/api/conversations/{conv.conv_id}/shell-approval/rules/replace", json={"rules": ["ok", 42]}
    )
    assert resp.status_code == 400
    # Rejected bodies must not mutate state.
    assert st.persisted.aux_approval_guidance == ["Existing"]


# ---------------------------------------------------------------------------
# Condense endpoint (#1267) — LLM proposal, never mutates state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_condense_returns_proposal_and_never_mutates(client, approval_config, manager, conv, app, stub_llm):
    st = manager.ensure_state(conv.conv_id)
    st.persisted.aux_approval_guidance = ["Approve make test", "Approve make lint", "Approve ruff"]
    st.persisted.llm_approved_shell_patterns = ["pytest *"]

    stub_llm.response = {
        "content": '{"rules": ["Approve make test and lint", "Approve ruff"]}',
        "tool_calls": None,
        "role": "assistant",
        "usage": {},
    }

    await _login(client, app)
    resp = await client.post(f"/api/conversations/{conv.conv_id}/shell-approval/condense")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["rules"] == ["Approve make test and lint", "Approve ruff"]

    # Condensation is a proposal: rules on disk and cached patterns are untouched.
    assert st.persisted.aux_approval_guidance == [
        "Approve make test",
        "Approve make lint",
        "Approve ruff",
    ]
    assert st.persisted.llm_approved_shell_patterns == ["pytest *"]


@pytest.mark.asyncio
async def test_condense_strips_json_fence(client, approval_config, manager, conv, app, stub_llm):
    st = manager.ensure_state(conv.conv_id)
    st.persisted.aux_approval_guidance = ["Approve make test", "Approve make lint"]

    stub_llm.response = {
        "content": '```json\n{"rules": ["Approve make test and lint"]}\n```',
        "tool_calls": None,
        "role": "assistant",
        "usage": {},
    }

    await _login(client, app)
    resp = await client.post(f"/api/conversations/{conv.conv_id}/shell-approval/condense")
    assert resp.status_code == 200
    assert resp.json()["rules"] == ["Approve make test and lint"]


@pytest.mark.asyncio
async def test_condense_parse_failure_returns_error_and_keeps_state(
    client, approval_config, manager, conv, app, stub_llm
):
    st = manager.ensure_state(conv.conv_id)
    st.persisted.aux_approval_guidance = ["Approve make test"]
    st.persisted.llm_approved_shell_patterns = ["pytest *"]

    # Not valid JSON — the model rambled.
    stub_llm.response = {
        "content": "Sure, here are some condensed rules: approve make test.",
        "tool_calls": None,
        "role": "assistant",
        "usage": {},
    }

    await _login(client, app)
    resp = await client.post(f"/api/conversations/{conv.conv_id}/shell-approval/condense")
    assert resp.status_code == 400
    assert "unparseable" in resp.json()["error"]
    # Rules and patterns are unchanged on a parse failure.
    assert st.persisted.aux_approval_guidance == ["Approve make test"]
    assert st.persisted.llm_approved_shell_patterns == ["pytest *"]


@pytest.mark.asyncio
async def test_condense_missing_rules_key_returns_error(client, approval_config, manager, conv, app, stub_llm):
    st = manager.ensure_state(conv.conv_id)
    st.persisted.aux_approval_guidance = ["Approve make test"]

    # Valid JSON but no "rules" list.
    stub_llm.response = {
        "content": '{"summary": "done"}',
        "tool_calls": None,
        "role": "assistant",
        "usage": {},
    }

    await _login(client, app)
    resp = await client.post(f"/api/conversations/{conv.conv_id}/shell-approval/condense")
    assert resp.status_code == 400
    assert "rules" in resp.json()["error"]


@pytest.mark.asyncio
async def test_condense_model_error_returns_502(client, approval_config, manager, conv, app, stub_llm, monkeypatch):
    st = manager.ensure_state(conv.conv_id)
    st.persisted.aux_approval_guidance = ["Approve make test"]

    async def _boom(config=None, messages=None, tools=None, *args, **kwargs):
        raise RuntimeError("provider exploded")

    # Override the stub so the one-shot call raises; monkeypatch undoes it at teardown.
    import decafclaw.llm as llm_mod

    monkeypatch.setattr(llm_mod, "call_llm", _boom)
    await _login(client, app)
    resp = await client.post(f"/api/conversations/{conv.conv_id}/shell-approval/condense")
    assert resp.status_code == 502
    assert "condensation failed" in resp.json()["error"]


@pytest.mark.asyncio
async def test_condense_409_when_busy(client, approval_config, manager, conv, app, stub_llm):
    st = manager.ensure_state(conv.conv_id)
    st.busy = True

    await _login(client, app)
    resp = await client.post(f"/api/conversations/{conv.conv_id}/shell-approval/condense")
    assert resp.status_code == 409
    assert "busy" in resp.json()["error"]


@pytest.mark.asyncio
async def test_condense_no_rules_returns_400(client, approval_config, manager, conv, app, stub_llm):
    st = manager.ensure_state(conv.conv_id)
    st.persisted.aux_approval_guidance = []

    await _login(client, app)
    resp = await client.post(f"/api/conversations/{conv.conv_id}/shell-approval/condense")
    assert resp.status_code == 400
    assert "no rules" in resp.json()["error"]


@pytest.mark.asyncio
async def test_condense_uses_configured_model(client, approval_config, manager, conv, app, stub_llm):
    """Success condition 4: with shell.condense_model set to a model_configs key,
    condensation resolves to that model."""
    st = manager.ensure_state(conv.conv_id)
    st.persisted.aux_approval_guidance = ["Approve make test", "Approve make lint"]
    approval_config.model_configs["condense-model"] = ModelConfig(provider="local", model="condense")
    approval_config.shell.condense_model = "condense-model"

    stub_llm.response = {
        "content": '{"rules": ["Approve make test and lint"]}',
        "tool_calls": None,
        "role": "assistant",
        "usage": {},
    }

    await _login(client, app)
    resp = await client.post(f"/api/conversations/{conv.conv_id}/shell-approval/condense")
    assert resp.status_code == 200
    # The stubbed call recorded the resolved model name.
    assert len(stub_llm) == 1
    assert stub_llm[0]["model_name"] == "condense-model"


@pytest.mark.asyncio
async def test_condense_unset_uses_active_conversation_model(client, approval_config, manager, conv, app, stub_llm):
    """Success condition 4: with shell.condense_model unset, condensation rides on
    the active conversation model (then the effective default)."""
    st = manager.ensure_state(conv.conv_id)
    st.persisted.aux_approval_guidance = ["Approve make test"]
    st.persisted.active_model = "active-model"
    approval_config.model_configs["active-model"] = ModelConfig(provider="local", model="active")

    stub_llm.response = {
        "content": '{"rules": ["Approve make test"]}',
        "tool_calls": None,
        "role": "assistant",
        "usage": {},
    }

    await _login(client, app)
    resp = await client.post(f"/api/conversations/{conv.conv_id}/shell-approval/condense")
    assert resp.status_code == 200
    assert stub_llm[0]["model_name"] == "active-model"
