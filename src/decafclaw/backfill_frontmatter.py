"""One-time CLI to backfill YAML frontmatter on existing vault pages (#197).

Pages written before frontmatter generation (dream consolidation, #197 Phase
2) have no `summary` / `keywords` / `tags` / `importance`. This walks the
vault, generates the missing fields via a forced-tool LLM call, and merges
them in without clobbering anything a human already set by hand. Resumable:
a page whose frontmatter already has all four fields is skipped, so a
partial or interrupted run can be re-run safely.

Does not reindex embeddings itself — run `make reindex` afterward so
composite embeddings (frontmatter.build_composite_text) pick up the new
frontmatter.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from decafclaw.frontmatter import parse_frontmatter, serialize_frontmatter
from decafclaw.llm import call_llm
from decafclaw.skills.vault.tools import merge_frontmatter

log = logging.getLogger(__name__)

FRONTMATTER_FIELDS = ("summary", "keywords", "tags", "importance")

_TOOL_NAME = "submit_frontmatter"
_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "One or two sentence summary of the page's content.",
        },
        "keywords": {
            "type": "array",
            "items": {"type": "string"},
            "description": "5-10 keywords or short phrases capturing the page's topics.",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "2-5 short topical tags (single words or short hyphenated phrases).",
        },
        "importance": {
            "type": "number",
            "description": "How important this page is to remember, from 0.0 (trivial) to 1.0 (critical).",
        },
    },
    "required": list(FRONTMATTER_FIELDS),
}


def _iter_frontmatter_candidates(config):
    """Yield vault page paths eligible for frontmatter backfill.

    Mirrors the walk in `embeddings._iter_vault_pages` (all vault `.md`
    files except journal entries) but yields `Path` objects instead of
    embedding text, since backfill needs to read and rewrite each page's
    frontmatter in place.
    """
    vault = config.vault_root
    if not vault.is_dir():
        return
    journal_dir = config.vault_agent_journal_dir
    for path in sorted(vault.rglob("*.md")):
        try:
            if path.resolve().is_relative_to(journal_dir.resolve()):
                continue
        except (ValueError, OSError):
            pass
        yield path


def _has_all_fields(metadata: dict) -> bool:
    """True if every field backfill would populate is already set and non-empty."""
    return all(metadata.get(field) not in (None, "", []) for field in FRONTMATTER_FIELDS)


async def generate_fields_for_page(config, path: Path) -> dict:
    """Forced-tool structured-output LLM call generating frontmatter fields.

    Sophie-style: a single tool the model must call, with "you MUST call
    this" framing and one retry on narrate-stall. Mirrors the pattern in
    `workflow/llm.py::call_structured`, which needs a `ctx`; this is
    config-only so the CLI can run outside an agent turn.
    """
    _, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    system = (
        "You generate vault-page frontmatter metadata — a short summary, "
        "keywords, tags, and an importance score — from a page's content."
    )
    base_user = f"Page content:\n\n{body}"
    tools = [{
        "type": "function",
        "function": {
            "name": _TOOL_NAME,
            "description": (
                "Submit frontmatter metadata for this page. "
                "You MUST call this — do not respond with prose."
            ),
            "parameters": _SCHEMA,
        },
    }]
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": base_user},
    ]
    last_error: str | None = None
    for attempt in range(2):
        response = await call_llm(
            config, messages, tools=tools, model_name=config.default_model,
        )
        tool_calls = response.get("tool_calls") or []
        if tool_calls:
            args_raw = tool_calls[0].get("function", {}).get("arguments") or "{}"
            try:
                return json.loads(args_raw)
            except json.JSONDecodeError as e:
                last_error = f"invalid JSON in tool args: {e}; raw={args_raw[:200]!r}"
        else:
            last_error = (
                f"model emitted text instead of calling {_TOOL_NAME!r}: "
                f"{(response.get('content') or '')[:200]!r}"
            )
        log.debug(
            "generate_fields_for_page(%s) attempt %d failed: %s",
            path, attempt, last_error,
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": (
                base_user + f"\n\nIMPORTANT: You MUST call the tool "
                f"`{_TOOL_NAME}` now. Do not narrate. Emit only the call."
            )},
        ]
    raise RuntimeError(f"structured frontmatter call failed for {path}: {last_error}")


async def run_backfill(config, *, dry_run: bool = False, limit: int | None = None) -> list[dict]:
    """Backfill frontmatter on vault pages missing summary/keywords/tags/importance.

    Returns one result dict per page examined: `{path, action, fields}`
    where `action` is `"filled"`, `"planned"` (dry_run), or `"skipped"`
    (already complete). `limit`, if given, caps how many pages get an LLM
    call in this run — already-complete pages are skipped for free and
    don't count against it, so a capped run stays resumable.
    """
    results: list[dict] = []
    processed = 0
    for path in _iter_frontmatter_candidates(config):
        content = path.read_text(encoding="utf-8")
        metadata, body = parse_frontmatter(content)
        rel = path.relative_to(config.vault_root)

        if _has_all_fields(metadata):
            log.info("Skipping %s (frontmatter already complete)", rel)
            results.append({"path": str(rel), "action": "skipped", "fields": {}})
            continue

        if limit is not None and processed >= limit:
            break

        log.info("Generating frontmatter for %s", rel)
        fields = await generate_fields_for_page(config, path)
        merged = merge_frontmatter(metadata, fields, overwrite=False)
        changed = {
            field: merged[field] for field in FRONTMATTER_FIELDS
            if merged.get(field) != metadata.get(field)
        }
        processed += 1

        if dry_run:
            log.info("Would update %s: %s", rel, ", ".join(changed) or "(no changes)")
            results.append({"path": str(rel), "action": "planned", "fields": changed})
            continue

        path.write_text(serialize_frontmatter(merged, body), encoding="utf-8")
        log.info("Updated %s: %s", rel, ", ".join(changed) or "(no changes)")
        results.append({"path": str(rel), "action": "filled", "fields": changed})

    return results


def main() -> None:
    """CLI entry point: backfill frontmatter on existing vault pages that lack it."""
    from .config import load_config

    parser = argparse.ArgumentParser(
        description=(
            "One-time backfill of vault-page frontmatter (summary/keywords/"
            "tags/importance) for pages written before frontmatter "
            "generation existed. Safe to re-run — pages already fully "
            "populated are skipped. Run `make reindex` afterward so "
            "composite embeddings pick up the new frontmatter."
        )
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show planned changes without writing",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Generate frontmatter for at most N pages this run",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    config = load_config()
    results = asyncio.run(run_backfill(config, dry_run=args.dry_run, limit=args.limit))

    filled = sum(1 for r in results if r["action"] == "filled")
    planned = sum(1 for r in results if r["action"] == "planned")
    skipped = sum(1 for r in results if r["action"] == "skipped")
    print(
        f"{len(results)} page(s) examined: "
        f"{filled} filled, {planned} planned, {skipped} skipped"
    )
    if not args.dry_run and filled:
        print("Run `make reindex` to refresh composite embeddings.")


if __name__ == "__main__":
    main()
