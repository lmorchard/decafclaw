#!/usr/bin/env python3
"""Migrate literal secret values in the agent config.json to ``${VAR}`` refs (#1032).

The settled design (issue #1032): secret values live OUT of
``data/{agent_id}/config.json`` and in an external ``.env`` store (outside the
agent dir). ``config.json`` therefore holds ``${VAR_NAME}`` references, which
``decafclaw.secrets`` resolves at config-load time.

This tool inspects the agent ``config.json`` for literal secret values in
secret-annotated fields and:

  * appends ``VAR=value`` to the external ``.env`` store (skipped when the var is
    already present — idempotent), and
  * rewrites the config.json literal to ``${VAR}`` (or moves an ``env``-section
    literal into the store, since env-held secrets are config-file literals too).

Non-destructive and idempotent: fields already holding a ``${VAR}`` ref, empty
values, and vars already present in the store are left alone. ``--dry-run``
previews without writing.

Usage:
  python scripts/migrate_secrets.py [--dry-run] [--env-file PATH]
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

# Add project root to path (mirror migrate_to_vault.py / migrate_sidecars_to_dirs.py)
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from decafclaw.secrets import is_secret_ref  # noqa: E402


def _lookup(raw: dict, path: str) -> Any:
    """Walk a dotted path (``a.b.c``) through the raw JSON dict. Missing → None."""
    node: Any = raw
    for seg in [s for s in str(path).split(".") if s]:
        if isinstance(node, dict):
            node = node.get(seg)
        else:
            return None
    return node


def _var_name_for(path: str) -> str:
    """Dotted config path → external env var name.

    ``llm.api_key`` → ``LLM_API_KEY``; ``providers.openai.api_key`` →
    ``OPENAI_API_KEY``; ``env.OPENAI_API_KEY`` → ``OPENAI_API_KEY``.
    """
    segs = [s for s in str(path).split(".") if s]
    if not segs:
        return ""
    if segs[0] == "env":
        return segs[1].upper() if len(segs) > 1 else segs[0].upper()
    if len(segs) >= 3 and segs[0] == "providers":
        # providers.<name>.<secret field> → <NAME>_<FIELD>
        return f"{segs[-2].upper()}_{segs[-1].upper()}"
    return "_".join(s.upper() for s in segs)


def _set_path(node: dict, path: str, value: Any) -> None:
    """Set a dotted path in a nested dict, creating intermediates as needed."""
    segs = [s for s in str(path).split(".") if s]
    if not segs:
        return
    if segs[0] == "env":
        # ``env.<KEY>`` stores its value directly under the env key.
        section = node.setdefault("env", {})
        if not isinstance(section, dict):
            return
        section[segs[1]] = value
        return
    target = node
    for seg in segs[:-1]:
        nxt = target.get(seg)
        if not isinstance(nxt, dict):
            nxt = {}
            target[seg] = nxt
        target = nxt
    target[segs[-1]] = value


def _unset_env_key(node: dict, key: str) -> None:
    """Drop an ``env.<KEY>`` literal after moving it into the external store."""
    section = node.get("env")
    if isinstance(section, dict) and key in section:
        del section[key]


def collect_secret_literals(raw: dict, secret_paths: Iterable[str]) -> list[tuple[str, str]]:
    """Return ``(dotted_path, literal)`` for secret-masked fields holding literals."""
    candidates: set[str] = set()
    for path in secret_paths:
        candidates.add(str(path))
    env_section = raw.get("env")
    if isinstance(env_section, dict):
        for key in env_section:
            candidates.add(f"env.{key}")
    found: list[tuple[str, str]] = []
    for path in sorted(candidates):
        value = _lookup(raw, path)
        if not isinstance(value, str):
            continue
        literal = value.strip()
        if not literal or is_secret_ref(literal):
            continue
        found.append((path, literal))
    return found


def _read_env_store(env_path: Path) -> dict:
    """Parse a simple ``KEY=value`` env file into a dict (comments/blanks skipped)."""
    store: dict = {}
    if not env_path.exists():
        return store
    for line in env_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key:
            store[key] = value
    return store


def _write_env_store(env_path: Path, store: dict) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in sorted(store.items())]
    env_path.write_text("\n".join(lines) + "\n")


def migrate_secrets(
    config_path: Path,
    env_path: Path,
    *,
    dry_run: bool,
    secret_paths: Iterable[str],
) -> list[dict]:
    """Migrate literal secrets in the agent config.json to ``${VAR}`` refs.

    Writes the external env store and rewrites the config file unless
    ``dry_run`` is true. Returns the migrated entries.
    """
    raw: dict = {}
    if config_path.exists():
        raw = json.loads(config_path.read_text())
    literals = collect_secret_literals(raw, secret_paths)
    migrated: list[dict] = []
    store = _read_env_store(env_path)
    for path, literal in literals:
        var = _var_name_for(path)
        ref = f"${{{var}}}"
        staged = store.get(var)
        if staged is not None and staged != literal:
            # Already migrated under a different value; leave the ref alone.
            continue
        if staged is not None and staged == literal:
            # Value already staged in the external store; just rewrite the ref.
            if not dry_run:
                _set_path(raw, path, ref)
            migrated.append({"path": path, "literal": literal, "var": var, "ref": ref, "action": "ref"})
            continue
        if not dry_run:
            store[var] = literal
            _write_env_store(env_path, store)
            if path.startswith("env."):
                _unset_env_key(raw, _var_name_for(path))
            else:
                _set_path(raw, path, ref)
        migrated.append({"path": path, "literal": literal, "var": var, "ref": ref, "action": "migrate"})
    if not dry_run and migrated:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(raw, indent=2) + "\n")
    return migrated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate literal secrets in config.json to ${VAR} refs backed by an external .env store"
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    parser.add_argument("--env-file", default=None, help="External .env store path (default: <data_home>/.env)")
    args = parser.parse_args()

    from decafclaw.config import load_config
    from decafclaw.secret_policy import annotation_secret_paths

    config = load_config()
    config_path = config.agent_path / "config.json"
    env_path = Path(args.env_file) if args.env_file else config.agent_path.parent / ".env"

    print(f"Config: {config_path}")
    print(f"Secret store: {env_path}")
    print(f"Dry run: {args.dry_run}")
    print()

    migrated = migrate_secrets(
        config_path,
        env_path,
        dry_run=args.dry_run,
        secret_paths=annotation_secret_paths(config),
    )
    for entry in migrated:
        verb = "WOULD MIGRATE" if args.dry_run else "MIGRATED"
        print(f"  {verb}: {entry['path']} literal -> {entry['ref']} (env {entry['var']}={entry['literal']})")
    if args.dry_run:
        print(f"\n{len(migrated)} secret(s) would be migrated. Re-run without --dry-run to apply.")
    else:
        print(f"\n{len(migrated)} secret(s) migrated.")


if __name__ == "__main__":
    main()
