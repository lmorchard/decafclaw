"""Whole-request image + serialized-payload budget (#1143).

Many individually-acceptable images can collectively exceed a provider's
image-count or request-body limit; a token estimate or per-image cap does not
cover that failure. This module clamps the assembled multimodal request before
submission, provider-aware and deterministic.

It runs on the **outbound request view** — the message list after
:func:`decafclaw.attachments.resolve_attachments` has inlined images as base64
data-URL parts — and never mutates archive entries or the pre-resolve originals.
It returns a new message list (only messages whose images were evicted are
copied; the rest pass through by reference) plus an :class:`ImageBudgetReport`
for context diagnostics.

Dual clamp, in order: (1) image count, then (2) total serialized request bytes.
Only **user-role** messages carry images today (assistant/tool content is
string-typed), so the budget counts and evicts ``image_url`` parts there only.
Eviction is oldest-first; the newest message's images are protected while they
fit the byte room. Mirrors oh-my-pi's ``provider-image-budget`` (64 KB request
envelope, count-then-byte clamp, assistant images excluded as model outputs).

See docs/context-composer.md and docs/file-attachments.md.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

# model_catalog imports only config_types + llm.types (both cycle-free and
# lightweight), so a module-level import is safe.
from .model_catalog import effective_default_model

log = logging.getLogger(__name__)

# Body bytes added after the clamp sees the context: injected reminders, the
# provider's own system prompt, and request fields (model, limits, thinking,
# stream). Reserved before protecting images against a body limit. Mirrors
# oh-my-pi's REQUEST_ENVELOPE_BYTES.
ENVELOPE_RESERVATION_BYTES = 64 * 1024


@dataclass
class ImageBudgetReport:
    """Outcome of :func:`apply_image_budget`, for context diagnostics.

    The limits echo the caps actually applied (``None`` = that dimension was
    unenforced for this provider type). ``inline_bytes`` is the base64 + data-URL
    size of the images that were kept; ``other_bytes`` is the serialized size of
    the non-image content (text + tool schemas); ``envelope_bytes`` is the fixed
    reservation for post-clamp request fields.
    """

    provider_type: str | None = None
    max_images: int | None = None  # count cap used (None = unenforced)
    max_request_bytes: int | None = None  # byte cap used (None = unenforced)
    vision_unsupported: bool = False  # True when supports_vision=False forced notices
    images_included: int = 0  # image parts actually serialized to the provider
    images_omitted: int = 0  # image parts replaced by omission notices
    inline_bytes: int = 0  # bytes of the included images (base64 + data-URL header)
    other_bytes: int = 0  # serialized bytes of non-image content (text + tools)
    envelope_bytes: int = ENVELOPE_RESERVATION_BYTES  # reserved for post-clamp fields


def _is_image_part(part) -> bool:
    return isinstance(part, dict) and part.get("type") == "image_url"


def _image_part_bytes(part: dict) -> int:
    """Serialized byte size of an image part (base64 is ASCII, so chars == bytes)."""
    url = part.get("image_url", {}).get("url", "")
    return len(url) if isinstance(url, str) else 0


def _user_image_messages(llm_history: list[dict]):
    """Yield ``(message_index, [part_indices])`` for user messages with images.

    Only user-role messages carry images today, so the budget operates there
    only. Messages are yielded in list order (oldest first); part indices are in
    content order. Assistant/tool image parts (model outputs, never replayed as
    inputs) are deliberately ignored — counting them would inflate the drop
    budget and evict user images the provider was happy to accept.
    """
    for i, m in enumerate(llm_history):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue
        part_indices = [j for j, p in enumerate(content) if _is_image_part(p)]
        if part_indices:
            yield i, part_indices


def _resolve(config, model_name: str):
    """Resolve provider type + limits + ``supports_vision`` for ``model_name``.

    Reads the config's model/provider tables directly (no import of
    :mod:`decafclaw.config`, so no cycle). Fails open: an empty or unresolvable
    model (unknown name, missing provider) yields no enforcement rather than
    breaking the turn. Returns ``(provider_type, max_images, max_request_bytes,
    supports_vision)`` with ``None`` for any unresolvable dimension.
    """
    name = model_name or ""
    if not name:
        try:
            name = effective_default_model(config) or ""
        except Exception as exc:  # fail-open on any resolution surprise
            log.debug("image_budget: default-model resolution failed: %s", exc)
            name = ""

    if not name or name not in config.model_configs:
        return None, None, None, None

    mc = config.model_configs[name]
    if mc.provider not in config.providers:
        return None, None, None, None

    provider_type = config.providers[mc.provider].type or None
    supports_vision = getattr(mc, "supports_vision", None)
    limits = config.image_budget.resolved(provider_type)
    return provider_type, limits.max_images, limits.max_request_bytes, supports_vision


def _omission_notice(
    originals: list[dict] | None, msg_index: int, image_ordinal: int = 0, image_part_count: int | None = None
) -> str:
    """Omission-notice text for an evicted image.

    Attributes the pre-resolve attachment filename to this part: the k-th image
    part in a message maps to the k-th ``image/*`` attachment (the order
    :func:`decafclaw.attachments.resolve_attachments` inlines them). A uniform
    fallback is used when no filename can be attributed (no ``originals``, an
    unattributable ordinal, or a missing-file shift). The filename lets the
    model — and a user — target a reread of exactly what was dropped.

    ``image_part_count`` is the number of image parts actually present in the
    resolved message. The k-th part maps to the k-th ``image/*`` attachment only
    when every image attachment inlined: a file missing from the workspace
    yields a text placeholder, not an image part, which shifts the mapping. A
    count mismatch means naming a file would misattribute, so fall back to the
    uniform notice.
    """
    if originals is not None and 0 <= msg_index < len(originals):
        atts = originals[msg_index].get("attachments") or []
        image_names = [a.get("filename") for a in atts if (a.get("mime_type") or "").startswith("image/")]
        if image_part_count is not None and len(image_names) != image_part_count:
            return "[image omitted: provider image limit]"
        if 0 <= image_ordinal < len(image_names) and image_names[image_ordinal]:
            return f"[image omitted: provider image limit ({image_names[image_ordinal]})]"
    return "[image omitted: provider image limit]"


def _other_serialized_bytes(llm_history: list[dict], tools: list[dict] | None) -> int:
    """Serialized UTF-8 byte size of the request's non-image content.

    JSON-serializes the messages with image parts removed (text + structure)
    plus any tool definitions. Base64 image bytes are excluded here — they are
    accounted separately against the byte budget. Non-ASCII text is counted in
    UTF-8 bytes (``ensure_ascii=False`` + encode). This is a provider-agnostic
    estimate of the OpenAI-format body; the fixed envelope reservation absorbs
    each provider's structural differences (see module docstring).
    """
    stripped = []
    for m in llm_history:
        content = m.get("content") if isinstance(m, dict) else None
        if isinstance(content, list):
            m2 = dict(m)
            m2["content"] = [p for p in content if not _is_image_part(p)]
            stripped.append(m2)
        else:
            stripped.append(m)
    body = {"messages": stripped}
    if tools:
        body["tools"] = tools
    return len(json.dumps(body, ensure_ascii=False).encode("utf-8"))


def apply_image_budget(
    config,
    model_name: str,
    llm_history: list[dict],
    originals: list[dict] | None = None,
    tools: list[dict] | None = None,
) -> tuple[list[dict], ImageBudgetReport]:
    """Clamp the outbound request's images to the provider's count/byte limits.

    Args:
        config: The agent :class:`~decafclaw.config.Config` (supplies the
            ``image_budget`` limits and the model/provider tables).
        model_name: The active model name (a ``model_configs`` key); empty falls
            back to the effective default. Resolved fresh on every call, so a
            provider/config change between calls uses the new limits.
        llm_history: The message list **after** ``resolve_attachments`` (the
            outbound request view). Image parts in user-role messages are the
            clamp target. Never mutated.
        originals: The message list **before** ``resolve_attachments`` (same
            order as ``llm_history``), used to attribute filenames to omission
            notices. Optional; a uniform fallback is used without it.
        tools: Tool definitions sent with the request, counted toward the byte
            budget's non-image bytes. Optional (side-question paths send none).

    Returns:
        ``(new_llm_history, report)``. ``new_llm_history`` is the input list
        unchanged (same object) when nothing was evicted, else a new list whose
        evicted image parts are replaced by omission-notice text parts.
    """
    provider_type, max_images, max_request_bytes, supports_vision = _resolve(config, model_name)

    img_msgs = list(_user_image_messages(llm_history))
    total_images = sum(len(p) for _, p in img_msgs)

    evict_by_msg: dict[int, set[int]] = {}

    def mark(msg_index: int, part_index: int) -> None:
        evict_by_msg.setdefault(msg_index, set()).add(part_index)

    other_bytes = 0  # measured only when the byte dimension is enforced

    if supports_vision is False:
        # The model cannot see images: every image part becomes a notice. No
        # budget math — sending pixels to a non-vision model is pure waste.
        for mi, pidxs in img_msgs:
            for pi in pidxs:
                mark(mi, pi)
    else:
        # -- Count clamp (oldest-first, part-level) --
        if max_images is not None and total_images > max_images:
            remaining = total_images - max_images
            for mi, pidxs in img_msgs:  # list order = oldest first
                if remaining <= 0:
                    break
                for pi in pidxs:
                    if remaining <= 0:
                        break
                    mark(mi, pi)
                    remaining -= 1

        # -- Byte clamp (protect newest while they fit; evict oldest-first) --
        if max_request_bytes is not None:
            other_bytes = _other_serialized_bytes(llm_history, tools)
            image_room = max_request_bytes - other_bytes - ENVELOPE_RESERVATION_BYTES
            if image_room <= 0:
                # Non-image content alone fills the body; no image can fit.
                for mi, pidxs in img_msgs:
                    for pi in pidxs:
                        mark(mi, pi)
            else:
                kept = 0
                for mi, pidxs in reversed(img_msgs):  # newest first
                    already = evict_by_msg.get(mi, set())
                    surviving = [pi for pi in pidxs if pi not in already]
                    msg_bytes = sum(_image_part_bytes(llm_history[mi]["content"][pi]) for pi in surviving)
                    if msg_bytes == 0:
                        continue
                    if kept + msg_bytes <= image_room:
                        kept += msg_bytes  # keep this message's images
                    else:
                        for pi in surviving:
                            mark(mi, pi)

    # -- Apply evictions (copy only affected messages; never mutate originals) --
    new_history = _apply_evictions(llm_history, originals, evict_by_msg)

    # -- Report --
    included = 0
    inline_bytes = 0
    for mi, pidxs in img_msgs:
        dropped = evict_by_msg.get(mi, set())
        for pi in pidxs:
            if pi in dropped:
                continue
            included += 1
            inline_bytes += _image_part_bytes(llm_history[mi]["content"][pi])

    report = ImageBudgetReport(
        provider_type=provider_type,
        max_images=max_images,
        max_request_bytes=max_request_bytes,
        vision_unsupported=supports_vision is False,
        images_included=included,
        images_omitted=total_images - included,
        inline_bytes=inline_bytes,
        other_bytes=other_bytes,  # measured in the byte clamp above (0 when unenforced)
        envelope_bytes=ENVELOPE_RESERVATION_BYTES if max_request_bytes is not None else 0,
    )

    if report.images_omitted:
        log.info(
            "image_budget[%s]: omitted %d/%d images (%d inline bytes kept of room; other=%dB)",
            provider_type or "?",
            report.images_omitted,
            total_images,
            inline_bytes,
            report.other_bytes,
        )

    return new_history, report


def _apply_evictions(
    llm_history: list[dict],
    originals: list[dict] | None,
    evict_by_msg: dict[int, set[int]],
) -> list[dict]:
    """Return a new message list with evicted image parts replaced by notices.

    Messages without evictions pass through by reference (identity preserved, so
    unchanged turns keep their prompt-cache prefix). A message with any eviction
    is copied with each evicted image part replaced by an omission-notice text
    part, in place. The input list and its messages are never mutated.
    """
    if not evict_by_msg:
        return llm_history

    new = []
    for i, m in enumerate(llm_history):
        part_set = evict_by_msg.get(i)
        content = m.get("content") if isinstance(m, dict) else None
        if not part_set or not isinstance(content, list):
            new.append(m)
            continue
        # Map each content index to its ordinal among the message's image parts,
        # so a notice names the specific attachment that was dropped. The part
        # count guards filename attribution against missing-file shifts (see
        # _omission_notice).
        ordinal = 0
        image_part_count = sum(1 for p in content if _is_image_part(p))
        new_content = []
        for j, p in enumerate(content):
            if _is_image_part(p):
                if j in part_set:
                    new_content.append(
                        {"type": "text", "text": _omission_notice(originals, i, ordinal, image_part_count)}
                    )
                else:
                    new_content.append(p)
                ordinal += 1
            else:
                new_content.append(p)
        m2 = dict(m)
        m2["content"] = new_content
        new.append(m2)
    return new
