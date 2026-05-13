"""Live smoke test for the kindle contrib skill.

Run from inside the worktree:
    uv run python contrib/skills/kindle/smoke.py [step]

Steps (run progressively; default `all` runs each in order, stopping on first failure):
    cookies           — Verify cookies file exists + parses; show count.
    list              — Run kindle_list_books against real Amazon; show count + first 3 entries.
    fetch <asin>      — Run kindle_fetch_highlights for one ASIN; show count + first 2 entries.
    sync <asin>       — Run kindle_sync_book end-to-end; write a real vault page.
    sync-all          — Run kindle_sync_all (WARNING: takes ~N×60s for N books).
    enabled-gate      — Test scheduled gate short-circuits when enabled=False.
    all               — cookies → list → (no sync; manual review).

Exits non-zero on first failure. Does not touch Mattermost / web UI; talks directly
to the tool functions.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

from decafclaw.config import load_config

# Load tools.py from the colocated path via importlib (same as production loader).
_THIS_DIR = Path(__file__).parent
_tools_spec = importlib.util.spec_from_file_location(
    "decafclaw_contrib_kindle_tools_smoke", _THIS_DIR / "tools.py"
)
assert _tools_spec is not None and _tools_spec.loader is not None
_tools = importlib.util.module_from_spec(_tools_spec)
sys.modules["decafclaw_contrib_kindle_tools_smoke"] = _tools
_tools_spec.loader.exec_module(_tools)

SkillConfig = _tools.SkillConfig
init = _tools.init
kindle_list_books = _tools.kindle_list_books
kindle_fetch_highlights = _tools.kindle_fetch_highlights
kindle_sync_book = _tools.kindle_sync_book
kindle_sync_all = _tools.kindle_sync_all
_resolve_cookies_path = _tools._resolve_cookies_path
_load_cookie_jar = _tools._load_cookie_jar
_cookie_file_age_days = _tools._cookie_file_age_days


def _print(label: str, value: object) -> None:
    print(f"  {label}: {value}")


async def step_cookies(config, skill_config: SkillConfig) -> None:
    print("\n=== step: cookies ===")
    path = _resolve_cookies_path(config, skill_config)
    _print("path", path)
    if not path.is_file():
        print(f"  FAIL: cookies file not found at {path}")
        sys.exit(1)
    jar = _load_cookie_jar(path)
    _print("cookie count", len(jar))
    _print("age (days)", round(_cookie_file_age_days(path), 1))
    names = sorted({c.name for c in jar})
    _print("names", ", ".join(names[:10]) + (f" ... +{len(names) - 10} more" if len(names) > 10 else ""))
    expected = {"at-main", "sess-at-main"}
    missing = expected - set(names)
    if missing:
        print(f"  WARN: expected cookies missing: {missing}")
    else:
        print("  OK: at-main + sess-at-main present")


async def step_list(ctx) -> list:
    print("\n=== step: list ===")
    result = await kindle_list_books(ctx)
    print(f"  text: {result.text}")
    if result.text.startswith("[error:"):
        print("  FAIL: kindle_list_books returned error")
        sys.exit(1)
    books = (result.data or {}).get("books", []) if result.data else []
    _print("count", len(books))
    for i, b in enumerate(books[:3]):
        _print(f"  [{i}]", f"{b.get('asin')} — {b.get('title')[:60]} ({b.get('author')[:30]})")
    return books


async def step_fetch(ctx, asin: str) -> None:
    print(f"\n=== step: fetch {asin} ===")
    result = await kindle_fetch_highlights(ctx, asin)
    print(f"  text: {result.text}")
    if result.text.startswith("[error:"):
        print("  FAIL: kindle_fetch_highlights returned error")
        sys.exit(1)
    highlights = (result.data or {}).get("highlights", []) if result.data else []
    _print("count", len(highlights))
    for i, h in enumerate(highlights[:2]):
        text_preview = h.get("text", "")[:80]
        _print(f"  [{i}]", f"{h.get('location')} · {h.get('color')} · {text_preview}...")


async def step_sync(ctx, asin: str) -> None:
    print(f"\n=== step: sync {asin} ===")
    result = await kindle_sync_book(ctx, asin=asin)
    print(f"  text: {result.text}")
    if result.text.startswith("[error:"):
        print("  FAIL: kindle_sync_book returned error")
        sys.exit(1)
    print(f"  data: {result.data}")


async def step_sync_all(ctx) -> None:
    print("\n=== step: sync-all ===")
    print("  WARNING: this rate-limits at 60s/book by default. Tune via skills.kindle.sync_min_interval_seconds.")
    result = await kindle_sync_all(ctx)
    print(f"  text: {result.text}")
    if result.text.startswith("[error:"):
        print("  FAIL: kindle_sync_all returned error")
        sys.exit(1)
    print(f"  data: {result.data}")


async def step_enabled_gate(config) -> None:
    print("\n=== step: enabled-gate ===")
    # Re-init with enabled=False and simulate scheduled mode
    disabled_config = SkillConfig(enabled=False)
    init(config, disabled_config)

    # Build a minimal stand-in ctx with task_mode="scheduled".
    class FakeCtx:
        config = None
        task_mode = "scheduled"
    fake = FakeCtx()
    fake.config = config

    result = await kindle_sync_all(fake)
    _print("text", result.text)
    if "disabled" not in result.text.lower():
        print("  FAIL: scheduled+disabled run did NOT short-circuit")
        sys.exit(1)
    print("  OK: scheduled+disabled short-circuited")


class _SmokeCtx:
    """Minimal ctx for direct tool invocation outside the agent loop."""

    def __init__(self, config) -> None:
        self.config = config
        self.task_mode = "interactive"
        # vault_write needs an event_bus to publish on; stub it.
        self.event_bus = _NullEventBus()
        self.channel_name = ""
        self.channel_id = ""
        self.thread_id = ""


class _NullEventBus:
    async def publish(self, *args, **kwargs):
        pass


async def main() -> None:
    args = sys.argv[1:]
    step = args[0] if args else "all"

    config = load_config()
    skill_config = SkillConfig()
    init(config, skill_config)
    ctx = _SmokeCtx(config)

    if step in ("cookies", "all"):
        await step_cookies(config, skill_config)

    books: list = []
    if step in ("list", "all"):
        books = await step_list(ctx)

    if step == "fetch":
        if len(args) < 2:
            print("usage: smoke.py fetch <asin>")
            sys.exit(2)
        await step_fetch(ctx, args[1])

    if step == "sync":
        if len(args) < 2:
            print("usage: smoke.py sync <asin>")
            sys.exit(2)
        await step_sync(ctx, args[1])

    if step == "sync-all":
        await step_sync_all(ctx)

    if step == "enabled-gate":
        await step_enabled_gate(config)

    if step == "all":
        print("\n=== smoke OK ===")
        print("Next: pick an ASIN from the list above and run:")
        print("  uv run python contrib/skills/kindle/smoke.py fetch <ASIN>")
        print("  uv run python contrib/skills/kindle/smoke.py sync <ASIN>")


if __name__ == "__main__":
    asyncio.run(main())
