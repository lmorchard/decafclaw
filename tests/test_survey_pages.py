"""Tests for survey page retrieval and calibration (#1738)."""

import pytest

from decafclaw.survey.pages import (
    EmbeddingRetriever,
    LocalVaultRetriever,
    PageCase,
    calibrate_pages,
    connected_page_keys,
    create_retriever,
    page_set,
    set_jaccard,
)


class StubRetriever:
    name = "stub"

    def __init__(self, mapping):
        self.mapping = mapping

    async def pages_for(self, text):
        return tuple(self.mapping.get(text, ()))


def _write_vault(config, relpath, content):
    path = config.vault_root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


async def test_calibrate_pages_with_provenance_and_proxy():
    retriever = StubRetriever(
        {
            "X": ("agent/pages/A.md", "agent/pages/C.md"),
            "prev": ("agent/pages/A.md", "agent/pages/D.md"),
            "asst": ("agent/pages/B.md",),
            "Y": ("agent/pages/E.md", "agent/pages/F.md"),
        }
    )
    cases = [
        PageCase(
            turn_id="t1",
            actual_next="X",
            previous_user="prev",
            assistant_before="asst",
            provenance_pages=("agent/pages/A.md", "agent/pages/B.md"),
        ),
        PageCase(
            turn_id="t2",
            actual_next="Y",
            previous_user="unmapped",
            assistant_before="unmapped",
        ),
    ]
    summary, details = await calibrate_pages(cases, retriever, top_k=2, connected=["A", "B", "Z"])
    assert summary["truth_sources"] == {"provenance": 1, "retrieval_proxy": 1}
    assert summary["truth_sources"]["provenance"] == 1
    assert details["t1"]["truth_source"] == "provenance"
    assert details["t1"]["reuse_previous_user"] == pytest.approx(1 / 3, abs=1e-3)
    assert details["t1"]["reuse_last_assistant"] == pytest.approx(1 / 2, abs=1e-3)
    assert details["t1"]["retrieval_of_actual"] == pytest.approx(1 / 3, abs=1e-3)
    assert details["t1"]["global_connected"] == pytest.approx(1.0)
    assert details["t2"]["truth_source"] == "retrieval_proxy"
    assert details["t2"]["retrieval_of_actual"] == pytest.approx(1.0)
    assert summary["baselines"]["retrieval_of_actual"]["n"] == len(cases)


async def test_calibrate_pages_counts_empty_truth():
    retriever = StubRetriever({})
    summary, details = await calibrate_pages(
        [PageCase(turn_id="t", actual_next="", previous_user="", assistant_before="")],
        retriever,
        top_k=5,
        connected=[],
    )
    assert summary["empty_ground_truth"] == 1
    assert details["t"]["truth_pages"] == []


def test_connected_page_keys_counts_inbound_links(config):
    _write_vault(config, "agent/pages/Linked.md", "plain page")
    _write_vault(config, "agent/pages/Source.md", "See [[Linked]] and [[Source]].")
    _write_vault(config, "agent/pages/Other.md", "See [[Source]] too.")
    _write_vault(config, "agent/pages/Third.md", "See [[Source]] again.")
    ranked = connected_page_keys(config)
    assert ranked[0] == "source"
    assert set(ranked) >= {"linked", "source", "other"}


def test_page_set_and_jaccard_normalize_paths():
    left = page_set(["agent/pages/A.md", "B"])
    right = page_set(["A", "user/C.md"])
    assert left == {"a", "b"}
    assert set_jaccard(left, right) == pytest.approx(1 / 3, abs=1e-3)


async def test_embedding_retriever_maps_and_dedupes(config, monkeypatch):
    async def fake_embed(_config, text):
        return [1.0, 0.0]

    def fake_search(_config, vector, top_k=5, source_type=None):
        return [
            {"file_path": "agent/pages/A.md"},
            {"file_path": "agent/pages/A.md"},
            {"file_path": "user/B.md"},
        ]

    monkeypatch.setattr("decafclaw.embeddings.embed_text", fake_embed)
    monkeypatch.setattr("decafclaw.embeddings.search_similar_sync", fake_search)
    retriever = EmbeddingRetriever(config, top_k=2)
    assert await retriever.pages_for("anything") == ("a", "b")


async def test_local_retriever_cosine_with_fake_fastembed(config, monkeypatch):
    _write_vault(config, "agent/pages/Alpha.md", "alpha alpha")
    _write_vault(config, "agent/pages/Beta.md", "beta beta")

    async def fake_embed_batch(self, model, texts, **kwargs):
        return [[float(text.count("alpha")), float(text.count("beta"))] for text in texts]

    monkeypatch.setattr(
        "decafclaw.llm.providers.fastembed.FastembedProvider.embed_batch",
        fake_embed_batch,
    )
    retriever = LocalVaultRetriever(config, top_k=1)
    assert await retriever.pages_for("alpha") == ("alpha",)
    assert await retriever.pages_for("beta") == ("beta",)


async def test_create_retriever_falls_back_to_local(config, monkeypatch):
    async def no_embedding(_config, _text):
        return None

    monkeypatch.setattr("decafclaw.embeddings.embed_text", no_embedding)
    retriever = await create_retriever(config, backend="auto", top_k=5)
    assert isinstance(retriever, LocalVaultRetriever)


async def test_create_retriever_explicit_embeddings_raises(config, monkeypatch):
    async def no_embedding(_config, _text):
        return None

    monkeypatch.setattr("decafclaw.embeddings.embed_text", no_embedding)
    with pytest.raises(RuntimeError):
        await create_retriever(config, backend="embeddings", top_k=5)
