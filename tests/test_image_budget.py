"""Tests for the whole-request image + serialized-payload budget (#1143).

Covers all five acceptance criteria:
  AC1 — many small images within limits stay included.
  AC2 — the provider's actual serialized payload shape (base64 + text/tool-schema
        overhead) and count-vs-byte limits binding independently.
  AC3 — deterministic selection (oldest-first, newest protected), vision-
        unsupported, and a single impossible-to-fit request.
  AC4 — originals/archive unchanged; omissions reported in context + diagnostics.
  AC5 — replayed inputs vs non-replayed outputs; fresh limits on provider/config change.
"""

import copy
import json

from decafclaw.config import Config
from decafclaw.config_types import ModelConfig, ProviderConfig
from decafclaw.image_budget import ENVELOPE_RESERVATION_BYTES, apply_image_budget

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _img(b64_len: int, mime: str = "image/png") -> dict:
    """An image_url part whose data-URL payload is exactly ``b64_len`` bytes."""
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64," + "A" * b64_len}}


def _user(text: str, images: list[dict]) -> dict:
    parts = [{"type": "text", "text": text}] + images
    return {"role": "user", "content": parts}


def _vertex_config(**overrides) -> Config:
    """A config whose active model routes to a vertex provider (3,000 / 256 MB)."""
    base = dict(
        providers={"v": ProviderConfig(type="vertex", project="p")},
        model_configs={"m": ModelConfig(provider="v", model="gemini-2.5-flash")},
        default_model="m",
    )
    base.update(overrides)
    return Config(**base)


def _openai_compat_config(**overrides) -> Config:
    base = dict(
        providers={"o": ProviderConfig(type="openai-compat", url="http://x")},
        model_configs={"m": ModelConfig(provider="o", model="gpt-4o")},
        default_model="m",
    )
    base.update(overrides)
    return Config(**base)


def _serialized_openai_compat(messages: list[dict], tools=None, model: str = "gpt-4o") -> int:
    """Actual on-the-wire byte size via the openai-compat serializer."""
    from decafclaw.llm.providers.openai_compat import _clean_messages

    body = {"model": model, "messages": _clean_messages(messages)}
    if tools:
        body["tools"] = tools
    return len(json.dumps(body, ensure_ascii=False).encode("utf-8"))


def _serialized_vertex(messages: list[dict], tools=None) -> int:
    """Actual on-the-wire byte size via the vertex serializer."""
    from decafclaw.llm.providers.vertex import _build_request_body

    body = _build_request_body(messages, tools=tools)
    return len(json.dumps(body, ensure_ascii=False).encode("utf-8"))


def _image_parts(messages: list[dict], role: str = "user") -> int:
    n = 0
    for m in messages:
        if m.get("role") != role or not isinstance(m.get("content"), list):
            continue
        n += sum(1 for p in m["content"] if isinstance(p, dict) and p.get("type") == "image_url")
    return n


# ---------------------------------------------------------------------------
# AC1 — many small images within limits stay included
# ---------------------------------------------------------------------------


def test_ac1_many_small_images_within_limits_all_included():
    """Many individually-small images that fit the provider limits all go out."""
    cfg = _vertex_config()  # vertex: 3,000 images / 256 MB — generous
    hist = [_user(f"msg {i}", [_img(120)]) for i in range(50)]
    new, rep = apply_image_budget(cfg, "m", hist)

    assert rep.images_included == 50
    assert rep.images_omitted == 0
    assert _image_parts(new) == 50
    # Nothing evicted → the input list is returned by reference (identity preserved).
    assert new is hist


def test_ac1_count_limit_generous_no_eviction():
    """A count cap above the image total evicts nothing."""
    cfg = _vertex_config()
    cfg.image_budget.providers["vertex"] = {"max_images": 100}
    hist = [_user("x", [_img(50)]) for _ in range(10)]
    new, rep = apply_image_budget(cfg, "m", hist)
    assert rep.images_included == 10 and rep.images_omitted == 0


# ---------------------------------------------------------------------------
# AC2 — actual serialized payload shape; count-vs-byte limits bind independently
# ---------------------------------------------------------------------------


def test_ac2_count_limit_binds_independently_of_bytes():
    """A tight count cap evicts oldest-first even though bytes are far under limit."""
    cfg = _vertex_config()
    # Count cap of 2; bytes are tiny (well under the 20 MB byte limit), so only
    # the count clamp should fire.
    cfg.image_budget.providers["vertex"] = {"max_images": 2, "max_request_bytes": None}
    hist = [_user(f"m{i}", [_img(10)]) for i in range(5)]
    new, rep = apply_image_budget(cfg, "m", hist)

    assert rep.max_images == 2
    assert rep.images_included == 2 and rep.images_omitted == 3
    # Oldest-first: the two NEWEST messages keep their image; the three oldest are notices.
    assert _image_parts(new) == 2
    # The two kept images are in the last two user messages.
    assert _image_parts([new[-1]]) == 1 and _image_parts([new[-2]]) == 1
    assert _image_parts([new[0]]) == 0 and _image_parts([new[1]]) == 0


def test_ac2_byte_limit_binds_independently_of_count():
    """A tight byte cap evicts by bytes even though the count is under its limit."""
    cfg = _vertex_config()
    s = 100_000  # each image ~100 KB of base64
    # Room for exactly one image (newest) but not two; count cap is generous.
    cfg.image_budget.providers["vertex"] = {
        "max_images": None,  # count unenforced
        "max_request_bytes": ENVELOPE_RESERVATION_BYTES + 2 * s - 1,
    }
    hist = [_user(f"m{i}", [_img(s)]) for i in range(3)]
    new, rep = apply_image_budget(cfg, "m", hist)

    assert rep.max_request_bytes == ENVELOPE_RESERVATION_BYTES + 2 * s - 1
    assert rep.images_included == 1 and rep.images_omitted == 2
    # Newest protected, two oldest evicted.
    assert _image_parts([new[-1]]) == 1
    assert _image_parts([new[0]]) == 0 and _image_parts([new[1]]) == 0


def test_ac2_serialized_payload_fits_via_openai_compat_serializer():
    """The budgeted request, serialized by the real openai-compat serializer, fits."""
    cfg = _openai_compat_config()
    s = 100_000
    cfg.image_budget.providers["openai-compat"] = {
        "max_images": None,
        "max_request_bytes": ENVELOPE_RESERVATION_BYTES + 2 * s - 1,
    }
    tools = [
        {
            "type": "function",
            "function": {"name": "t", "description": "d" * 200, "parameters": {"type": "object", "properties": {}}},
        }
    ]
    hist = [_user(f"some text {i} " + "w" * 50, [_img(s)]) for i in range(3)]
    new, rep = apply_image_budget(cfg, "m", hist, tools=tools)

    assert rep.images_included == 1 and rep.images_omitted == 2
    # The full request (system + budgeted history + tools) must fit the byte cap.
    system = [{"role": "system", "content": "S" * 2000}]
    actual = _serialized_openai_compat(system + new, tools=tools)
    assert actual <= rep.max_request_bytes


def test_ac2_serialized_payload_fits_via_vertex_serializer():
    """The budgeted request, serialized by the real vertex serializer, fits."""
    cfg = _vertex_config()
    s = 100_000
    cfg.image_budget.providers["vertex"] = {
        "max_images": None,
        "max_request_bytes": ENVELOPE_RESERVATION_BYTES + 2 * s - 1,
    }
    tools = [
        {
            "type": "function",
            "function": {"name": "t", "description": "d" * 200, "parameters": {"type": "object", "properties": {}}},
        }
    ]
    hist = [_user(f"some text {i} " + "w" * 50, [_img(s)]) for i in range(3)]
    new, rep = apply_image_budget(cfg, "m", hist, tools=tools)

    assert rep.images_included == 1 and rep.images_omitted == 2
    system = [{"role": "system", "content": "S" * 2000}]
    actual = _serialized_vertex(system + new, tools=tools)
    assert actual <= rep.max_request_bytes


def test_ac2_base64_and_text_overhead_accounted():
    """Byte accounting includes the data-URL header and non-image text bytes."""
    cfg = _vertex_config()
    header = "data:image/png;base64,"
    s = 10_000
    cfg.image_budget.providers["vertex"] = {
        "max_images": None,
        # Generous cap (envelope + image data-URL + margin for text) so the
        # single image is kept; we assert on how its bytes are accounted.
        "max_request_bytes": ENVELOPE_RESERVATION_BYTES + len(header) + s + 10_000,
    }
    hist = [_user("hello world", [_img(s)])]
    new, rep = apply_image_budget(cfg, "m", hist)

    # The image's data-URL is len(header)+s; inline_bytes must count the full
    # URL (header + base64 payload), not just the payload. other_bytes counts the
    # non-image text separately (and is > 0 because of the "hello world" part).
    assert rep.images_included == 1 and rep.images_omitted == 0
    assert rep.inline_bytes == len(header) + s
    assert rep.other_bytes > 0


# ---------------------------------------------------------------------------
# AC3 — determinism, vision-unsupported, single impossible-to-fit request
# ---------------------------------------------------------------------------


def test_ac3_deterministic_oldest_first_newest_protected():
    """Repeated calls on identical input produce identical, deterministic output."""
    cfg = _vertex_config()
    s = 100_000
    cfg.image_budget.providers["vertex"] = {
        "max_images": None,
        "max_request_bytes": ENVELOPE_RESERVATION_BYTES + 2 * s - 1,
    }
    hist = [_user(f"m{i}", [_img(s)]) for i in range(4)]

    new_a, rep_a = apply_image_budget(cfg, "m", hist)
    new_b, rep_b = apply_image_budget(cfg, "m", hist)

    assert json.dumps(new_a) == json.dumps(new_b)
    assert rep_a.images_included == rep_b.images_included == 1
    # Deterministic: the single kept image is always the newest message's.
    assert _image_parts([new_a[-1]]) == 1 and _image_parts(new_a[:-1]) == 0


def test_ac3_vision_unsupported_all_images_become_notices():
    """supports_vision=False turns every image into a notice, no budget math."""
    cfg = _vertex_config(
        model_configs={"m": ModelConfig(provider="v", model="gpt-4o-audio", supports_vision=False)},
    )
    hist = [_user(f"m{i}", [_img(50_000)]) for i in range(3)]
    new, rep = apply_image_budget(cfg, "m", hist)

    assert rep.vision_unsupported is True
    assert rep.images_included == 0 and rep.images_omitted == 3
    assert _image_parts(new) == 0
    # Each evicted image is replaced by a readable notice text part.
    for m in new:
        assert any(p.get("type") == "text" and p["text"].startswith("[image omitted:") for p in m["content"])


def test_ac3_single_impossible_to_fit_request():
    """A single image larger than the whole byte room becomes a notice (not silently dropped)."""
    cfg = _vertex_config()
    s = 500_000  # one image far larger than the room
    cfg.image_budget.providers["vertex"] = {
        "max_images": None,
        # Room is far smaller than the single image.
        "max_request_bytes": ENVELOPE_RESERVATION_BYTES + 10_000,
    }
    hist = [_user("look at this", [_img(s)])]
    new, rep = apply_image_budget(cfg, "m", hist)

    assert rep.images_included == 0 and rep.images_omitted == 1
    # The image is reported as omitted, with a notice the model can read.
    assert _image_parts(new) == 0
    texts = [p["text"] for p in new[0]["content"] if p.get("type") == "text"]
    assert any(t.startswith("[image omitted:") for t in texts)


# ---------------------------------------------------------------------------
# AC4 — originals/archive unchanged; omissions reported accurately
# ---------------------------------------------------------------------------


def test_ac4_originals_and_archive_unchanged():
    """The pre-resolve originals and the input messages are never mutated."""
    cfg = _vertex_config()
    s = 100_000
    cfg.image_budget.providers["vertex"] = {
        "max_images": None,
        "max_request_bytes": ENVELOPE_RESERVATION_BYTES + 2 * s - 1,
    }

    originals = [
        {"role": "user", "content": f"m{i}", "attachments": [{"filename": f"f{i}.png", "mime_type": "image/png"}]}
        for i in range(3)
    ]
    # Simulate resolve_attachments output: image parts inlined, attachments stripped.
    llm_history = [{"role": "user", "content": [{"type": "text", "text": f"m{i}"}, _img(s)]} for i in range(3)]

    originals_before = copy.deepcopy(originals)
    history_before = copy.deepcopy(llm_history)

    new, rep = apply_image_budget(cfg, "m", llm_history, originals=originals)

    # Originals (archive entries) are byte-for-byte unchanged.
    assert originals == originals_before
    # The input message objects were not mutated (evicted ones are copied, not edited).
    assert llm_history == history_before
    # Omissions reported accurately in the diagnostics report.
    assert rep.images_omitted == 2 and rep.images_included == 1


def test_ac4_omission_notice_names_the_file():
    """An evicted image's notice carries its pre-resolve attachment filename."""
    cfg = _vertex_config()
    s = 100_000
    cfg.image_budget.providers["vertex"] = {
        "max_images": None,
        "max_request_bytes": ENVELOPE_RESERVATION_BYTES + 2 * s - 1,
    }

    originals = [
        {"role": "user", "content": f"m{i}", "attachments": [{"filename": f"photo{i}.png", "mime_type": "image/png"}]}
        for i in range(3)
    ]
    llm_history = [{"role": "user", "content": [{"type": "text", "text": f"m{i}"}, _img(s)]} for i in range(3)]
    new, rep = apply_image_budget(cfg, "m", llm_history, originals=originals)

    # The two oldest (photo0, photo1) are evicted; their notices name the files.
    notice_texts = [
        p["text"]
        for m in new[:2]
        for p in m["content"]
        if p.get("type") == "text" and p["text"].startswith("[image omitted:")
    ]
    assert any("photo0.png" in t for t in notice_texts)
    assert any("photo1.png" in t for t in notice_texts)


def test_ac4_missing_file_shift_falls_back_to_uniform_notice():
    """A missing earlier attachment shifts the part↔attachment mapping; no filename.

    ``resolve_attachments`` emits a text placeholder (not an image part) for a
    file missing from the workspace, so the k-th image part no longer maps to
    the k-th ``image/*`` attachment. Naming a file would misattribute, so the
    notice must use the uniform fallback.
    """
    cfg = _vertex_config()
    s = 100_000
    cfg.image_budget.providers["vertex"] = {
        "max_images": None,
        # Room for exactly one image; this single (larger) one does not fit.
        "max_request_bytes": ENVELOPE_RESERVATION_BYTES + s - 1,
    }

    # Original: two image/* attachments; the first file is missing on disk.
    originals = [
        {
            "role": "user",
            "content": "m0",
            "attachments": [
                {"filename": "gone.png", "mime_type": "image/png"},  # missing → placeholder
                {"filename": "kept.png", "mime_type": "image/png"},  # inlined → the image part
            ],
        }
    ]
    # Resolved view: placeholder text + one image part (the mapping has shifted).
    llm_history = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "m0"},
                {"type": "text", "text": "[attachment missing: gone.png]"},
                _img(s),  # this is kept.png, but it is the 0th image part
            ],
        }
    ]

    new, rep = apply_image_budget(cfg, "m", llm_history, originals=originals)

    assert rep.images_omitted == 1
    texts = [
        p["text"] for p in new[0]["content"] if p.get("type") == "text" and p["text"].startswith("[image omitted:")
    ]
    assert len(texts) == 1
    # Uniform fallback — must not name either file (naming "gone.png" would be
    # wrong: that part was never sent; the mapping cannot be trusted).
    assert texts[0] == "[image omitted: provider image limit]"


def test_ac4_report_fields_populated():
    """The report exposes the limits used and byte accounting for diagnostics."""
    cfg = _vertex_config()
    s = 100_000
    cfg.image_budget.providers["vertex"] = {
        "max_images": None,
        "max_request_bytes": ENVELOPE_RESERVATION_BYTES + 2 * s - 1,
    }
    hist = [_user(f"m{i}", [_img(s)]) for i in range(3)]
    _, rep = apply_image_budget(cfg, "m", hist)

    assert rep.provider_type == "vertex"
    assert rep.max_request_bytes == ENVELOPE_RESERVATION_BYTES + 2 * s - 1
    assert rep.max_images is None  # count unenforced in this scenario
    assert rep.envelope_bytes == ENVELOPE_RESERVATION_BYTES
    assert rep.other_bytes > 0  # text content is accounted
    # Exactly one image kept; its bytes include the data-URL header + payload.
    assert rep.inline_bytes == len("data:image/png;base64,") + s


# ---------------------------------------------------------------------------
# AC5 — replayed inputs vs non-replayed outputs; fresh limits on change
# ---------------------------------------------------------------------------


def test_ac5_assistant_image_part_untouched():
    """A synthetic assistant image part (a model output) is never counted or evicted."""
    cfg = _vertex_config()
    s = 100_000
    # Tight byte cap that would evict a user image if it were counted.
    cfg.image_budget.providers["vertex"] = {
        "max_images": None,
        "max_request_bytes": ENVELOPE_RESERVATION_BYTES + 2 * s - 1,
    }

    hist = [
        _user("q", [_img(s)]),  # user image (replayed input) — clamp target
        {"role": "assistant", "content": [{"type": "text", "text": "here"}, _img(s)]},  # assistant image (output)
    ]
    new, rep = apply_image_budget(cfg, "m", hist)

    # Only the user image is counted; the assistant's is ignored entirely.
    assert rep.images_included + rep.images_omitted == 1
    # The assistant message is passed through untouched (its image part survives).
    assert new[1] is hist[1]
    assert _image_parts([new[1]], role="assistant") == 1


def test_ac5_fresh_limits_on_provider_change():
    """A provider/config change between calls uses the new limits, not stale state."""
    # Same history; two models with very different byte caps.
    s = 100_000
    hist = [_user(f"m{i}", [_img(s)]) for i in range(3)]

    generous = _vertex_config(model_configs={"m": ModelConfig(provider="v", model="gemini-2.5-flash")})
    _, rep_generous = apply_image_budget(generous, "m", hist)

    tight = _vertex_config(model_configs={"m": ModelConfig(provider="v", model="gemini-2.5-flash")})
    tight.image_budget.providers["vertex"] = {
        "max_images": None,
        "max_request_bytes": ENVELOPE_RESERVATION_BYTES + 2 * s - 1,
    }
    _, rep_tight = apply_image_budget(tight, "m", hist)

    # The generous cap keeps everything; the tight cap evicts. No shared/stale state.
    assert rep_generous.images_omitted == 0 and rep_generous.images_included == 3
    assert rep_tight.images_omitted == 2 and rep_tight.images_included == 1


def test_ac5_unconfigured_provider_type_no_enforcement():
    """A provider type with no verified limit (openai-responses) is unenforced."""
    cfg = Config(
        providers={"r": ProviderConfig(type="openai-responses", api_key="k")},
        model_configs={"m": ModelConfig(provider="r", model="gpt-4o")},
        default_model="m",
    )
    hist = [_user(f"m{i}", [_img(1_000_000)]) for i in range(5)]  # huge images
    new, rep = apply_image_budget(cfg, "m", hist)

    assert rep.max_images is None and rep.max_request_bytes is None
    assert rep.images_included == 5 and rep.images_omitted == 0
    assert new is hist


def test_ac5_unresolvable_model_fails_open():
    """An unknown model name fails open (no enforcement) rather than raising."""
    cfg = _vertex_config()  # has model "m" but we ask for a missing one
    hist = [_user("q", [_img(1_000_000)])]
    new, rep = apply_image_budget(cfg, "does-not-exist", hist)

    assert rep.max_images is None and rep.max_request_bytes is None
    assert rep.images_included == 1 and rep.images_omitted == 0
    assert new is hist


# ---------------------------------------------------------------------------
# Config robustness — resolved() must never raise on a turn
# ---------------------------------------------------------------------------


def test_config_env_path_stringified_override_parses():
    """The generic loader's env path stringifies nested values; resolved() parses them back.

    ``IMAGE_BUDGET_PROVIDERS='{"vertex": {"max_images": 10}}'`` arrives as
    ``providers["vertex"] == "{'max_images': 10}"`` (a string) because
    ``_parse_dict`` maps every value through ``str()``.
    """
    from decafclaw.config_types import ImageBudgetConfig

    cfg = ImageBudgetConfig(providers={"vertex": "{'max_images': 10}"})
    limits = cfg.resolved("vertex")

    assert limits.max_images == 10  # parsed from the stringified mapping
    assert limits.max_request_bytes == 256 * 1024 * 1024  # verified default still applies


def test_config_non_numeric_override_disables_dimension():
    """A non-numeric override value disables that dimension rather than raising."""
    from decafclaw.config_types import ImageBudgetConfig

    cfg = ImageBudgetConfig(providers={"vertex": {"max_images": "abc", "max_request_bytes": None}})
    limits = cfg.resolved("vertex")

    assert limits.max_images is None  # "abc" → disabled, no exception
    assert limits.max_request_bytes is None  # explicit null → disabled


def test_config_unparseable_override_ignored():
    """An unparseable per-provider mapping is ignored (verified defaults apply)."""
    from decafclaw.config_types import ImageBudgetConfig

    cfg = ImageBudgetConfig(providers={"vertex": "max_images 10"})  # not a mapping
    limits = cfg.resolved("vertex")

    assert limits.max_images == 3000  # verified default, override ignored
    assert limits.max_request_bytes == 256 * 1024 * 1024


# ---------------------------------------------------------------------------
# Verified provider limits — pinned to the providers' documented values
# ---------------------------------------------------------------------------


def test_verified_defaults_match_provider_docs():
    """Pin the per-provider-type defaults to the documented limits.

    Sources are recorded on ``_VERIFIED_IMAGE_LIMITS`` in ``config_types``:
    OpenAI (512 MB payload / 1,500 images per request) and Vertex AI / Gemini
    (3,000 images and PDFs combined; 256 MB inline part). Changing a number
    here means re-verifying the provider doc first — that is the point of the
    pin.
    """
    from decafclaw.config_types import ImageBudgetConfig

    cfg = ImageBudgetConfig()

    for provider_type in ("openai", "openai-compat"):
        limits = cfg.resolved(provider_type)
        assert limits.max_images == 1500
        assert limits.max_request_bytes == 512 * 1024 * 1024

    vertex = cfg.resolved("vertex")
    assert vertex.max_images == 3000
    assert vertex.max_request_bytes == 256 * 1024 * 1024

    # Provider types without a verified limit stay unenforced.
    for provider_type in ("openai-responses", "litellm", "scripted", None, ""):
        limits = cfg.resolved(provider_type)
        assert limits.max_images is None
        assert limits.max_request_bytes is None
