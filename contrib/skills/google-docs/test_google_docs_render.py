"""Pure-logic tests for the google-docs body renderer.

No network or credentials: everything here exercises `_docs_to_text` and the
table helpers against hand-built Docs API structures. That is deliberate — the
rendering bugs these cover (dropped tables, flattened bullets) are invisible in
API responses and only show up in the rendered text, which is exactly what a
real-doc test would need OAuth to reach.
"""

import importlib.util
from pathlib import Path

import pytest

# tools.py imports google.oauth2 / googleapiclient at module scope. Those are
# not installed in a bare test environment, so skip rather than error out and
# make `make check` depend on a Google client library.
pytest.importorskip("googleapiclient")
pytest.importorskip("google.oauth2.credentials")

_TOOLS_PATH = Path(__file__).parent / "tools.py"
_spec = importlib.util.spec_from_file_location("google_docs_tools", _TOOLS_PATH)
assert _spec is not None and _spec.loader is not None, f"cannot load {_TOOLS_PATH}"
tools = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tools)


def para(text, bullet_level=None, style="NORMAL_TEXT", bold=False):
    """Build a paragraph element shaped like the Docs API returns."""
    paragraph = {
        "elements": [{"textRun": {"content": text, "textStyle": {"bold": bold}}}],
        "paragraphStyle": {"namedStyleType": style},
    }
    if bullet_level is not None:
        paragraph["bullet"] = {"nestingLevel": bullet_level}
    return {"paragraph": paragraph}


def cell(*paragraphs):
    """A table cell's content is a list; nested lists arrive as list elements."""
    return {"content": list(paragraphs)}


def table(*rows):
    return {"table": {"tableRows": [{"tableCells": list(row)} for row in rows]}}


def render(*elements):
    return tools._docs_to_text({"content": list(elements)}, markdown=True)


class TestTableRendering:
    def test_table_content_is_not_dropped(self):
        """A table's text must survive rendering.

        Regression: tables used to render as "[Table omitted]", silently
        discarding content. A doc whose whole checklist lived in a table read
        back as an empty document with no error.
        """
        out = render(
            table(
                (cell(para("Name\n")), cell(para("Role\n"))),
                (cell(para("Ada\n")), cell(para("Engineer\n"))),
            )
        )
        assert "Ada" in out
        assert "Engineer" in out
        assert "Table omitted" not in out

    def test_table_renders_as_markdown(self):
        out = render(
            table(
                (cell(para("A\n")), cell(para("B\n"))),
                (cell(para("1\n")), cell(para("2\n"))),
            )
        )
        assert "| A | B |" in out
        assert "| --- | --- |" in out
        assert "| 1 | 2 |" in out

    def test_blank_rows_dropped(self):
        """Docs leaves a trailing empty paragraph per cell; blank rows go away."""
        out = render(
            table(
                (cell(para("A\n")), cell(para("B\n"))),
                (cell(para("\n")), cell(para("\n"))),
            )
        )
        assert out.count("| --- |") == 1
        assert "|  |  |" not in out

    def test_empty_table_is_marked(self):
        out = render(table((cell(para("\n")), cell(para("\n")))))
        assert "[Table present but empty]" in out

    def test_pipe_in_cell_escaped(self):
        """A raw pipe would split the row into a phantom column."""
        out = render(table((cell(para("a | b\n")), cell(para("c\n")))))
        assert r"a \| b" in out

    def test_multiparagraph_cell_joined(self):
        out = render(table((cell(para("one\n"), para("two\n")), cell(para("x\n")))))
        assert "one <br> two" in out

    def test_nested_list_inside_cell(self):
        """Nested bullets arrive as a list element inside the cell content."""
        out = render(
            table(
                (cell(para("Outer\n"), [para("Inner A\n"), para("Inner B\n")]), cell(para("x\n"))),
            )
        )
        assert "Inner A" in out and "Inner B" in out

    def test_ragged_rows_padded(self):
        out = render(
            table(
                (cell(para("A\n")), cell(para("B\n"))),
                (cell(para("only\n")),),
            )
        )
        data_rows = [ln for ln in out.splitlines() if ln.startswith("|") and "---" not in ln]
        widths = {ln.count("|") for ln in data_rows}
        assert len(widths) == 1, f"ragged table: {data_rows}"


class TestNestedBullets:
    def test_nesting_level_indents(self):
        out = render(
            para("Slack\n", bullet_level=0),
            para("#firefox\n", bullet_level=1),
            para("#build\n", bullet_level=2),
        )
        lines = out.splitlines()
        assert lines[0] == "- Slack"
        assert lines[1] == "  - #firefox"
        assert lines[2] == "    - #build"

    @pytest.mark.parametrize("level,expected_indent", [("2", 4), ("x", 0), (-3, 0), (0, 0)])
    def test_bad_nesting_level_falls_back(self, level, expected_indent):
        out = render(para("item\n", bullet_level=level))
        assert out == f"{' ' * expected_indent}- item\n"

    def test_empty_bullet_dict_still_bullets(self):
        """Regression: `if bullet` treated {} as falsy and dropped the marker,
        welding the line onto the next bullet."""
        element = {"paragraph": {"elements": [{"textRun": {"content": "x\n"}}], "bullet": {}}}
        out = render(element, para("y\n", bullet_level=0))
        assert out == "- x\n- y\n"

    def test_non_bullets_unaffected(self):
        out = render(para("plain\n"), para("Heading\n", style="HEADING_1"))
        assert out == "plain\n## Heading\n"


class TestTableIsolation:
    def test_table_does_not_swallow_neighbours(self):
        out = render(
            para("before\n"),
            table((cell(para("A\n")), cell(para("B\n")))),
            para("after\n"),
        )
        assert "before" in out and "after" in out
        assert out.index("before") < out.index("| A |") < out.index("after")
