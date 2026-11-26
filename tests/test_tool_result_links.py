"""ToolResult.links: in-session file/page links for tool rows (#1829)."""

from decafclaw.media import ToolResult
from decafclaw.tools import _attach_links


def test_workspace_tool_links_the_path_argument():
    result = ToolResult(text="Wrote 3 characters to notes.md")
    _attach_links(result, "workspace_write", {"path": "notes.md", "content": "abc"})
    assert result.links == [{"kind": "workspace", "path": "notes.md", "label": "notes.md"}]


def test_vault_tool_links_the_page_argument():
    result = ToolResult(text="Wrote page")
    _attach_links(result, "vault_write", {"page": "agent/pages/Notes", "content": "x"})
    assert result.links == [{"kind": "vault", "page": "agent/pages/Notes", "label": "agent/pages/Notes"}]


def test_error_and_unrelated_tools_have_no_links():
    result = ToolResult(text="[error: nope]")
    _attach_links(result, "workspace_write", {"path": "x", "content": "y"})
    assert result.links == []

    result = ToolResult(text="a.txt")
    _attach_links(result, "workspace_list", {"path": "a.txt"})
    assert result.links == []


async def test_execute_tool_attaches_the_link(ctx):
    from decafclaw.tools import execute_tool

    result = await execute_tool(ctx, "workspace_write", {"path": "linked.txt", "content": "hello"})
    assert result.links == [{"kind": "workspace", "path": "linked.txt", "label": "linked.txt"}]
