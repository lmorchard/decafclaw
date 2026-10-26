import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from decafclaw.config_types import ShellConfig
from decafclaw.http_server import create_app
from decafclaw.shell_presets import DEFAULT_AUX_APPROVAL_PRESETS, ShellPresetStore


@pytest.fixture
def preset_config(http_config):
    http_config.shell = ShellConfig(aux_approval_enabled=True)
    return http_config


@pytest.fixture
def app(preset_config, bus):
    return create_app(preset_config, bus)


@pytest.mark.asyncio
async def test_presets_list_builtin_and_config(client, preset_config):
    preset_config.shell.aux_approval_presets = {"my_config_preset": "Auto-approve make test"}
    resp = await client.get("/api/shell-approval/presets")
    assert resp.status_code == 200
    presets = resp.json()
    names = {p["name"] for p in presets}
    assert "developer" in names
    assert "github" in names
    assert "my_config_preset" in names

    dev = next(p for p in presets if p["name"] == "developer")
    assert dev["source"] == "builtin"
    assert dev["shadows_config"] is False
    assert len(dev["rules"]) > 0

    cfg = next(p for p in presets if p["name"] == "my_config_preset")
    assert cfg["source"] == "config"
    assert cfg["shadows_config"] is False
    assert cfg["rules"] == ["Auto-approve make test"]


@pytest.mark.asyncio
async def test_presets_put_and_delete_round_trip(client, preset_config):
    # 1. Create custom preset
    resp = await client.put(
        "/api/shell-approval/presets/my_custom",
        json={"rules": ["Auto-approve pytest", "Auto-approve ruff check"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["name"] == "my_custom"
    assert data["rules"] == ["Auto-approve pytest", "Auto-approve ruff check"]

    # Verify in list
    resp = await client.get("/api/shell-approval/presets")
    presets = resp.json()
    custom = next(p for p in presets if p["name"] == "my_custom")
    assert custom["source"] == "custom"
    assert custom["rules"] == ["Auto-approve pytest", "Auto-approve ruff check"]

    # 2. Update custom preset
    resp = await client.put(
        "/api/shell-approval/presets/my_custom",
        json={"rules": ["Auto-approve pytest tests/unit"]},
    )
    assert resp.status_code == 200
    assert resp.json()["rules"] == ["Auto-approve pytest tests/unit"]

    # 3. Delete custom preset
    resp = await client.delete("/api/shell-approval/presets/my_custom")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Verify not in list
    resp = await client.get("/api/shell-approval/presets")
    assert "my_custom" not in {p["name"] for p in resp.json()}


@pytest.mark.asyncio
async def test_presets_put_or_delete_builtin_or_config_returns_400(client, preset_config):
    preset_config.shell.aux_approval_presets = {"cfg_preset": "Auto-approve echo hello"}

    # Built-in PUT
    resp = await client.put("/api/shell-approval/presets/developer", json={"rules": ["Rule 1"]})
    assert resp.status_code == 400
    assert "Cannot overwrite built-in preset" in resp.json()["error"]

    # Built-in DELETE
    resp = await client.delete("/api/shell-approval/presets/developer")
    assert resp.status_code == 400
    assert "Cannot delete built-in preset" in resp.json()["error"]

    # Config PUT
    resp = await client.put("/api/shell-approval/presets/cfg_preset", json={"rules": ["Rule 1"]})
    assert resp.status_code == 400
    assert "Cannot overwrite config preset" in resp.json()["error"]

    # Config DELETE
    resp = await client.delete("/api/shell-approval/presets/cfg_preset")
    assert resp.status_code == 400
    assert "Cannot delete config preset" in resp.json()["error"]

    # Verify file is not created or modified
    file_path = preset_config.agent_path / "shell_approval_presets.json"
    assert not file_path.exists()


@pytest.mark.asyncio
async def test_presets_delete_nonexistent_returns_404(client):
    resp = await client.delete("/api/shell-approval/presets/does_not_exist")
    assert resp.status_code == 404
    assert "not found" in resp.json()["error"]


@pytest.mark.asyncio
async def test_concurrent_puts_survive(client, preset_config):
    resp1, resp2 = await asyncio.gather(
        client.put("/api/shell-approval/presets/preset_a", json={"rules": ["Rule A1", "Rule A2"]}),
        client.put("/api/shell-approval/presets/preset_b", json={"rules": ["Rule B1"]}),
    )
    assert resp1.status_code == 200
    assert resp2.status_code == 200

    file_path = preset_config.agent_path / "shell_approval_presets.json"
    assert file_path.exists()
    disk_data = json.loads(file_path.read_text(encoding="utf-8"))
    assert "preset_a" in disk_data
    assert "preset_b" in disk_data


@pytest.mark.asyncio
async def test_malformed_presets_file_returns_error_and_unchanged(client, preset_config):
    file_path = preset_config.agent_path / "shell_approval_presets.json"

    # 1. Invalid JSON
    corrupt_content = "{ this is not valid json"
    file_path.write_text(corrupt_content, encoding="utf-8")

    resp_get = await client.get("/api/shell-approval/presets")
    assert resp_get.status_code == 500
    assert "Corrupt or unreadable presets file" in resp_get.json()["error"]

    resp_put = await client.put("/api/shell-approval/presets/new_preset", json={"rules": ["Rule 1"]})
    assert resp_put.status_code == 500
    assert "Corrupt or unreadable presets file" in resp_put.json()["error"]
    assert file_path.read_text(encoding="utf-8") == corrupt_content

    # 2. Non-dict root
    list_root = json.dumps(["an", "array"])
    file_path.write_text(list_root, encoding="utf-8")
    resp_get_arr = await client.get("/api/shell-approval/presets")
    assert resp_get_arr.status_code == 500
    assert "Corrupt or unreadable presets file" in resp_get_arr.json()["error"]
    assert file_path.read_text(encoding="utf-8") == list_root

    # 3. Non-string keys/values
    non_str_val = json.dumps({"key": 12345})
    file_path.write_text(non_str_val, encoding="utf-8")
    resp_get_val = await client.get("/api/shell-approval/presets")
    assert resp_get_val.status_code == 500
    assert "Corrupt or unreadable presets file" in resp_get_val.json()["error"]
    assert file_path.read_text(encoding="utf-8") == non_str_val


@pytest.mark.asyncio
async def test_editing_preset_invalidates_active_conversation_cache(preset_config, bus):
    from decafclaw.conversation_manager import ConversationManager, ConversationState
    from decafclaw.web.auth import create_token

    manager = ConversationManager(preset_config, bus)
    conv1 = manager.ensure_state("conv1")
    conv1.persisted.active_aux_approval_presets = ["my_preset", "other_preset"]
    conv1.persisted.llm_approved_shell_patterns = ["pytest *", "ruff *"]

    conv2 = manager.ensure_state("conv2")
    conv2.persisted.active_aux_approval_presets = ["other_preset"]
    conv2.persisted.llm_approved_shell_patterns = ["npm test *"]

    app = create_app(preset_config, bus, manager=manager)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        token = create_token(preset_config, "testuser")
        resp_login = await c.post("/api/auth/login", json={"token": token})
        c.cookies = resp_login.cookies
        resp = await c.put(
            "/api/shell-approval/presets/my_preset",
            json={"rules": ["Auto-approve pytest only"]},
        )
        assert resp.status_code == 200

    # conv1 has my_preset active -> cleared
    assert conv1.persisted.llm_approved_shell_patterns == []
    # conv2 does NOT have my_preset active -> left alone
    assert conv2.persisted.llm_approved_shell_patterns == ["npm test *"]


@pytest.mark.asyncio
async def test_editing_preset_invalidates_inherited_config_conversations(preset_config, bus):
    from decafclaw.conversation_manager import ConversationManager
    from decafclaw.web.auth import create_token

    # Preset is globally active via config.shell.active_aux_approval_presets
    preset_config.shell.active_aux_approval_presets = ["global_preset"]

    manager = ConversationManager(preset_config, bus)
    conv1 = manager.ensure_state("conv1")
    # Inherits global_preset; has cached approved patterns
    conv1.persisted.llm_approved_shell_patterns = ["pytest *"]

    conv2 = manager.ensure_state("conv2")
    # Disabled global_preset in this conversation
    conv2.persisted.disabled_aux_approval_presets = ["global_preset"]
    conv2.persisted.llm_approved_shell_patterns = ["npm test *"]

    app = create_app(preset_config, bus, manager=manager)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        token = create_token(preset_config, "testuser")
        resp_login = await c.post("/api/auth/login", json={"token": token})
        c.cookies = resp_login.cookies
        resp = await c.put(
            "/api/shell-approval/presets/global_preset",
            json={"rules": ["Auto-approve updated"]},
        )
        assert resp.status_code == 200

    # conv1 has global_preset active via config -> cleared
    assert conv1.persisted.llm_approved_shell_patterns == []
    # conv2 disabled global_preset -> untouched
    assert conv2.persisted.llm_approved_shell_patterns == ["npm test *"]


@pytest.mark.asyncio
async def test_presets_list_populates_referenced_by(client, preset_config):
    preset_config.shell.active_aux_approval_presets = ["developer", "custom_active"]
    # Save custom_active to disk
    resp = await client.put(
        "/api/shell-approval/presets/custom_active",
        json={"rules": ["Auto-approve make check"]},
    )
    assert resp.status_code == 200

    resp_list = await client.get("/api/shell-approval/presets")
    assert resp_list.status_code == 200
    presets = resp_list.json()

    # developer is in mode 'dev' and config.shell.active_aux_approval_presets
    dev = next(p for p in presets if p["name"] == "developer")
    assert "mode 'dev'" in dev["referenced_by"]
    assert "config.shell.active_aux_approval_presets" in dev["referenced_by"]

    # custom_active is in config.shell.active_aux_approval_presets
    custom = next(p for p in presets if p["name"] == "custom_active")
    assert "config.shell.active_aux_approval_presets" in custom["referenced_by"]


@pytest.mark.asyncio
async def test_resolve_aux_approval_guidance_after_rest_roundtrip(client, preset_config):
    from decafclaw.context import Context, SkillState, ToolState
    from decafclaw.tools.shell_tools import resolve_aux_approval_guidance

    # Create preset via REST
    resp = await client.put(
        "/api/shell-approval/presets/team_rules",
        json={"rules": ["Auto-approve cargo test", "Auto-approve cargo clippy"]},
    )
    assert resp.status_code == 200

    ctx = MagicMock(spec=Context)
    ctx.config = preset_config
    ctx.tools = ToolState()
    ctx.tools.active_aux_approval_presets = ["team_rules"]
    ctx.tools.disabled_aux_approval_presets = []

    guidance = resolve_aux_approval_guidance(ctx)
    assert len(guidance) == 1
    assert "- Auto-approve cargo test" in guidance[0]
    assert "- Auto-approve cargo clippy" in guidance[0]
