from decafclaw.skills.vault._sections import Document, describe_section_miss


def test_document_round_trip():
    text = "# Title\n\nBody line.\n\n## Sub\n\n- item\n"
    doc = Document.from_text(text)
    assert doc.to_text() == text


def test_section_walk_by_path():
    text = "# Top\n\n## Child\n\ncontent\n"
    doc = Document.from_text(text)
    sec = doc.find_section("top/child")
    assert sec is not None
    assert sec.normalized_title == "child"


# --- Path resolution (#671) -------------------------------------------------
#
# Section paths used to be resolved strictly from the document's top-level
# sections. Since an H1 page has exactly one top-level section (the H1), every
# `##` heading was addressable only as '<H1 Title>/<Section>' — so the obvious
# spelling, a bare heading title, always missed.

NESTED = "# Project Notes\n\n## Background\n\nbg\n\n## Archive\n\n### Background\n\nold bg\n\n## TODO\n\n- Old item\n"

FLAT = "# Project Notes\n\n## Background\n\nbg\n\n## TODO\n\n- x\n"


def test_bare_title_resolves_without_h1_root():
    doc = Document.from_text(FLAT)
    assert doc.find_section("Background") is not None
    assert doc.find_section("TODO") is not None
    # The rooted form keeps working.
    assert doc.find_section("Project Notes/Background") is not None


def test_partial_path_resolves():
    doc = Document.from_text(NESTED)
    sec = doc.find_section("Archive/Background")
    assert sec is not None and sec.level == 3


def test_exact_path_wins_over_suffix():
    doc = Document.from_text(NESTED)
    sec = doc.find_section("Project Notes/Background")
    assert sec is not None and sec.level == 2


def test_ambiguous_bare_title_resolves_to_nothing():
    """Two sections titled 'Background' — a guess would edit the wrong one."""
    doc = Document.from_text(NESTED)
    assert doc.find_section("Background") is None


def test_heading_hashes_are_tolerated():
    doc = Document.from_text("# Project Notes\n\n## Background\n\nbg\n")
    assert doc.find_section("## Background") is not None
    assert doc.find_section("#Background") is not None


def test_add_section_after_bare_title():
    """The operation from the #671 repro."""
    doc = Document.from_text(FLAT)
    assert doc.add_section("Status", level=2, after="Background") is True
    assert "## Status" in doc.to_text()


def test_empty_and_slash_only_paths_resolve_to_nothing():
    doc = Document.from_text(NESTED)
    assert doc.find_section("") is None
    assert doc.find_section("/") is None


# --- Miss diagnostics (#671) ------------------------------------------------
#
# find_section returns None for both "ambiguous" and "missing". These let the
# error sites tell them apart and say something the caller can act on.


def test_section_candidates_lists_ambiguous_matches():
    doc = Document.from_text(NESTED)
    got = doc.section_candidates("Background")
    assert got == ["Project Notes/Background", "Project Notes/Archive/Background"]


def test_section_candidates_empty_when_missing():
    doc = Document.from_text(NESTED)
    assert doc.section_candidates("Nonexistent") == []


def test_candidate_paths_are_valid_input():
    """Whatever the error offers must resolve when pasted back."""
    doc = Document.from_text(NESTED)
    candidates = doc.section_candidates("Background")
    assert candidates
    for candidate in candidates:
        assert doc.find_section(candidate) is not None


def test_all_section_paths_in_document_order():
    doc = Document.from_text(NESTED)
    assert doc.all_section_paths() == [
        "Project Notes",
        "Project Notes/Background",
        "Project Notes/Archive",
        "Project Notes/Archive/Background",
        "Project Notes/TODO",
    ]


def test_describe_section_miss_ambiguous_names_candidates():
    doc = Document.from_text(NESTED)
    msg = describe_section_miss(doc, "Background")
    assert "ambiguous" in msg
    assert "Project Notes/Background" in msg
    assert "Project Notes/Archive/Background" in msg


def test_describe_section_miss_missing_lists_known_paths():
    doc = Document.from_text(NESTED)
    msg = describe_section_miss(doc, "Nope")
    assert "not found" in msg
    assert "Project Notes/TODO" in msg


def test_describe_section_miss_truncates_long_lists():
    body = "# Root\n\n" + "".join(f"## S{i}\n\ntext\n\n" for i in range(30))
    doc = Document.from_text(body)
    msg = describe_section_miss(doc, "Nope")
    assert "and 11 more" in msg  # 31 sections total, 20 shown


def test_describe_section_miss_on_page_with_no_sections():
    doc = Document.from_text("just body text, no headings\n")
    msg = describe_section_miss(doc, "Anything")
    assert "no sections" in msg


# --- Heading level inference (#671 Phase 6) ---------------------------------
#
# `level` defaulted to 1, so "add a section after ## Background" inserted an
# H1 mid-page — which reparents every section below it under the new heading.


def test_level_defaults_to_sibling_of_after_anchor():
    doc = Document.from_text(FLAT)
    assert doc.add_section("Status", content="Working on it.", after="Background") is True
    added = Document.from_text(doc.to_text()).find_section("Status")
    assert added is not None
    assert added.level == 2  # sibling of ## Background, not a new H1


def test_level_defaults_to_sibling_of_before_anchor():
    doc = Document.from_text(FLAT)
    assert doc.add_section("Status", before="TODO") is True
    assert "## Status" in doc.to_text()


def test_level_defaults_to_child_of_parent_anchor():
    doc = Document.from_text(FLAT)
    assert doc.add_section("Detail", parent="Background") is True
    assert "### Detail" in doc.to_text()


def test_explicit_level_still_wins():
    doc = Document.from_text(FLAT)
    assert doc.add_section("Deep", level=4, after="Background") is True
    assert "#### Deep" in doc.to_text()


def test_level_defaults_to_one_with_no_anchor():
    doc = Document.from_text(FLAT)
    assert doc.add_section("Appendix") is True
    assert "\n# Appendix\n" in doc.to_text()


def test_inferred_level_keeps_the_tree_intact():
    """The point of the inference: following sections must not be reparented."""
    doc = Document.from_text(FLAT)
    doc.add_section("Status", content="Working on it.", after="Background")
    reparsed = Document.from_text(doc.to_text())
    assert reparsed.all_section_paths() == [
        "Project Notes",
        "Project Notes/Background",
        "Project Notes/Status",
        "Project Notes/TODO",
    ]


def test_describe_section_miss_duplicate_headings_dont_advise_a_longer_path():
    """Two headings with the same path can't be separated by a longer path.

    Telling the caller to lengthen it is a dead end — observed sending the
    agent into a retry loop until it blew its tool budget.
    """
    doc = Document.from_text("# Project Notes\n\n## Status\n\na\n\n## Status\n\nb\n")
    msg = describe_section_miss(doc, "Status")
    assert "Use a longer path" not in msg
    assert "duplicate headings" in msg
    assert "vault_read" in msg


def test_after_wins_when_several_anchors_are_passed():
    """Anchor selection and insertion must use the same precedence.

    The refactor picked the anchor by after > before > parent but re-tested
    `before` at the insertion branch, so passing both put the section at the
    `after` target's *heading* line — i.e. before it. Caught in review.
    """
    doc = Document.from_text(FLAT)
    assert doc.add_section("Status", after="Background", before="TODO") is True
    text = doc.to_text()
    # 'after=Background' wins: Status sits between Background's body and TODO.
    assert text.index("## Background") < text.index("## Status") < text.index("## TODO")
    assert text.index("bg") < text.index("## Status")


# --- Public line operations (#1377) -----------------------------------------
#
# The vault tools used to reach into the private `_delete_lines` and import the
# module-level `_insert_into_doc`. These are now public Document methods; these
# cases pin their behavior, including the dirty/re-parse and blank-collapse
# invariants they must maintain.


def test_delete_lines_removes_the_right_lines():
    doc = Document.from_text("# Top\n\nline1\nline2\nline3\n")
    # indices: 0='# Top', 1='', 2='line1', 3='line2', 4='line3'
    doc.delete_lines(2, 2)
    text = doc.to_text()
    assert "line1" not in text
    assert "line2" not in text
    assert "line3" in text


def test_delete_lines_collapses_blank_runs():
    """Deleting a line that leaves 3+ consecutive blanks must collapse to two."""
    doc = Document.from_text("# Top\n\na\n\n\nb\n")
    # lines: ['# Top', '', 'a', '', '', 'b']; deleting 'a' leaves a 3-blank run.
    doc.delete_lines(2, 1)
    assert doc.lines == ["# Top\n", "\n", "\n", "b\n"]


def test_delete_lines_marks_dirty_and_reparses():
    """Removing a heading must make it disappear on the next section lookup."""
    doc = Document.from_text("# Top\n\n## A\n\nx\n\n## B\n\ny\n")
    doc.delete_lines(2, 1)  # remove the '## A' heading line
    assert doc.find_section("top/a") is None
    assert doc.find_section("top/b") is not None


def test_insert_lines_append_into_section():
    doc = Document.from_text("# Top\n\n## Notes\n\nexisting\n")
    err = doc.insert_lines(["new1", "new2"], to_section="top/notes", position="append")
    assert err is None
    text = doc.to_text()
    assert "new1" in text and "new2" in text
    # Appended after the existing content, in order.
    assert text.index("existing") < text.index("new1") < text.index("new2")


def test_insert_lines_prepend_before_first_list_item():
    doc = Document.from_text("# Top\n\n## Notes\n\n- item1\n- item2\n")
    err = doc.insert_lines(["new"], to_section="top/notes", position="prepend")
    assert err is None
    lines = doc.to_text().splitlines()
    # 'new' lands before the first list item, not at the raw content start.
    assert lines.index("new") < lines.index("- item1")


def test_insert_lines_append_without_section():
    doc = Document.from_text("# Top\n\nbody\n")
    err = doc.insert_lines(["new"], position="append")  # no to_section
    assert err is None
    assert doc.lines[-1] == "new\n"


def test_insert_lines_prepend_without_section():
    doc = Document.from_text("# Top\n\n- item1\n")
    err = doc.insert_lines(["new"], position="prepend")  # no to_section
    assert err is None
    lines = doc.to_text().splitlines()
    assert lines.index("new") < lines.index("- item1")


def test_insert_lines_returns_error_for_missing_section():
    doc = Document.from_text("# Top\n\n## Notes\n")
    err = doc.insert_lines(["new"], to_section="top/nowhere", position="append")
    assert err is not None
    assert "not found" in err
    # The document must be left untouched on failure.
    assert doc.to_text() == "# Top\n\n## Notes\n"


def test_insert_lines_marks_dirty_and_reparses():
    """A heading inserted via insert_lines must be resolvable after re-parse."""
    doc = Document.from_text("# Top\n")
    err = doc.insert_lines(["## New", "body"], position="append")
    assert err is None
    assert doc.find_section("top/new") is not None
