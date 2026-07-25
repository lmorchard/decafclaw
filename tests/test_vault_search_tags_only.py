"""Tests for vault_search's tags-only mode (#669).

The mode — empty ``query`` + non-empty ``tags`` routing to
``_tag_filter_search`` — was implemented and documented by #318 but was
uninvokable: ``query`` had no default and the schema marked it ``required``.
The schema simultaneously told the model, in the ``tags`` description, that an
empty query was the way to run a pure tag filter, so the two halves
contradicted each other and a compliant tags-only call raised ``TypeError``.
"""

import pytest

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

    `vault_search(query="")` was always callable and has always returned every
    page (see `test_empty_tags_leaves_behavior_unchanged` in
    tests/test_vault_tools.py, which deliberately asserts that). This fix only
    makes the argument omissible; it deliberately does NOT alter what an
    unconstrained search returns. That unbounded-dump behavior is pre-existing
    and tracked separately — see the follow-up referenced in the PR.
    """
    omitted = await tool_vault_search(ctx)
    explicit = await tool_vault_search(ctx, query="")
    a = omitted if isinstance(omitted, str) else omitted.text
    b = explicit if isinstance(explicit, str) else explicit.text
    # Compare page-name presence rather than raw text — the payload embeds
    # mtimes, which can tick between the two calls.
    for page in ("storage-notes", "compute-notes"):
        assert (page in a) == (page in b)


# -- schema must not contradict the signature --


def test_query_not_required_in_schema():
    """`required: ['query']` is what made the documented mode unreachable."""
    schema = _vault_search_schema()
    assert "query" not in schema["parameters"].get("required", [])


def test_schema_steers_away_from_the_unconstrained_call():
    """The `query` description must warn against a bare, filter-less call.

    Making `query` omissible also makes a fully-unconstrained call easy to
    emit, and that lists the whole vault rather than searching (pre-existing
    behavior — see #673). We don't change the behavior here, so the schema has
    to steer the model instead: descriptions are the control surface.
    """
    desc = _vault_search_schema()["parameters"]["properties"]["query"]["description"]
    assert "vault_list" in desc
    lowered = desc.lower()
    assert "every page" in lowered or "lists every" in lowered


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
