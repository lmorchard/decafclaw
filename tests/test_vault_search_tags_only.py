"""Tests for vault_search's tags-only mode (#669).

The mode — empty ``query`` + non-empty ``tags`` routing to
``_tag_filter_search`` — was implemented and documented by #318 but was
uninvokable: ``query`` had no default and the schema marked it ``required``.
The schema simultaneously told the model, in the ``tags`` description, that an
empty query was the way to run a pure tag filter, so the two halves
contradicted each other and a compliant tags-only call raised ``TypeError``.
"""

import json
from unittest.mock import patch

import pytest

from decafclaw.media import ToolResult
from decafclaw.skills.vault.tools import TOOL_DEFINITIONS, tool_vault_search


def _vault_search_schema():
    for d in TOOL_DEFINITIONS:
        fn = d.get("function", d)
        if fn.get("name") == "vault_search":
            return fn
    raise AssertionError("vault_search not found in TOOL_DEFINITIONS")


@pytest.fixture
def tagged_pages(config):
    """Two pages whose prose is identical apart from their tags.

    Mirrors evals/vault-tags.yaml: only the frontmatter `tags:` field
    distinguishes them, so a result that picks the right one proves the tag
    filter ran rather than a substring/semantic match.
    """
    d = config.vault_agent_pages_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / "storage-notes.md").write_text(
        "---\ntags: [rust]\n---\n# Storage Notes\n\n"
        "We compared Rust and Python here. The chosen runtime is Zephyr.\n"
    )
    (d / "compute-notes.md").write_text(
        "---\ntags: [python]\n---\n# Compute Notes\n\n"
        "We compared Rust and Python here. The chosen runtime is Nimbus.\n"
    )
    return d


# -- the mode must be callable at all --


@pytest.mark.asyncio
async def test_tags_only_call_omitting_query(ctx, tagged_pages):
    """`vault_search(tags=[...])` with no `query` must work.

    Before the fix this raised:
        TypeError: tool_vault_search() missing 1 required positional
        argument: 'query'
    """
    result = await tool_vault_search(ctx, tags=["rust"])
    text = result if isinstance(result, str) else result.text
    assert "storage-notes" in text
    assert "compute-notes" not in text


@pytest.mark.asyncio
async def test_tags_only_explicit_empty_query(ctx, tagged_pages):
    """Passing `query=""` explicitly takes the same path."""
    result = await tool_vault_search(ctx, query="", tags=["python"])
    text = result if isinstance(result, str) else result.text
    assert "compute-notes" in text
    assert "storage-notes" not in text


@pytest.mark.asyncio
async def test_tags_only_leading_hash_normalized(ctx, tagged_pages):
    """Tags normalize, so '#rust' and 'rust' behave identically."""
    result = await tool_vault_search(ctx, tags=["#rust"])
    text = result if isinstance(result, str) else result.text
    assert "storage-notes" in text


@pytest.mark.asyncio
async def test_tags_only_any_tag_matches_either(ctx, tagged_pages):
    """`any_tag=True` ORs the tag list instead of ANDing it."""
    result = await tool_vault_search(ctx, tags=["rust", "python"], any_tag=True)
    text = result if isinstance(result, str) else result.text
    assert "storage-notes" in text
    assert "compute-notes" in text


@pytest.mark.asyncio
async def test_tags_only_all_tags_required_by_default(ctx, tagged_pages):
    """Default AND semantics: no page carries both tags, so none match."""
    result = await tool_vault_search(ctx, tags=["rust", "python"])
    text = result if isinstance(result, str) else result.text
    assert "storage-notes" not in text
    assert "compute-notes" not in text


@pytest.mark.asyncio
async def test_omitting_query_matches_passing_empty_string(ctx, tagged_pages):
    """Omitting `query` is equivalent to passing `""` — that's the whole change.

    #669 made the argument omissible without altering what an unconstrained
    search returns; #673 then changed that return from a whole-vault dump to a
    refusal naming `vault_list`. This test is indifferent to which of the two
    it is, and that's the point: whatever an unconstrained call does, both
    spellings must do the same thing.
    """
    omitted = await tool_vault_search(ctx)
    explicit = await tool_vault_search(ctx, query="")
    a = omitted if isinstance(omitted, str) else omitted.text
    b = explicit if isinstance(explicit, str) else explicit.text
    # Compare page-name presence rather than raw text — the payload embeds
    # mtimes, which can tick between the two calls.
    for page in ("storage-notes", "compute-notes"):
        assert (page in a) == (page in b)
    # Stem-presence alone goes vacuous under #673: both sides refuse, both
    # are stem-free, and every comparison above degrades to False == False —
    # the test would stop discriminating exactly when the behavior it guards
    # changes. Comparing refusal-ness too keeps at least one axis live under
    # either behavior, while staying indifferent to which one applies.
    assert ("[error:" in a) == ("[error:" in b)


# -- an unconstrained call must refuse rather than dump the vault (#673) --


@pytest.mark.asyncio
async def test_unconstrained_search_refuses_and_names_vault_list(
    ctx, tagged_pages
):
    """`vault_search()` with no query and no filters must refuse (#673).

    Making `query` omissible (#669) also made a fully-unconstrained call
    trivial to emit, and that call used to enumerate the entire vault — a
    listing dressed up as a search, blowing context for no signal. This
    grades three separable things, all load-bearing:

    (a) the result is an error-shaped `ToolResult` (`[error: ...]`, the house
        convention) that names `vault_list`, so the model is redirected to
        the tool that actually does enumeration;
    (b) no vault page appears in the result — neither in `text` nor in
        `data`. The refusal must replace the dump, not merely preface it.
        Asserting only (a) would go green on a format-string edit that still
        lists every page underneath;
    (c) `pages_with_tags` is never called — the refusal short-circuits ahead
        of any filter path, so an empty tag set can't vacuously match
        everything on the way out.
    """
    with patch(
        "decafclaw.skills.vault.tools.pages_with_tags"
    ) as mock_pages_with_tags:
        result = await tool_vault_search(ctx)

    assert isinstance(result, ToolResult)
    # (a) error-shaped, and it names the tool the model should use instead.
    assert "[error:" in result.text
    assert "vault_list" in result.text
    # (b) the vault is NOT enumerated. `tagged_pages` really is on disk, so
    # these stems would show up in the old dump.
    #
    # `data` counts as much as `text` does: execute_single_tool appends
    # `json.dumps(result.data)` to the model-visible tool message as a fenced
    # JSON block (tool_execution.py), and today's dump carries the page list
    # in `data["results"]` as well as in `text`. Swapping the text for a
    # refusal while leaving `data` intact would still ship every page path to
    # the model, so assert over the serialized payload too.
    serialized = json.dumps(result.data or {})
    for stem in ("storage-notes", "compute-notes"):
        assert stem not in result.text
        assert stem not in serialized
    # (c) no filter path ran at all.
    mock_pages_with_tags.assert_not_called()


# -- the refusal must not swallow constrained empty-query searches (#673) --
#
# C1 refuses an empty query only when NOTHING else narrows the call. The
# cheapest over-broad implementation — `if not query and not req_tags:
# refuse` — greens C1 while breaking `folder` / `days` / `source_type`
# searches, which are legitimate empty-query modes. Every other empty-query
# call in the suite passes `tags` or nothing, so without these three the
# boundary has no fence on its non-tag side.


@pytest.mark.asyncio
async def test_empty_query_with_folder_is_not_refused(ctx, tagged_pages):
    """`folder` alone is enough of a constraint — scoped listing is valid."""
    result = await tool_vault_search(ctx, "", folder="agent/pages")
    text = result if isinstance(result, str) else result.text
    assert "[error:" not in text
    assert "storage-notes" in text
    assert "compute-notes" in text


@pytest.mark.asyncio
async def test_empty_query_with_days_is_not_refused(ctx, tagged_pages):
    """`days` alone is enough of a constraint — "what changed lately".

    The fixture writes both pages immediately, so a 1-day window contains
    them and a non-empty result proves the call ran rather than refused.
    """
    result = await tool_vault_search(ctx, "", days=1)
    text = result if isinstance(result, str) else result.text
    assert "[error:" not in text
    assert "storage-notes" in text
    assert "compute-notes" in text


@pytest.mark.asyncio
async def test_empty_query_with_source_type_is_not_refused(ctx, tagged_pages):
    """`source_type` alone must not trip the refusal."""
    result = await tool_vault_search(ctx, "", source_type="page")
    text = result if isinstance(result, str) else result.text
    assert "[error:" not in text


@pytest.mark.asyncio
async def test_source_type_filters_on_substring_strategy(ctx, config):
    """vault_search with source_type='page' excludes journal entries on substring search."""
    pages_dir = config.vault_agent_pages_dir
    journal_dir = config.vault_agent_journal_dir
    pages_dir.mkdir(parents=True, exist_ok=True)
    journal_dir.mkdir(parents=True, exist_ok=True)

    (pages_dir / "page-file.md").write_text("Hello unique_substring_abc")
    (journal_dir / "journal-file.md").write_text("Hello unique_substring_abc")

    # Force substring strategy
    config.embedding.search_strategy = "substring"

    result = await tool_vault_search(ctx, "unique_substring_abc", source_type="page")
    text = result.text if hasattr(result, "text") else str(result)
    assert "page-file" in text
    assert "journal-file" not in text



# -- schema must not contradict the signature --


def test_query_not_required_in_schema():
    """`required: ['query']` is what made the documented mode unreachable."""
    schema = _vault_search_schema()
    assert "query" not in schema["parameters"].get("required", [])


def test_schema_steers_away_from_the_unconstrained_call():
    """The `query` description must warn against a bare, filter-less call.

    Making `query` omissible also makes a fully-unconstrained call easy to
    emit. #673 made that call refuse outright, but the schema steer still
    earns its keep: descriptions are the control surface, and steering the
    model away from a call it would otherwise have to be told off for is
    cheaper than the refusal round-trip. This guards the steer against being
    dropped as redundant once the behavior backs it up.
    """
    desc = _vault_search_schema()["parameters"]["properties"]["query"]["description"]
    assert "vault_list" in desc
    lowered = desc.lower()
    assert "every page" in lowered or "lists every" in lowered
    # The actual steer is the imperative ("Do NOT omit it with no other
    # filter"), not the two substrings above — a rewording like "omit with
    # `tags` for a pure tag filter; `vault_list` enumerates every page"
    # satisfies both of them with the prohibition deleted outright. Require a
    # prohibitive token so the steer can't be softened into a description.
    assert "do not" in lowered or "don't" in lowered or "never" in lowered


def test_schema_documents_the_empty_query_mode():
    """Some param description must tell the model the mode exists.

    The `tags` description already carried this before the fix; asserting it
    here keeps the guidance from being dropped in a future edit, since the
    signature default alone makes the mode possible but not discoverable.
    """
    props = _vault_search_schema()["parameters"]["properties"]
    blob = (props["query"]["description"] + " " + props["tags"]["description"]).lower()
    assert "empty" in blob
    assert "tag filter" in blob or "tags only" in blob
