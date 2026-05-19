"""Tests for /api/schedules REST endpoints."""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from decafclaw.events import EventBus
from decafclaw.http_server import create_app
from decafclaw.web.auth import create_token


@pytest.fixture
def http_config(config):
    config.http.enabled = True
    config.http.secret = "test-secret"
    config.http.host = "127.0.0.1"
    config.http.port = 18880
    config.http.base_url = ""
    config.agent_path.mkdir(parents=True, exist_ok=True)
    config.workspace_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def app(http_config, bus):
    return create_app(http_config, bus)


@pytest.fixture
async def client(app, http_config):
    """Client with a valid auth cookie."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        token = create_token(http_config, "testuser")
        resp = await c.post("/api/auth/login", json={"token": token})
        c.cookies = resp.cookies
        yield c


class TestSchedulesAPI:
    """REST endpoints for schedule listing and editing."""

    @pytest.mark.asyncio
    async def test_list_includes_bundled(self, client):
        r = await client.get("/api/schedules")
        assert r.status_code == 200
        names = {s["name"] for s in r.json()["schedules"]}
        assert "dream" in names
        assert "garden" in names
        assert "newsletter" in names

    @pytest.mark.asyncio
    async def test_list_shape(self, client):
        r = await client.get("/api/schedules")
        dream = next(s for s in r.json()["schedules"] if s["name"] == "dream")
        for key in ("name", "source_tier", "has_overlay", "enabled",
                    "schedule", "body", "next_run_iso"):
            assert key in dream, f"missing key: {key}"
        assert dream["source_tier"] == "bundled"
        assert dream["has_overlay"] is False
        assert dream["enabled"] is True

    @pytest.mark.asyncio
    async def test_put_creates_overlay(self, client, http_config):
        r = await client.put("/api/schedules/dream", json={"enabled": False})
        assert r.status_code == 200
        assert r.json()["schedule"]["enabled"] is False
        overlay = http_config.agent_path / "schedules" / "dream.md"
        assert overlay.exists()
        # Subsequent GET reflects overlay
        listed_r = await client.get("/api/schedules")
        listed = next(
            s for s in listed_r.json()["schedules"] if s["name"] == "dream"
        )
        assert listed["source_tier"] == "admin"
        assert listed["has_overlay"] is True
        assert listed["enabled"] is False

    @pytest.mark.asyncio
    async def test_put_preserves_unchanged_fields_in_overlay(self, client, http_config):
        await client.put("/api/schedules/dream", json={"schedule": "0 4 * * *"})
        overlay = http_config.agent_path / "schedules" / "dream.md"
        content = overlay.read_text()
        assert "required-skills" in content
        assert "vault" in content

    @pytest.mark.asyncio
    async def test_put_404_for_unknown_name(self, client):
        r = await client.put("/api/schedules/nonexistent", json={"enabled": False})
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_put_workspace_writes_in_place(self, client, http_config):
        ws_dir = http_config.workspace_path / "schedules"
        ws_dir.mkdir(parents=True, exist_ok=True)
        (ws_dir / "agent-task.md").write_text(
            "---\nschedule: '0 * * * *'\n---\nAgent self-scheduled.\n"
        )
        r = await client.put("/api/schedules/agent-task", json={"enabled": False})
        assert r.status_code == 200
        # Written in-place to workspace, not to admin overlay path
        assert (ws_dir / "agent-task.md").read_text() != ""
        assert "enabled: false" in (ws_dir / "agent-task.md").read_text()
        admin = http_config.agent_path / "schedules" / "agent-task.md"
        assert not admin.exists()

    @pytest.mark.asyncio
    async def test_delete_overlay_reverts(self, client, http_config):
        await client.put(
            "/api/schedules/dream",
            json={"enabled": False, "schedule": "0 4 * * *"},
        )
        r = await client.delete("/api/schedules/dream/overlay")
        assert r.status_code == 200
        listed_r = await client.get("/api/schedules")
        listed = next(
            s for s in listed_r.json()["schedules"] if s["name"] == "dream"
        )
        assert listed["source_tier"] == "bundled"
        assert listed["has_overlay"] is False
        assert listed["schedule"] == "0 3 * * *"
        assert listed["enabled"] is True

    @pytest.mark.asyncio
    async def test_delete_overlay_404_when_absent(self, client):
        r = await client.delete("/api/schedules/dream/overlay")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_list_returns_200_and_json(self, client):
        r = await client.get("/api/schedules")
        assert r.status_code == 200
        assert "schedules" in r.json()
        assert isinstance(r.json()["schedules"], list)

    @pytest.mark.asyncio
    async def test_delete_overlay_404_for_admin_standalone(self, client, http_config):
        """DELETE-overlay must NOT delete an admin standalone schedule that has
        no skill SCHEDULE.md fallback — the file must survive intact."""
        admin_dir = http_config.agent_path / "schedules"
        admin_dir.mkdir(parents=True, exist_ok=True)
        standalone = admin_dir / "admin-only.md"
        standalone.write_text(
            "---\nschedule: '0 * * * *'\n---\nAdmin-only standalone.\n"
        )
        r = await client.delete("/api/schedules/admin-only/overlay")
        assert r.status_code == 404
        # File MUST still exist — the handler must not have deleted it
        assert standalone.exists()
        assert "Admin-only standalone." in standalone.read_text()

    # -- Regression tests for code-review fixes ----------------------------

    @pytest.mark.asyncio
    async def test_put_400_on_invalid_cron(self, client, http_config):
        """Critical 1: invalid cron must return 400 without writing overlay."""
        r = await client.put("/api/schedules/dream", json={"schedule": "not-a-cron"})
        assert r.status_code == 400
        assert "invalid cron" in r.json()["error"].lower()
        overlay = http_config.agent_path / "schedules" / "dream.md"
        assert not overlay.exists(), "overlay must NOT be created on invalid cron"

    @pytest.mark.asyncio
    async def test_delete_overlay_400_on_unsafe_name(self, client):
        """Important 2: unsafe names must return 400, not 500."""
        r = await client.delete("/api/schedules/foo..bar/overlay")
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_put_null_field_is_no_op(self, client, http_config):
        """Important 3: null field in PUT body must be treated as 'leave unchanged'."""
        # Create overlay with enabled=False first
        r = await client.put("/api/schedules/dream", json={"enabled": False})
        assert r.status_code == 200
        assert r.json()["schedule"]["enabled"] is False
        # PUT with enabled=null; enabled should stay False, not flip
        r2 = await client.put("/api/schedules/dream", json={"enabled": None})
        assert r2.status_code == 200
        assert r2.json()["schedule"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_put_400_on_non_dict_body(self, client):
        """Important 4: non-dict JSON body must return 400."""
        r = await client.put("/api/schedules/dream", content=b"[1, 2, 3]",
                             headers={"Content-Type": "application/json"})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_put_400_on_invalid_json(self, client):
        """Important 4 (optional): malformed JSON must return 400."""
        r = await client.put("/api/schedules/dream", content=b"not valid json{",
                             headers={"Content-Type": "application/json"})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_put_accepts_content_as_alias_for_body(self, client):
        r = await client.put(
            "/api/schedules/dream",
            json={"content": "Edited via wiki-editor.", "modified": 1234567890},
        )
        assert r.status_code == 200
        assert r.json()["schedule"]["body"] == "Edited via wiki-editor."

    @pytest.mark.asyncio
    async def test_get_includes_modified_field(self, client):
        r = await client.get("/api/schedules")
        assert r.status_code == 200
        for s in r.json()["schedules"]:
            assert "modified" in s, f"missing 'modified' in schedule {s.get('name')}"
            assert isinstance(s["modified"], (int, float))

    @pytest.mark.asyncio
    async def test_get_single_schedule(self, client):
        r = await client.get("/api/schedules/dream")
        assert r.status_code == 200
        s = r.json()["schedule"]
        assert s["name"] == "dream"
        assert s["source_tier"] == "bundled"

    @pytest.mark.asyncio
    async def test_get_single_schedule_404(self, client):
        r = await client.get("/api/schedules/nonexistent")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_put_response_includes_modified(self, client):
        r = await client.put("/api/schedules/dream", json={"enabled": False})
        assert r.status_code == 200
        assert "modified" in r.json()["schedule"]
        assert isinstance(r.json()["schedule"]["modified"], (int, float))
        assert r.json()["schedule"]["modified"] > 0
        # wiki-editor reads data.modified at top level
        assert "modified" in r.json()
        assert isinstance(r.json()["modified"], (int, float))

    # -- Run now (POST /api/schedules/{name}/run) ------------------------------

    @pytest.mark.asyncio
    async def test_run_endpoint_starts_task(self, client, http_config, monkeypatch):
        """POST /run starts the task in the background and returns 202 + conv_id."""
        captured = {}

        async def fake_run(cfg, ev, mgr, task, *, conv_id=None):
            captured["task_name"] = task.name
            captured["conv_id"] = conv_id
            return {"task_name": task.name, "is_ok": True, "channel": "",
                    "response": "", "context_id": None}

        monkeypatch.setattr("decafclaw.http_server.run_schedule_task", fake_run)

        r = await client.post("/api/schedules/dream/run")
        assert r.status_code == 202
        data = r.json()
        assert data["task_name"] == "dream"
        assert data["conv_id"].startswith("schedule-dream-")
        assert "started_at" in data

        # Give the event loop a tick to run the create_task'd coroutine
        await asyncio.sleep(0)

        assert captured.get("task_name") == "dream"
        assert captured.get("conv_id") == data["conv_id"]

        # last_run written so the timer doesn't double-fire
        last_run = http_config.workspace_path / ".schedule_last_run" / "dream"
        assert last_run.exists()

    @pytest.mark.asyncio
    async def test_run_endpoint_404_unknown(self, client):
        r = await client.post("/api/schedules/nonexistent/run")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_run_endpoint_works_when_disabled(self, client, http_config, monkeypatch):
        """Manual run bypasses the enabled flag."""
        ws_dir = http_config.workspace_path / "schedules"
        ws_dir.mkdir(parents=True, exist_ok=True)
        (ws_dir / "disabled-task.md").write_text(
            "---\nschedule: '0 * * * *'\nenabled: false\n---\nDisabled body.\n"
        )

        fired = {"count": 0}

        async def fake_run(cfg, ev, mgr, task, *, conv_id=None):
            fired["count"] += 1
            return {"task_name": task.name, "is_ok": True, "channel": "",
                    "response": "", "context_id": None}

        monkeypatch.setattr("decafclaw.http_server.run_schedule_task", fake_run)

        r = await client.post("/api/schedules/disabled-task/run")
        assert r.status_code == 202

        await asyncio.sleep(0)
        assert fired["count"] == 1
