"""Tests for the markdown-tools contrib skill."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

_THIS_DIR = Path(__file__).parent
_TOOLS_SPEC = importlib.util.spec_from_file_location("decafclaw_contrib_markdown_tools", _THIS_DIR / "tools.py")
assert _TOOLS_SPEC is not None and _TOOLS_SPEC.loader is not None
markdown_tools = importlib.util.module_from_spec(_TOOLS_SPEC)
sys.modules["decafclaw_contrib_markdown_tools"] = markdown_tools
_TOOLS_SPEC.loader.exec_module(markdown_tools)


def make_ctx(workspace: Path):
    return SimpleNamespace(config=SimpleNamespace(workspace_path=workspace))


def write(workspace: Path, rel_path: str, content: str) -> Path:
    target = workspace / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, newline="")
    return target


def test_tool_registration_and_skill_frontmatter():
    tool_names = {definition["function"]["name"] for definition in markdown_tools.TOOL_DEFINITIONS}
    assert tool_names == set(markdown_tools.TOOLS)

    text = (_THIS_DIR / "SKILL.md").read_text()
    _, frontmatter, _ = text.split("---", 2)
    meta = yaml.safe_load(frontmatter)
    assert meta["name"] == "markdown-tools"
    assert meta["user-invocable"] is True
    assert meta["context"] == "inline"


def test_markdown_get_section_returns_nested_subtree(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write(
        workspace,
        "notes/plan.md",
        "# Roadmap\n\n## Plan\nAlpha\n\n### Detail\nNested\n\n## Next\nBeta\n",
    )

    result = markdown_tools.markdown_get_section(make_ctx(workspace), "notes/plan.md", "## Plan")

    assert result.text == "## Plan\nAlpha\n\n### Detail\nNested\n\n"
    assert result.data["heading"] == "## Plan"
    assert result.data["start_line"] == 3
    assert result.data["end_line"] == 8


def test_markdown_replace_section_preserves_rest_and_trailing_newline_state(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = write(
        workspace,
        "notes/plan.md",
        "# Roadmap\n\n## Plan\nAlpha\n\n### Detail\nNested\n\n## Next\nBeta",
    )

    result = markdown_tools.markdown_replace_section(
        make_ctx(workspace), "notes/plan.md", "Plan", "Updated\n\n- [ ] Ship it"
    )

    assert result.text == "Replaced section body for ## Plan in notes/plan.md"
    assert path.read_text(newline="") == "# Roadmap\n\n## Plan\nUpdated\n\n- [ ] Ship it\n## Next\nBeta"
    assert not path.read_text(newline="").endswith("\n")


def test_markdown_table_read_returns_rows_with_unescaped_pipes(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write(
        workspace,
        "notes/table.md",
        "## Deliverables\n\n| Task | Status |\n| --- | --- |\n| Alpha | Todo |\n| Beta\\|Gamma | Done |\n",
    )

    result = markdown_tools.markdown_table_read(make_ctx(workspace), "notes/table.md", "## Deliverables")

    assert result.data["columns"] == ["Task", "Status"]
    assert result.data["rows"] == [
        {"Task": "Alpha", "Status": "Todo"},
        {"Task": "Beta|Gamma", "Status": "Done"},
    ]


def test_markdown_table_append_rerenders_alignment_and_escapes_pipes(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = write(
        workspace,
        "notes/table.md",
        "## Deliverables\n\n| Task | Status |\n| --- | --- |\n| Alpha | Todo |\n",
    )

    result = markdown_tools.markdown_table_append(
        make_ctx(workspace),
        "notes/table.md",
        "Deliverables",
        [{"Task": "Beta|Gamma", "Status": "In Progress"}],
    )

    assert result.data["appended"] == 1
    assert path.read_text(newline="") == (
        "## Deliverables\n\n"
        "| Task        | Status      |\n"
        "| ----------- | ----------- |\n"
        "| Alpha       | Todo        |\n"
        "| Beta\\|Gamma | In Progress |\n"
    )


def test_markdown_table_update_changes_matching_cells_and_keeps_alignment(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = write(
        workspace,
        "notes/table.md",
        "## Deliverables\n\n| Task        | Status      |\n| ----------- | ----------- |\n| Alpha       | Todo        |\n| Beta\\|Gamma | In Progress |\n",
    )

    result = markdown_tools.markdown_table_update(
        make_ctx(workspace),
        "notes/table.md",
        "## Deliverables",
        {"Task": "Beta|Gamma"},
        {"Status": "Blocked|Waiting"},
    )

    assert result.data["updated"] == 1
    assert path.read_text(newline="") == (
        "## Deliverables\n\n"
        "| Task        | Status           |\n"
        "| ----------- | ---------------- |\n"
        "| Alpha       | Todo             |\n"
        "| Beta\\|Gamma | Blocked\\|Waiting |\n"
    )


def test_markdown_checklist_get_filters_section_and_status(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write(
        workspace,
        "notes/tasks.md",
        "# Day\n\n## Tasks\n- [ ] open task\n- [x] done task\n- [>] in progress\n  - [ ] nested task\n\n## Later\n- [ ] later task\n",
    )

    result = markdown_tools.markdown_checklist_get(
        make_ctx(workspace), "notes/tasks.md", section="## Tasks", status="open"
    )

    assert result.data["count"] == 3
    assert [item["text"] for item in result.data["items"]] == ["open task", "in progress", "nested task"]
    assert [item["marker"] for item in result.data["items"]] == [" ", ">", " "]


def test_markdown_checklist_toggle_updates_only_matching_item(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = write(
        workspace,
        "notes/tasks.md",
        "- [ ] one\n- [ ] two\n  - [ ] nested target",
    )

    result = markdown_tools.markdown_checklist_toggle(
        make_ctx(workspace), "notes/tasks.md", "nested target", checked=True
    )

    assert result.data["changed"] is True
    assert path.read_text(newline="") == "- [ ] one\n- [ ] two\n  - [x] nested target"
    assert not path.read_text(newline="").endswith("\n")


def test_markdown_tools_reject_paths_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = markdown_tools.markdown_get_section(make_ctx(workspace), "../outside.md", "Plan")

    assert result.text == "[error: path '../outside.md' is outside the workspace]"
