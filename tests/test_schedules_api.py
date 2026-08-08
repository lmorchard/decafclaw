"""REST API schedule tests for issue #735 verification."""

import pytest

pytest_plugins = ["tests.test_web_schedules_api"]


@pytest.mark.asyncio
async def test_update_schedule_rejects_unrecognized_patch_keys(client):
    r = await client.put("/api/schedules/dream", json={"allowed_tool": ["vault_read"]})
    assert r.status_code == 400
    assert "unrecognized patch key" in r.json()["error"].lower()
    assert "allowed_tool" in r.json()["error"]
