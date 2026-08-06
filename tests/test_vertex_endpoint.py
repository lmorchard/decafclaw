"""Tests for Vertex endpoint URL construction."""

from decafclaw.llm.providers.vertex import VertexProvider


def test_regional_endpoint_uses_region_prefixed_host():
    p = VertexProvider(project="proj", region="us-central1")
    assert p._base_url("gemini-2.5-flash") == (
        "https://us-central1-aiplatform.googleapis.com/v1/"
        "projects/proj/locations/us-central1/"
        "publishers/google/models/gemini-2.5-flash"
    )


def test_global_region_uses_unprefixed_host():
    """`global` is not a region prefix — there is no global-aiplatform host.

    Google serves the global endpoint from the bare aiplatform.googleapis.com
    with `locations/global` in the path. Prefixing produced a DNS-level 404 for
    every model, and the Gemini 3.x models are global-only, so they are
    unreachable without this.
    """
    p = VertexProvider(project="proj", region="global")
    assert p._base_url("gemini-3.1-pro-preview") == (
        "https://aiplatform.googleapis.com/v1/"
        "projects/proj/locations/global/"
        "publishers/google/models/gemini-3.1-pro-preview"
    )
