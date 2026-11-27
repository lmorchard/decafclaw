"""Import smoke tests — verify all modules load without errors."""

import importlib

import pytest


@pytest.mark.parametrize(
    ("module", "symbol"),
    [
        ("decafclaw.events", "EventBus"),
        ("decafclaw.context", "Context"),
        ("decafclaw.agent", "run_agent_turn"),
        ("decafclaw.tools", "execute_tool"),
        ("decafclaw.llm", "call_llm"),
        ("decafclaw.llm", "call_llm_streaming"),
        ("decafclaw.skills.vault.tools", "TOOLS"),
        ("decafclaw.archive", "append_message"),
        ("decafclaw.compaction", "compact_history"),
        ("decafclaw.compaction.decisions", "format_slice"),
        ("decafclaw.compaction.cleanup", "clear_old_tool_results"),
        ("decafclaw.mattermost", "ConversationDisplay"),
    ],
)
def test_module_imports(module, symbol):
    imported = importlib.import_module(module)
    assert getattr(imported, symbol) is not None


def test_tool_registries_are_not_empty():
    from decafclaw.skills.vault.tools import TOOL_DEFINITIONS as VAULT_DEFINITIONS
    from decafclaw.skills.vault.tools import TOOLS as VAULT_TOOLS
    from decafclaw.tools import TOOL_DEFINITIONS

    assert TOOL_DEFINITIONS
    assert VAULT_TOOLS
    assert VAULT_DEFINITIONS


def test_import_mattermost_client_methods():
    """Verify MattermostClient has all expected methods (catches class boundary issues)."""
    from decafclaw.mattermost import MattermostClient

    assert hasattr(MattermostClient, "run")
    assert hasattr(MattermostClient, "send")
    assert hasattr(MattermostClient, "close")
    assert hasattr(MattermostClient, "_make_heartbeat_cycle")
    assert hasattr(MattermostClient, "_resolve_heartbeat_channel")
    assert hasattr(MattermostClient, "_poll_confirmation_manager")
