# Secret-file protection for the admin tools (issue #1013).
# Covers the shared rule in decafclaw.secret_policy and its enforcement in
# decafclaw.tools.admin_tools: all six refuse the secret stores, admin_read
# redacts the two config files, the marker guard blocks payloads, and the
# mutation recovery path inherits both guards. Includes the fail-loud
# authoring guard that every secret-annotated Config field appears in the
# redaction schema.

import asyncio
import dataclasses
import json
from dataclasses import fields
from typing import get_args, get_origin, get_type_hints
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.config import Config
from decafclaw.config_types import AgentConfig
from decafclaw.confirmations import ConfirmationAction, ConfirmationRequest, ConfirmationResponse
from decafclaw.context import Context
from decafclaw.media import ToolResult
from decafclaw.secret_policy import (
    DEFAULT_REFUSE_PATTERNS,
    REDACTED,
    STRUCTURAL_REDACT_PATTERNS,
    annotation_secret_paths,
    is_redactable,
    is_refused,
    marker_in,
    redact_json,
    redact_text,
    redaction_patterns,
)
from decafclaw.tools.admin_tools import (
    ADMIN_TOOLS,
    AdminMutationHandler,
    tool_admin_delete,
    tool_admin_edit,
    tool_admin_list,
    tool_admin_read,
    tool_admin_replace_lines,
    tool_admin_write,
)


def _text(result) -> str:
    return result.text if isinstance(result, ToolResult) else str(result)


def _mock_confirm(approved: bool = True):
    return AsyncMock(return_value=ConfirmationResponse(confirmation_id="c1", approved=approved))


@pytest.fixture(autouse=True)
def ensure_agent_path(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    return config


def _ensure(config, rel: str):
    p = config.agent_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _config_with(**overlays):
    """A standalone Config with secret_policy fields set (no event bus needed)."""
    from decafclaw.config_types import SecretPolicyConfig

    sp = SecretPolicyConfig(**overlays)
    return Config(agent=AgentConfig(data_home="/tmp/secret-policy-test", id="x"), secret_policy=sp)


ALL_TOOL_NAMES = [
    "admin_read",
    "admin_list",
    "admin_write",
    "admin_replace_lines",
    "admin_edit",
    "admin_delete",
]

# Secret files/directories the default rule must refuse. The files/dirs are
# created in each test so a refusal is not a "file not found".
SECRET_PATHS = [
    "web_tokens.json",
    "browser_tokens.json",
    "mcp_oauth/linear.json",
    "gce_runner/keys/vm-1",
    "service_account.json",
]


async def _call(ctx, name, path, **kw):
    t = ADMIN_TOOLS[name]
    if name == "admin_read":
        r = t(ctx, path)
    elif name == "admin_list":
        r = t(ctx, path)
    elif name == "admin_write":
        r = await t(ctx, path, "replacement-content", **kw)
    elif name == "admin_replace_lines":
        r = await t(ctx, path, 1, 1, "replacement-content", **kw)
    elif name == "admin_edit":
        r = await t(ctx, path, "old", "replacement-content", **kw)
    elif name == "admin_delete":
        r = await t(ctx, path, recursive=True, **kw)
    else:
        raise AssertionError(name)
    return r


# ---------------------------------------------------------------------------
# 1. Refusal, parametrized (tool x path) — the files EXIST so this is not
#    "file not found".
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("path", SECRET_PATHS)
@pytest.mark.parametrize(
    "name",
    ALL_TOOL_NAMES,
    ids=lambda n: n,
)
async def test_all_tools_refuse_secret_paths(ctx, path, name):
    target = _ensure(ctx.config, path)
    target.write_text("a-real-secret-value")
    before = target.read_text()
    confirm = _mock_confirm(approved=True)
    ctx.request_confirmation = confirm

    r = await _call(ctx, name, path)
    text = _text(r)

    assert "protected secret" in text
    assert "a-real-secret-value" not in text
    # Refusal happens before confirmation is even requested.
    assert confirm.call_count == 0
    # Nothing was read or changed.
    assert target.read_text() == before


def _unused_anchor():
    pass


# ---------------------------------------------------------------------------
# admin_list: shows a refused file's name at its parent, refuses to descend
# into a refused directory.
# ---------------------------------------------------------------------------
def test_admin_list_root_shows_secret_name(ctx):
    """admin_list('.') may show web_tokens.json's name; that is a name, not a content."""
    _ensure(ctx.config, "web_tokens.json").write_text("secret")
    r = tool_admin_list(ctx, ".")
    text = _text(r)
    assert "web_tokens.json" in text


def test_admin_list_refuses_to_descend_into_secret_dir(ctx):
    d = ctx.config.agent_path / "mcp_oauth"
    d.mkdir(parents=True, exist_ok=True)
    (d / "linear.json").write_text("secret")
    r = tool_admin_list(ctx, "mcp_oauth")
    assert "protected secret" in _text(r)


def test_admin_list_refuses_keys_dir(ctx):
    d = ctx.config.agent_path / "gce_runner" / "keys"
    d.mkdir(parents=True, exist_ok=True)
    (d / "vm-1").write_text("ssh key")
    r = tool_admin_list(ctx, "gce_runner/keys")
    assert "protected secret" in _text(r)


def test_admin_list_normal_dir_unchanged(ctx):
    """Listing a normal admin directory still works and shows its (immediate) entries."""
    d = ctx.config.agent_path / "skills" / "demo"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text("name: demo")
    (ctx.config.agent_path / "skills").mkdir(parents=True, exist_ok=True)
    r = tool_admin_list(ctx, ".")
    text = _text(r)
    assert "skills/" in text
    r2 = tool_admin_list(ctx, "skills")
    assert "demo/" in _text(r2)


# ---------------------------------------------------------------------------
# 2. Redaction of config.json / mcp_servers.json — annotation-driven.
# ---------------------------------------------------------------------------
def test_admin_read_redacts_config_json(ctx):
    cfg_file = ctx.config.agent_path / "config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "llm": {"url": "http://x", "model": "m", "api_key": "SECRET_LLM_KEY"},
                "mattermost": {"url": "https://mm", "token": "SECRET_MM_TOKEN", "bot_username": "bot"},
                "providers": {
                    "gcp": {
                        "type": "vertex",
                        "api_key": "SECRET_PROVIDER_KEY",
                        "project": "proj",
                        "region": "us-central1",
                    }
                },
                "skills": {"custom": {"some_api_key": "SECRET_SKILL_KEY"}},
                "env": {"MY_TOKEN": "SECRET_ENV"},
            }
        )
    )
    r = tool_admin_read(ctx, "config.json")
    text = _text(r)
    # Real values must NOT appear in the text...
    for secret in ("SECRET_LLM_KEY", "SECRET_MM_TOKEN", "SECRET_PROVIDER_KEY", "SECRET_SKILL_KEY", "SECRET_ENV"):
        assert secret not in text, f"leaked {secret}"
    # ...and are replaced by the marker.
    assert REDACTED in text
    # Non-secret values remain.
    assert "us-central1" in text
    assert "vertex" in text

    # Envelope: redacted=True, redacted_fields lists dotted concrete paths.
    assert r.data["redacted"] is True
    expected = {"llm.api_key", "mattermost.token", "providers.gcp.api_key"}
    assert expected.issubset(set(r.data["redacted_fields"]))

    # Envelope reflects the REDACTED content (size/lines of the redacted body),
    # not the on-disk original — otherwise the agent could infer secret length
    # from the byte count.
    redacted_body, _ = redact_text(cfg_file.read_text(), ctx.config)
    assert r.data["size"] == len(redacted_body.encode("utf-8"))
    assert r.data["lines"] == len(redacted_body.splitlines())


def test_admin_read_redacts_mcp_servers_json(ctx):
    mcp_file = ctx.config.agent_path / "mcp_servers.json"
    mcp_file.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "linear": {
                        "transport": "http",
                        "url": "https://mcp.linear.app",
                        "headers": {"Authorization": "Bearer SECRET_MCP_HDR"},
                        "env": {"LINEAR_API_KEY": "SECRET_MCP_ENV"},
                    }
                }
            }
        )
    )
    r = tool_admin_read(ctx, "mcp_servers.json")
    text = _text(r)
    assert "SECRET_MCP_HDR" not in text
    assert "SECRET_MCP_ENV" not in text
    assert REDACTED in text
    assert "mcp.linear.app" in text
    assert r.data["redacted"] is True
    assert "mcpServers.linear.headers.Authorization" in r.data["redacted_fields"]
    assert "mcpServers.linear.env.LINEAR_API_KEY" in r.data["redacted_fields"]


def test_admin_read_no_redaction_envelope_for_other_files(ctx):
    """Non-redactable admin files return the unchanged envelope (no 'redacted' key)."""
    target = _ensure(ctx.config, "skills/demo/SKILL.md")
    target.write_text("name: demo\ndescription: demo\n")
    r = tool_admin_read(ctx, "skills/demo/SKILL.md")
    assert "redacted" not in r.data
    assert r.data["lines"] == 2


def test_admin_read_config_json_invalid_json_refuses(ctx):
    """Fail-closed: a redactable file whose JSON doesn't parse is refused, not leaked."""
    p = ctx.config.agent_path / "config.json"
    p.write_text("this is not json: {")
    r = tool_admin_read(ctx, "config.json")
    text = _text(r)
    assert "protected secret" in text or "could not be parsed" in text
    # The raw bytes must not be exposed.
    assert "this is not json" not in text


def test_admin_read_normal_file_unchanged(ctx):
    """A normal markdown admin file returns exactly as-is (no redaction, no marker)."""
    target = _ensure(ctx.config, "SOUL.md")
    target.write_text("I am decafclaw.\n")
    r = tool_admin_read(ctx, "SOUL.md")
    assert "I am decafclaw." in _text(r)
    assert REDACTED not in _text(r)


# ---------------------------------------------------------------------------
# 3. Extension — admin-declared refuse_paths: all six tools refuse the file.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("name", ALL_TOOL_NAMES)
async def test_extension_refuse_paths_all_tools(ctx, name):
    from dataclasses import replace

    from decafclaw.config_types import SecretPolicyConfig

    ctx.config = replace(ctx.config, secret_policy=SecretPolicyConfig(refuse_paths=["custom_secrets.json"]))
    target = _ensure(ctx.config, "custom_secrets.json")
    target.write_text("admin-added-secret")
    before = target.read_text()
    ctx.request_confirmation = _mock_confirm(approved=True)

    r = await _call(ctx, name, "custom_secrets.json")
    assert "protected secret" in _text(r)
    assert "admin-added-secret" not in _text(r)
    assert target.read_text() == before


def test_extension_refuse_default_glob_new_secret(ctx):
    """A new *.pem / *.key secret is refused without touching code (default globs)."""
    target = _ensure(ctx.config, "certs/api_certificate.pem")
    target.write_text("PEM_SECRET")
    r = tool_admin_read(ctx, "certs/api_certificate.pem")
    assert "protected secret" in _text(r)
    assert "PEM_SECRET" not in _text(r)


# ---------------------------------------------------------------------------
# 4. Extension — admin-declared redact_paths: redact a path the code lacks.
# ---------------------------------------------------------------------------
def test_extension_redact_paths_unannotated_field(ctx):
    from dataclasses import replace

    from decafclaw.config_types import SecretPolicyConfig

    (ctx.config.agent_path / "config.json").write_text(
        json.dumps({"some_group": {"legacy_key": "ADMIN_KNOWN_SECRET", "plain": "ok"}})
    )
    ctx.config = replace(ctx.config, secret_policy=SecretPolicyConfig(redact_paths=["some_group.legacy_key"]))

    r = tool_admin_read(ctx, "config.json")
    text = _text(r)
    assert "ADMIN_KNOWN_SECRET" not in text
    assert REDACTED in text
    assert "some_group.legacy_key" in r.data["redacted_fields"]
    # The non-secret sibling is untouched.
    assert '"plain": "ok"' in text


# ---------------------------------------------------------------------------
# 5. Marker guard: mutations whose payload holds the marker are refused
#    (prevents read->modify->write from clobbering a real secret with the
#    placeholder) — on the interactive tools AND the recovery path.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_admin_write_refuses_marker(ctx):
    ctx.request_confirmation = _mock_confirm(approved=True)
    r = await tool_admin_write(ctx, "notes.md", f'key = "{REDACTED}"\n')
    assert "redaction marker" in _text(r)
    assert ctx.request_confirmation.call_count == 0
    assert not (ctx.config.agent_path / "notes.md").exists()


@pytest.mark.asyncio
async def test_admin_edit_refuses_marker(ctx):
    (ctx.config.agent_path / "notes.md").write_text("key = realvalue\n")
    ctx.request_confirmation = _mock_confirm(approved=True)
    r = await tool_admin_edit(ctx, "notes.md", "realvalue", REDACTED)
    assert "redaction marker" in _text(r)
    assert (ctx.config.agent_path / "notes.md").read_text() == "key = realvalue\n"


@pytest.mark.asyncio
async def test_admin_replace_lines_refuses_marker(ctx):
    (ctx.config.agent_path / "notes.md").write_text("a\nb\nc\n")
    ctx.request_confirmation = _mock_confirm(approved=True)
    r = await tool_admin_replace_lines(ctx, "notes.md", 2, 2, REDACTED)
    assert "redaction marker" in _text(r)
    assert (ctx.config.agent_path / "notes.md").read_text() == "a\nb\nc\n"


def _snapshot(config, path: str, content: str) -> dict:
    return {
        "resolved": str((config.agent_path / path).resolve()),
        "exists": True,
        "is_dir": False,
        "content": content,
    }


@pytest.mark.asyncio
async def test_recovery_path_refuses_marker(config):
    """AdminMutationHandler.on_approve inherits the marker guard (issue #1013)."""
    handler = AdminMutationHandler()
    target = config.agent_path / "recovered_marker.txt"
    target.write_text("before-marker-write")

    mock_ctx = MagicMock()
    mock_ctx.config = config
    mock_ctx.conv_id = "rec-conv"

    req = ConfirmationRequest(
        action_type=ConfirmationAction.ADMIN_MUTATION,
        action_data={
            "tool_name": "admin_write",
            "path": "recovered_marker.txt",
            "payload": {"content": f"value = {REDACTED}"},
            "snapshot": _snapshot(config, "recovered_marker.txt", "before-marker-write"),
        },
    )
    resp = ConfirmationResponse(confirmation_id=req.confirmation_id, approved=True)

    result = await handler.on_approve(mock_ctx, req, resp)
    assert "redaction marker" in result.get("error", "")
    assert target.read_text() == "before-marker-write"


@pytest.mark.asyncio
async def test_recovery_path_refuses_secret_path(config):
    """The recovery path must ALSO refuse a protected secret store (#1013 bypass)."""
    handler = AdminMutationHandler()
    (config.agent_path / "web_tokens.json").write_text("session-tokens-{}")
    mock_ctx = MagicMock()
    mock_ctx.config = config
    mock_ctx.conv_id = "rec-conv2"
    req = ConfirmationRequest(
        action_type=ConfirmationAction.ADMIN_MUTATION,
        action_data={
            "tool_name": "admin_delete",
            "path": "web_tokens.json",
            "payload": {"recursive": False},
        },
    )
    resp = ConfirmationResponse(confirmation_id=req.confirmation_id, approved=True)
    result = await handler.on_approve(mock_ctx, req, resp)
    assert "protected secret" in result.get("error", "")
    assert (config.agent_path / "web_tokens.json").exists()


# ---------------------------------------------------------------------------
# Shared-policy unit behaviors (the one rule) — refusal granularity + redaction.
# ---------------------------------------------------------------------------
def _cfg():
    return Config(agent=AgentConfig(data_home="/tmp/sp-unit", id="u"))


@pytest.mark.parametrize(
    "rel",
    [
        "web_tokens.json",
        "browser_tokens.json",
        "mcp_oauth/linear.json",
        "gce_runner/keys/vm-1",
        "service_account.json",
        "sub/dir/other.key",
        "a/b/c/keyfile.pem",
        ".//mcp_oauth//deep/x.json",  # dot/./dup slashes normalize
    ],
)
def test_is_refused_defaults(rel):
    assert is_refused(rel, _cfg()), f"expected refused: {rel}"


@pytest.mark.parametrize(
    "rel",
    ["SOUL.md", "AGENTS.md", "skills/demo/SKILL.md", "config.json", "mcp_servers.json", "schedules/task.md"],
)
def test_is_refused_allows_normal_admin(rel):
    assert not is_refused(rel, _cfg()), f"should NOT be refused: {rel}"


def test_refuse_admin_extension_glob():
    from decafclaw.config_types import SecretPolicyConfig

    cfg = Config(
        agent=AgentConfig(data_home="/tmp/sp-unit", id="u"),
        secret_policy=SecretPolicyConfig(refuse_paths=["vault_backup/keys/**", "legacy_key.json"]),
    )
    assert is_refused("vault_backup/keys", cfg)
    assert is_refused("legacy_key.json", cfg)
    assert not is_refused("SOUL.md", cfg)


def test_redact_structural_env_and_skills():
    cfg = _cfg()
    data = {"env": {"A": "1"}, "skills": {"s": {"c": "2"}}, "llm": {"url": "u", "api_key": "k"}}
    redacted, paths = redact_json(data, tuple(redaction_patterns(cfg)))
    assert redacted["env"]["A"] == REDACTED
    assert redacted["skills"]["s"]["c"] == REDACTED
    assert redacted["llm"]["api_key"] == REDACTED
    assert redacted["llm"]["url"] == "u"
    assert "env.A" in paths and "skills.s.c" in paths and "llm.api_key" in paths


# ---------------------------------------------------------------------------
# 6. Annotation coverage — fail-loud authoring guards (not hand-lists).
#    6a: every secret-annotated field on the reachable Config tree appears in
#        the redaction schema.  6b: any credential-evoking str field is
#        annotated secret or explicitly whitelisted as a known non-secret path
#        ref (e.g. ProviderConfig.service_account_file).
# ---------------------------------------------------------------------------


def _reachable_dataclasses(config_cls, leaf=None) -> tuple[list[type], set[type]]:
    """Independently enumerate the dataclasses reachable from Config via
    nested dataclass fields and dict[str, Dataclass] fields, and the
    secret-annotated leaf dataclasses (e.g. ProviderConfig). This is the
    *independent* check in the issue — it does not call secret_policy's own
    walker; it re-derives the set to catch a regression in the impl walker.
    """
    from decafclaw.config import Config as ConfigCls

    root = config_cls or ConfigCls
    seen: set[type] = set()
    stack: list[tuple[type, str]] = [(root, "")]
    leaves: set[type] = set()  # dataclasses holding secret fields

    while stack:
        cls, _prefix = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        hints = get_type_hints(cls)
        for f in dataclasses.fields(cls):
            t = hints.get(f.name, f.type)
            if f.metadata.get("secret"):
                leaves.add(cls)
                continue
            origin = get_origin(t)
            if origin is dict:
                args = [a for a in get_args(t) if a is not type(None)]
                if len(args) == 2 and isinstance(args[1], type) and dataclasses.is_dataclass(args[1]):
                    leaves.add(args[1])
                    stack.append((args[1], f.name))
            elif isinstance(t, type) and dataclasses.is_dataclass(t):
                stack.append((t, f.name))
    return seen, leaves


def test_6a_every_secret_field_in_redaction_schema():
    from decafclaw.config import Config

    config = _cfg()
    _seen, leaves = _reachable_dataclasses(Config)
    schema = annotation_secret_paths(config)

    # For every secret-annotated field on a reachable dataclass, there MUST be
    # a schema path ending in that field name (possibly under a `.*` for
    # dict-of-dataclass leaves like ProviderConfig).
    for cls in leaves:
        for f in dataclasses.fields(cls):
            if not f.metadata.get("secret"):
                continue
            candidates = [p for p in schema if p.rstrip().endswith("." + f.name) or p == f.name]
            assert candidates, (
                f"secret-annotated field {cls.__name__}.{f.name} has no entry in "
                f"annotation_secret_paths({sorted(schema)}) — add the walker rule or "
                f"annotate a known non-secret path ref."
            )


# Credential-evoking field names. Fields whose sole role is a *path reference*
# to a secret file (not the secret value) are intentionally non-secret; they
# must be listed here, not silently left unannotated (the failure mode this
# guard exists to catch — issue #1013, 6b).
KNOWN_NON_SECRET_PATH_REFS = frozenset(
    {
        "ProviderConfig.service_account_file",  # vertex: a *path* to the SA key file (the file itself is refused)
    }
)


def test_6b_credential_named_str_fields_are_secret_or_whitelisted():
    """Scan config_types.py for str fields whose name evokes a credential; fail
    unless the field is secret-annotated or explicitly whitelisted above."""
    import decafclaw.config_types as ct

    credential_tokens = ("token", "key", "secret", "password", "credential", "auth")
    unhandled: list[str] = []
    for name in sorted(dir(ct)):
        obj = getattr(ct, name)
        if not (isinstance(obj, type) and dataclasses.is_dataclass(obj) and name not in ("SecretPolicyConfig",)):
            continue
        for f in dataclasses.fields(obj):
            if f.type != "str":
                continue
            nm = f.name.lower()
            if not any(tok in nm for tok in credential_tokens):
                continue
            is_secret = bool(f.metadata.get("secret"))
            whitelisted = f"{obj.__name__}.{f.name}" in KNOWN_NON_SECRET_PATH_REFS
            if not is_secret and not whitelisted:
                unhandled.append(f"{obj.__name__}.{f.name}")
    assert not unhandled, (
        "These credential-named str fields are neither secret-annotated nor in "
        f"KNOWN_NON_SECRET_PATH_REFS: {unhandled}. Annotate `secret: True` or add "
        "a whitelisted path-ref entry with a comment."
    )
