"""Tests for the markdown vault library."""

import datetime
import textwrap

import pytest

from decafclaw.skills.markdown_vault.tools import (
    Document,
    bulk_move_items,
    daily_path,
    extract_tags,
    move_item_across_files,
    normalize_title,
)

# -- Fixture document used by most tests --

SAMPLE = textwrap.dedent("""\
    # daily
    - [x] exercise for 30 minutes
    - [ ] write 3 journal pages

    # today
    - [ ] consider renovatebot
    - [ ] take a look at thingyjobber

    # tonight
    - [ ] New batteries in exercise bike

    # tomorrow

    # this week
    - [ ] Haircut appt
    - [ ] Schedule a flu shot

    # [[someday]]

    # followup

    # notes

    ## standup
    - project demos
    """)


@pytest.fixture
def doc():
    return Document(SAMPLE)


# -- Section parsing --


class TestSectionParsing:
    def test_top_level_sections(self, doc):
        titles = [s.title for s in doc.sections]
        assert titles == [
            "daily", "today", "tonight", "tomorrow",
            "this week", "[[someday]]", "followup", "notes",
        ]

    def test_nested_section(self, doc):
        notes = doc.find_section("notes")
        assert notes is not None
        assert len(notes.children) == 1
        assert notes.children[0].title == "standup"

    def test_section_levels(self, doc):
        for sec in doc.sections:
            assert sec.level == 1
        standup = doc.find_section("notes/standup")
        assert standup is not None
        assert standup.level == 2


# -- Section lookup --


class TestSectionLookup:
    def test_simple_path(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        assert sec.title == "today"

    def test_nested_path(self, doc):
        sec = doc.find_section("notes/standup")
        assert sec is not None
        assert sec.title == "standup"

    def test_wikilink_title(self, doc):
        sec = doc.find_section("someday")
        assert sec is not None
        assert sec.title == "[[someday]]"

    def test_case_insensitive(self, doc):
        assert doc.find_section("TODAY") is not None
        assert doc.find_section("This Week") is not None

    def test_nonexistent(self, doc):
        assert doc.find_section("nope") is None
        assert doc.find_section("notes/nope") is None

    def test_list_sections(self, doc):
        flat = doc.list_sections()
        # All top-level at depth 0, tabstack at depth 1
        depths = {sec.title: depth for depth, sec in flat}
        assert depths["daily"] == 0
        assert depths["notes"] == 0
        assert depths["standup"] == 1


# -- Normalize title --


class TestNormalizeTitle:
    def test_plain(self):
        assert normalize_title("today") == "today"

    def test_wikilink(self):
        assert normalize_title("[[someday]]") == "someday"

    def test_wikilink_with_alias(self):
        assert normalize_title("[[path/to/page|display text]]") == "display text"

    def test_mixed(self):
        assert normalize_title("See [[link]] here") == "see link here"


# -- Checklist items --


class TestChecklistItems:
    def test_get_items(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        items = doc.get_items(sec)
        assert len(items) == 2
        assert items[0].text == "consider renovatebot"
        assert not items[0].checked

    def test_get_items_with_checked(self, doc):
        sec = doc.find_section("daily")
        assert sec is not None
        items = doc.get_items(sec)
        assert len(items) == 2
        assert items[0].checked
        assert not items[1].checked

    def test_get_items_empty_section(self, doc):
        sec = doc.find_section("tomorrow")
        assert sec is not None
        items = doc.get_items(sec)
        assert items == []

    def test_items_dont_include_children(self, doc):
        """Items in notes/tabstack shouldn't appear in notes' own items."""
        sec = doc.find_section("notes")
        assert sec is not None
        items = doc.get_items(sec)
        assert items == []


# -- Check / uncheck --


class TestCheckUncheck:
    def test_check_by_match(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        assert doc.check_item(sec, match="thingyjobber")
        items = doc.get_items(sec)
        assert items[1].checked

    def test_uncheck_by_match(self, doc):
        sec = doc.find_section("daily")
        assert sec is not None
        assert doc.uncheck_item(sec, match="exercise")
        items = doc.get_items(sec)
        assert not items[0].checked

    def test_check_by_index(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        assert doc.check_item(sec, index=0)
        items = doc.get_items(sec)
        assert items[0].checked

    def test_check_by_negative_index(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        assert doc.check_item(sec, index=-1)
        items = doc.get_items(sec)
        assert items[-1].checked

    def test_check_no_match(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        assert not doc.check_item(sec, match="nonexistent")

    def test_check_bad_index(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        assert not doc.check_item(sec, index=99)

    def test_check_preserves_other_lines(self, doc):
        original = str(doc)
        sec = doc.find_section("today")
        assert sec is not None
        doc.check_item(sec, index=0)
        # Only the checked line should differ
        orig_lines = original.splitlines()
        new_lines = str(doc).splitlines()
        diffs = [
            i for i, (a, b) in enumerate(zip(orig_lines, new_lines)) if a != b
        ]
        assert len(diffs) == 1


# -- Append / prepend / insert --


class TestContentOps:
    def test_append_to_section_with_items(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        doc.append(sec, "- [ ] new task")
        items = doc.get_items(doc.find_section("today"))
        assert items[-1].text == "new task"

    def test_append_to_empty_section(self, doc):
        sec = doc.find_section("tomorrow")
        assert sec is not None
        doc.append(sec, "- [ ] something tomorrow")
        items = doc.get_items(doc.find_section("tomorrow"))
        assert len(items) == 1
        assert items[0].text == "something tomorrow"

    def test_prepend_to_section(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        doc.prepend(sec, "- [ ] urgent task")
        items = doc.get_items(doc.find_section("today"))
        assert items[0].text == "urgent task"
        assert len(items) == 3  # original 2 + new 1

    def test_insert_at_index(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        doc.insert_item(sec, 1, "- [ ] inserted task")
        items = doc.get_items(doc.find_section("today"))
        assert items[1].text == "inserted task"
        assert len(items) == 3

    def test_insert_at_zero(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        doc.insert_item(sec, 0, "- [ ] first task")
        items = doc.get_items(doc.find_section("today"))
        assert items[0].text == "first task"

    def test_insert_past_end_appends(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        doc.insert_item(sec, 99, "- [ ] last task")
        items = doc.get_items(doc.find_section("today"))
        assert items[-1].text == "last task"

    def test_append_doesnt_corrupt_next_section(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        doc.append(sec, "- [ ] new task")
        # tonight section should still be intact
        tonight = doc.find_section("tonight")
        assert tonight is not None
        items = doc.get_items(tonight)
        assert len(items) == 1
        assert items[0].text == "New batteries in exercise bike"


# -- Delete --


class TestDelete:
    def test_delete_by_match(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        assert doc.delete_item(sec, match="thingyjobber")
        items = doc.get_items(doc.find_section("today"))
        assert len(items) == 1
        assert all("thingyjobber" not in item.text for item in items)

    def test_delete_by_index(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        original_second = doc.get_items(sec)[1].text
        assert doc.delete_item(sec, index=0)
        items = doc.get_items(doc.find_section("today"))
        assert len(items) == 1
        assert items[0].text == original_second

    def test_delete_no_match(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        assert not doc.delete_item(sec, match="nonexistent")

    def test_delete_preserves_structure(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        doc.delete_item(sec, index=0)
        # All sections still findable
        for name in ["daily", "today", "tonight", "tomorrow", "this week", "someday", "notes"]:
            assert doc.find_section(name) is not None


# -- Section operations (add / remove / move) --


class TestAddSection:
    def test_add_at_end(self, doc):
        assert doc.add_section("new section")
        sec = doc.find_section("new section")
        assert sec is not None
        assert sec.level == 1

    def test_add_after(self, doc):
        assert doc.add_section("inserted", after="today")
        sections = [s.title for s in doc.sections]
        today_idx = sections.index("today")
        assert sections[today_idx + 1] == "inserted"

    def test_add_before(self, doc):
        assert doc.add_section("inserted", before="tonight")
        sections = [s.title for s in doc.sections]
        tonight_idx = sections.index("tonight")
        assert sections[tonight_idx - 1] == "inserted"

    def test_add_as_child(self, doc):
        assert doc.add_section("retro", level=2, parent="notes")
        notes = doc.find_section("notes")
        assert notes is not None
        child_titles = [c.title for c in notes.children]
        assert "retro" in child_titles

    def test_add_with_content(self, doc):
        assert doc.add_section("new", content="- [ ] first item", after="today")
        sec = doc.find_section("new")
        assert sec is not None
        items = doc.get_items(sec)
        assert len(items) == 1
        assert items[0].text == "first item"

    def test_add_bad_target(self, doc):
        assert not doc.add_section("x", after="nonexistent")

    def test_add_preserves_existing(self, doc):
        original_names = [s.normalized_title for s in doc.sections]
        doc.add_section("new section")
        for name in original_names:
            assert doc.find_section(name) is not None


class TestRemoveSection:
    def test_remove_top_level(self, doc):
        assert doc.remove_section("tonight") is not None
        assert doc.find_section("tonight") is None
        # Neighbors still intact
        assert doc.find_section("today") is not None
        assert doc.find_section("tomorrow") is not None

    def test_remove_with_children(self, doc):
        """Removing 'notes' should also remove its 'standup' child."""
        assert doc.remove_section("notes") is not None
        assert doc.find_section("notes") is None
        assert doc.find_section("notes/standup") is None

    def test_remove_child_only(self, doc):
        """Removing 'notes/standup' should leave 'notes' intact."""
        assert doc.remove_section("notes/standup") is not None
        assert doc.find_section("notes") is not None
        assert doc.find_section("notes/standup") is None

    def test_remove_returns_lines(self, doc):
        removed = doc.remove_section("tonight")
        assert removed is not None
        text = "".join(removed)
        assert "# tonight" in text
        assert "exercise bike" in text

    def test_remove_nonexistent(self, doc):
        assert doc.remove_section("nope") is None

    def test_remove_preserves_other_sections(self, doc):
        doc.remove_section("today")
        for name in ["daily", "tonight", "tomorrow", "this week", "someday", "notes"]:
            assert doc.find_section(name) is not None


class TestMoveSection:
    def test_move_after(self, doc):
        assert doc.move_section("tonight", after="this week")
        sections = [s.title for s in doc.sections]
        week_idx = sections.index("this week")
        assert sections[week_idx + 1] == "tonight"

    def test_move_before(self, doc):
        assert doc.move_section("tonight", before="daily")
        sections = [s.title for s in doc.sections]
        assert sections[0] == "tonight"

    def test_move_to_end(self, doc):
        assert doc.move_section("daily")
        sections = [s.title for s in doc.sections]
        # daily should now be last top-level section
        assert sections[-1] == "daily"
        # it should no longer be first
        assert sections[0] != "daily"

    def test_move_preserves_content(self, doc):
        # Get original items
        sec = doc.find_section("today")
        assert sec is not None
        original_items = [item.text for item in doc.get_items(sec)]

        doc.move_section("today", after="this week")

        sec = doc.find_section("today")
        assert sec is not None
        moved_items = [item.text for item in doc.get_items(sec)]
        assert moved_items == original_items

    def test_move_bad_source(self, doc):
        assert not doc.move_section("nonexistent", after="daily")

    def test_move_bad_target(self, doc):
        assert not doc.move_section("today", after="nonexistent")

    def test_move_preserves_all_sections(self, doc):
        all_titles = {s.title for s in doc.sections}
        doc.move_section("tonight", after="this week")
        new_titles = {s.title for s in doc.sections}
        assert all_titles == new_titles


# -- Replace item --


class TestReplaceItem:
    def test_replace_by_match(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        assert doc.replace_item(sec, "updated task text", match="renovatebot")
        items = doc.get_items(sec)
        assert items[0].text == "updated task text"

    def test_replace_preserves_checked_state(self, doc):
        sec = doc.find_section("daily")
        # First item is checked
        assert doc.get_items(sec)[0].checked
        assert doc.replace_item(sec, "new exercise plan", index=0)
        items = doc.get_items(sec)
        assert items[0].text == "new exercise plan"
        assert items[0].checked  # still checked

    def test_replace_by_index(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        assert doc.replace_item(sec, "replaced", index=-1)
        items = doc.get_items(sec)
        assert items[-1].text == "replaced"

    def test_replace_not_found(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        assert not doc.replace_item(sec, "x", match="nonexistent")

    def test_replace_preserves_other_lines(self, doc):
        original = str(doc)
        sec = doc.find_section("today")
        assert sec is not None
        doc.replace_item(sec, "changed", index=0)
        orig_lines = original.splitlines()
        new_lines = str(doc).splitlines()
        diffs = [i for i, (a, b) in enumerate(zip(orig_lines, new_lines)) if a != b]
        assert len(diffs) == 1


# -- Move item --


class TestMoveItem:
    def test_move_by_match(self, doc):
        sec_from = doc.find_section("today")
        sec_to = doc.find_section("tomorrow")
        assert sec_to is not None
        assert doc.move_item(sec_from, sec_to, match="renovatebot")
        # Gone from today
        today_items = doc.get_items(doc.find_section("today"))
        assert all("renovatebot" not in i.text for i in today_items)
        # Present in tomorrow
        tomorrow_items = doc.get_items(doc.find_section("tomorrow"))
        assert any("renovatebot" in i.text for i in tomorrow_items)

    def test_move_by_index(self, doc):
        sec_from = doc.find_section("today")
        sec_to = doc.find_section("tonight")
        original_text = doc.get_items(sec_from)[0].text
        assert doc.move_item(sec_from, sec_to, index=0)
        tonight_items = doc.get_items(doc.find_section("tonight"))
        assert any(i.text == original_text for i in tonight_items)

    def test_move_not_found(self, doc):
        sec_from = doc.find_section("today")
        sec_to = doc.find_section("tomorrow")
        assert sec_to is not None
        assert not doc.move_item(sec_from, sec_to, match="nonexistent")

    def test_move_preserves_counts(self, doc):
        today_count = len(doc.get_items(doc.find_section("today")))
        tomorrow_count = len(doc.get_items(doc.find_section("tomorrow")))
        sec_from = doc.find_section("today")
        sec_to = doc.find_section("tomorrow")
        assert sec_to is not None
        doc.move_item(sec_from, sec_to, index=0)
        assert len(doc.get_items(doc.find_section("today"))) == today_count - 1
        assert len(doc.get_items(doc.find_section("tomorrow"))) == tomorrow_count + 1


# -- Bulk check / uncheck --


class TestBulkCheckUncheck:
    def test_bulk_check(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        count = doc.bulk_check(sec)
        assert count == 2  # both items were unchecked
        items = doc.get_items(sec)
        assert all(i.checked for i in items)

    def test_bulk_check_already_checked(self, doc):
        sec = doc.find_section("daily")
        # exercise is already checked, journal is not
        count = doc.bulk_check(sec)
        assert count == 1  # only journal changed
        items = doc.get_items(sec)
        assert all(i.checked for i in items)

    def test_bulk_uncheck(self, doc):
        sec = doc.find_section("daily")
        assert sec is not None
        count = doc.bulk_uncheck(sec)
        assert count == 1  # exercise was checked
        items = doc.get_items(sec)
        assert all(not i.checked for i in items)

    def test_bulk_check_empty_section(self, doc):
        sec = doc.find_section("tomorrow")
        assert sec is not None
        count = doc.bulk_check(sec)
        assert count == 0

    def test_bulk_uncheck_returns_zero_if_none_checked(self, doc):
        sec = doc.find_section("today")
        assert sec is not None
        count = doc.bulk_uncheck(sec)
        assert count == 0


# -- Find items --


class TestFindItems:
    def test_find_across_sections(self, doc):
        results = doc.find_items("appt")
        assert len(results) == 1
        sec, item = results[0]
        assert sec.title == "this week"
        assert "Haircut" in item.text

    def test_find_multiple_matches(self, doc):
        # "exercise" appears in daily, "exercise bike" in tonight
        results = doc.find_items("exercise")
        assert len(results) == 2
        sections = {sec.title for sec, _ in results}
        assert "daily" in sections
        assert "tonight" in sections

    def test_find_no_matches(self, doc):
        results = doc.find_items("xyzzy")
        assert results == []

    def test_find_case_insensitive(self, doc):
        results = doc.find_items("HAIRCUT")
        assert len(results) == 1


# -- Rename section --


class TestRenameSection:
    def test_rename(self, doc):
        assert doc.rename_section("tonight", "this evening")
        assert doc.find_section("tonight") is None
        assert doc.find_section("this evening") is not None

    def test_rename_preserves_content(self, doc):
        items_before = [i.text for i in doc.get_items(doc.find_section("tonight"))]
        doc.rename_section("tonight", "this evening")
        items_after = [i.text for i in doc.get_items(doc.find_section("this evening"))]
        assert items_before == items_after

    def test_rename_preserves_level(self, doc):
        doc.rename_section("notes/standup", "weekly sync")
        sec = doc.find_section("notes/weekly sync")
        assert sec is not None
        assert sec.level == 2

    def test_rename_nonexistent(self, doc):
        assert not doc.rename_section("nope", "x")


# -- Replace section content --


class TestReplaceSectionContent:
    def test_replace_content(self, doc):
        assert doc.replace_section_content("today", "- [ ] brand new task\n- [ ] another new task")
        items = doc.get_items(doc.find_section("today"))
        assert len(items) == 2
        assert items[0].text == "brand new task"
        assert items[1].text == "another new task"

    def test_replace_preserves_heading(self, doc):
        doc.replace_section_content("today", "new stuff")
        sec = doc.find_section("today")
        assert sec is not None
        assert sec.title == "today"
        assert sec.level == 1

    def test_replace_preserves_children(self, doc):
        doc.replace_section_content("notes", "Some general notes here.")
        # children should still be there
        standup = doc.find_section("notes/standup")
        assert standup is not None

    def test_replace_empty_section(self, doc):
        assert doc.replace_section_content("tomorrow", "- [ ] plan for tomorrow")
        items = doc.get_items(doc.find_section("tomorrow"))
        assert len(items) == 1

    def test_replace_nonexistent(self, doc):
        assert not doc.replace_section_content("nope", "x")


# -- Tag operations --

TAGGED_SAMPLE = textwrap.dedent("""\
    # notes
    - #storyidea culture-esque setting where a character lives as a fire watch keeper

    # today
    - [ ] consider something #research
    - [ ] write blog post #writing #blog
    - [x] fix the build

    # tonight
    - [ ] read that article #reading

    # ideas
    - #storyidea a lighthouse keeper AI story
    - #gamedev retro game jam concept
    """)


class TestExtractTags:
    def test_single_tag(self):
        assert extract_tags("- #storyidea some text") == ["storyidea"]

    def test_multiple_tags(self):
        assert extract_tags("- [ ] write post #writing #blog") == ["writing", "blog"]

    def test_tag_at_start(self):
        assert extract_tags("#storyidea some text") == ["storyidea"]

    def test_tag_at_end(self):
        assert extract_tags("some text #research") == ["research"]

    def test_camelcase(self):
        assert extract_tags("#CamelCase tag") == ["CamelCase"]

    def test_hyphenated(self):
        assert extract_tags("#multi-word-tag here") == ["multi-word-tag"]

    def test_no_tags(self):
        assert extract_tags("plain text without tags") == []

    def test_heading_not_a_tag(self):
        """Headings start with # but aren't tags."""
        assert extract_tags("## some heading") == []

    def test_url_fragment_not_a_tag(self):
        """#anchors in URLs should not be extracted as tags."""
        # This is a known limitation — anchor after text with no space will match.
        # But anchors in URLs (no space before #) won't match.
        assert extract_tags("https://example.com/page#section") == []


class TestListTags:
    def test_list_tags(self):
        doc = Document(TAGGED_SAMPLE)
        tags = doc.list_tags()
        assert "storyidea" in tags
        assert "research" in tags
        assert "writing" in tags
        assert "blog" in tags
        assert "reading" in tags
        assert "gamedev" in tags

    def test_list_tags_sorted(self):
        doc = Document(TAGGED_SAMPLE)
        tags = doc.list_tags()
        assert tags == sorted(tags)

    def test_list_tags_unique(self):
        """storyidea appears twice but should only be listed once."""
        doc = Document(TAGGED_SAMPLE)
        tags = doc.list_tags()
        assert tags.count("storyidea") == 1

    def test_no_tags(self, doc):
        """The standard SAMPLE fixture has no tags."""
        assert doc.list_tags() == []


class TestFindTagged:
    def test_find_by_tag(self):
        doc = Document(TAGGED_SAMPLE)
        results = doc.find_tagged("storyidea")
        assert len(results) == 2
        texts = [text for _, _, text in results]
        assert any("culture-esque" in t for t in texts)
        assert any("lighthouse" in t for t in texts)

    def test_find_case_insensitive(self):
        doc = Document(TAGGED_SAMPLE)
        results = doc.find_tagged("STORYIDEA")
        assert len(results) == 2

    def test_find_with_hash_prefix(self):
        doc = Document(TAGGED_SAMPLE)
        results = doc.find_tagged("#research")
        assert len(results) == 1

    def test_find_returns_section(self):
        doc = Document(TAGGED_SAMPLE)
        results = doc.find_tagged("reading")
        assert len(results) == 1
        sec, _, _ = results[0]
        assert sec is not None
        assert sec.title == "tonight"

    def test_find_no_matches(self):
        doc = Document(TAGGED_SAMPLE)
        assert doc.find_tagged("nonexistent") == []


class TestAddTag:
    def test_add_tag(self):
        doc = Document(TAGGED_SAMPLE)
        sec = doc.find_section("today")
        assert sec is not None
        assert doc.add_tag(sec, "urgent", index=0)
        line = doc.lines[sec.content_start]
        assert "#urgent" in line
        assert "#research" in line  # original tag preserved

    def test_add_tag_idempotent(self):
        doc = Document(TAGGED_SAMPLE)
        sec = doc.find_section("today")
        assert sec is not None
        assert doc.add_tag(sec, "research", index=0)
        # Should not duplicate
        tags = extract_tags(doc.lines[sec.content_start])
        assert tags.count("research") == 1

    def test_add_tag_not_found(self):
        doc = Document(TAGGED_SAMPLE)
        sec = doc.find_section("today")
        assert sec is not None
        assert not doc.add_tag(sec, "x", match="nonexistent")

    def test_add_tag_with_hash_prefix(self):
        doc = Document(TAGGED_SAMPLE)
        sec = doc.find_section("today")
        assert sec is not None
        assert doc.add_tag(sec, "#priority", index=0)
        line = doc.lines[sec.content_start]
        assert "#priority" in line
        # Should not have double hash
        assert "##priority" not in line


class TestRemoveTag:
    def test_remove_tag(self):
        doc = Document(TAGGED_SAMPLE)
        sec = doc.find_section("today")
        assert sec is not None
        assert doc.remove_tag(sec, "research", index=0)
        line = doc.lines[sec.content_start]
        assert "#research" not in line
        assert "consider something" in line  # text preserved

    def test_remove_one_of_multiple(self):
        doc = Document(TAGGED_SAMPLE)
        sec = doc.find_section("today")
        assert sec is not None
        assert doc.remove_tag(sec, "blog", index=1)
        line = doc.lines[sec.content_start + 1]
        assert "#blog" not in line
        assert "#writing" in line  # other tag preserved

    def test_remove_tag_not_found(self):
        doc = Document(TAGGED_SAMPLE)
        sec = doc.find_section("today")
        assert sec is not None
        assert not doc.remove_tag(sec, "x", match="nonexistent")

    def test_remove_tag_case_insensitive(self):
        doc = Document(TAGGED_SAMPLE)
        sec = doc.find_section("today")
        assert sec is not None
        assert doc.remove_tag(sec, "RESEARCH", index=0)
        line = doc.lines[sec.content_start]
        assert "#research" not in line


# -- Daily path --


class TestDailyPath:
    def test_today(self):
        today = datetime.date.today()
        result = daily_path()
        assert result == f"journals/{today.year}/{today.isoformat()}.md"

    def test_specific_date(self):
        assert daily_path("2026-03-17") == "journals/2026/2026-03-17.md"

    def test_date_object(self):
        d = datetime.date(2025, 12, 31)
        assert daily_path(d) == "journals/2025/2025-12-31.md"

    def test_offset_yesterday(self):
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        assert daily_path(offset=-1) == f"journals/{yesterday.year}/{yesterday.isoformat()}.md"

    def test_offset_tomorrow(self):
        today = datetime.date.today()
        tomorrow = today + datetime.timedelta(days=1)
        assert daily_path(offset=1) == f"journals/{tomorrow.year}/{tomorrow.isoformat()}.md"

    def test_date_plus_offset(self):
        assert daily_path("2026-03-17", offset=-1) == "journals/2026/2026-03-16.md"

    def test_year_boundary(self):
        assert daily_path("2026-01-01", offset=-1) == "journals/2025/2025-12-31.md"


# -- Cross-file move --

YESTERDAY = textwrap.dedent("""\
    # today
    - [ ] unchecked from yesterday
    - [x] done from yesterday
    - [ ] another unchecked

    # tonight
    - [ ] evening task
    """)

TODAY = textwrap.dedent("""\
    # today
    - [ ] fresh task for today

    # tonight

    # tomorrow
    """)


class TestMoveItemAcrossFiles:
    def test_move_single_by_match(self):
        from_doc = Document(YESTERDAY)
        to_doc = Document(TODAY)
        assert move_item_across_files(
            from_doc, "today", to_doc, "today", match="unchecked from"
        )
        # Gone from source
        from_sec = from_doc.find_section("today")
        assert from_sec is not None
        from_items = from_doc.get_items(from_sec)
        assert all("unchecked from yesterday" not in i.text for i in from_items)
        # Present in target
        to_sec = to_doc.find_section("today")
        assert to_sec is not None
        to_items = to_doc.get_items(to_sec)
        assert any("unchecked from yesterday" in i.text for i in to_items)

    def test_move_single_by_index(self):
        from_doc = Document(YESTERDAY)
        to_doc = Document(TODAY)
        from_sec = from_doc.find_section("today")
        assert from_sec is not None
        text = from_doc.get_items(from_sec)[0].text
        assert move_item_across_files(
            from_doc, "today", to_doc, "today", index=0
        )
        to_sec = to_doc.find_section("today")
        assert to_sec is not None
        to_items = to_doc.get_items(to_sec)
        assert any(i.text == text for i in to_items)

    def test_move_not_found(self):
        from_doc = Document(YESTERDAY)
        to_doc = Document(TODAY)
        assert not move_item_across_files(
            from_doc, "today", to_doc, "today", match="nonexistent"
        )


class TestBulkMoveItems:
    def test_move_all(self):
        from_doc = Document(YESTERDAY)
        to_doc = Document(TODAY)
        count = bulk_move_items(from_doc, "today", to_doc, "today")
        assert count == 3
        from_sec = from_doc.find_section("today")
        assert from_sec is not None
        assert from_doc.get_items(from_sec) == []
        to_sec = to_doc.find_section("today")
        assert to_sec is not None
        assert len(to_doc.get_items(to_sec)) == 4  # 1 original + 3 moved

    def test_move_unchecked_only(self):
        from_doc = Document(YESTERDAY)
        to_doc = Document(TODAY)
        count = bulk_move_items(
            from_doc, "today", to_doc, "today", unchecked_only=True
        )
        assert count == 2
        from_sec = from_doc.find_section("today")
        assert from_sec is not None
        from_items = from_doc.get_items(from_sec)
        assert len(from_items) == 1
        assert from_items[0].checked

    def test_move_checked_only(self):
        from_doc = Document(YESTERDAY)
        to_doc = Document(TODAY)
        count = bulk_move_items(
            from_doc, "today", to_doc, "today", checked_only=True
        )
        assert count == 1
        from_sec = from_doc.find_section("today")
        assert from_sec is not None
        from_items = from_doc.get_items(from_sec)
        assert len(from_items) == 2
        assert all(not i.checked for i in from_items)

    def test_move_by_indices(self):
        from_doc = Document(YESTERDAY)
        to_doc = Document(TODAY)
        count = bulk_move_items(
            from_doc, "today", to_doc, "today", indices=[0, 2]
        )
        assert count == 2
        from_sec = from_doc.find_section("today")
        assert from_sec is not None
        from_items = from_doc.get_items(from_sec)
        assert len(from_items) == 1
        assert from_items[0].checked

    def test_move_across_sections(self):
        from_doc = Document(YESTERDAY)
        to_doc = Document(TODAY)
        count = bulk_move_items(from_doc, "tonight", to_doc, "tonight")
        assert count == 1
        to_sec = to_doc.find_section("tonight")
        assert to_sec is not None
        to_items = to_doc.get_items(to_sec)
        assert any("evening task" in i.text for i in to_items)

    def test_move_empty_section(self):
        from_doc = Document(TODAY)
        to_doc = Document(YESTERDAY)
        count = bulk_move_items(from_doc, "tomorrow", to_doc, "today")
        assert count == 0

    def test_same_file_move(self):
        """Move within the same document."""
        doc = Document(YESTERDAY)
        count = bulk_move_items(doc, "today", doc, "tonight", unchecked_only=True)
        assert count == 2
        today_sec = doc.find_section("today")
        assert today_sec is not None
        assert len(doc.get_items(today_sec)) == 1  # only checked remains
        tonight_sec = doc.find_section("tonight")
        assert tonight_sec is not None
        assert len(doc.get_items(tonight_sec)) == 3  # 1 original + 2 moved


# -- Tag operations on prose lines --


class TestTagsOnProseLines:
    def test_add_tag_to_prose_by_match(self):
        doc = Document(TAGGED_SAMPLE)
        sec = doc.find_section("notes")
        assert sec is not None
        assert doc.add_tag(sec, "favorite", match="culture-esque")
        results = doc.find_tagged("favorite")
        assert len(results) == 1
        assert "culture-esque" in results[0][2]

    def test_remove_tag_from_prose_by_match(self):
        doc = Document(TAGGED_SAMPLE)
        sec = doc.find_section("notes")
        assert sec is not None
        assert doc.remove_tag(sec, "storyidea", match="culture-esque")
        line = [ln for ln in doc.lines if "culture-esque" in ln][0]
        assert "#storyidea" not in line

    def test_add_tag_prose_not_found(self):
        doc = Document(TAGGED_SAMPLE)
        sec = doc.find_section("notes")
        assert sec is not None
        assert not doc.add_tag(sec, "x", match="nonexistent prose")


# -- Round-trip fidelity --


class TestRoundTrip:
    def test_no_op_preserves_bytes(self):
        """Loading and immediately serializing should be identical."""
        doc = Document(SAMPLE)
        assert str(doc) == SAMPLE

    def test_file_round_trip(self, tmp_path):
        """Write to file, read back, content should match."""
        p = tmp_path / "test.md"
        p.write_text(SAMPLE)
        doc = Document.from_file(p)
        doc.save(p)
        assert p.read_text() == SAMPLE
