"""Import smoke tests — verify all modules load without errors."""


def test_import_events():
    from decafclaw.events import EventBus
    assert EventBus is not None


def test_import_context():
    from decafclaw.context import Context
    assert Context is not None


def test_import_agent():
    from decafclaw.agent import run_agent_turn
    from decafclaw.interactive_terminal import run_interactive
    assert run_agent_turn is not None
    assert run_interactive is not None


def test_import_tools():
    from decafclaw.tools import TOOL_DEFINITIONS, execute_tool
    assert len(TOOL_DEFINITIONS) > 0
    assert execute_tool is not None


def test_import_llm():
    from decafclaw.llm import call_llm
    assert call_llm is not None


def test_import_vault_tools():
    from decafclaw.skills.vault.tools import TOOL_DEFINITIONS, TOOLS
    assert len(TOOLS) > 0
    assert len(TOOL_DEFINITIONS) > 0


def test_import_archive():
    from decafclaw.archive import append_message, read_archive
    assert append_message is not None


def test_import_compaction():
    from decafclaw.compaction import compact_history
    assert compact_history is not None


def test_import_mattermost_client_methods():
    """Verify MattermostClient has all expected methods (catches class boundary issues)."""
    from decafclaw.mattermost import MattermostClient
    assert hasattr(MattermostClient, "run")
    assert hasattr(MattermostClient, "send")
    assert hasattr(MattermostClient, "close")
    assert hasattr(MattermostClient, "_make_heartbeat_cycle")
    assert hasattr(MattermostClient, "_resolve_heartbeat_channel")
    assert hasattr(MattermostClient, "_poll_confirmation_manager")


def test_import_streaming():
    from decafclaw.llm import call_llm_streaming
    from decafclaw.mattermost import ConversationDisplay
    assert call_llm_streaming is not None
    assert ConversationDisplay is not None
