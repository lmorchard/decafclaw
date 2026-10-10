"""Shared secret-file protection rule for the admin file tools (issue #1013).

The six ``admin_*`` tools live under ``config.agent_path`` and can otherwise
read files that hold secrets (web/browser tokens, provider API keys, MCP
credentials, per-VM keys, ...). This module holds the **one** rule those tools
consult, so a new secret file needs a change here (or in config), not a separate
fix in each tool.

Three tiers:

  1. **Refuse** — whole files/directories that *are* a secret store. The agent
     gets nothing. Driven by a small, concrete default set of globs plus the
     admin's ``secret_policy.refuse_paths``.
  2. **Redact** — configuration values where reading is an intended use. The
     agent sees the structure and every non-secret field, with secret leaf
     values replaced by :data:`REDACTED`. Driven by the existing ``secret``
     field annotation (the same convention ``config show --reveal`` uses), plus
     fixed structural rules for the raw-dict sections an annotation cannot reach
     (``env``, ``skills``, MCP ``env``/``headers``), plus the admin's
     ``secret_policy.redact_paths``.
  3. **Extend** — the admin's additions to either tier, so a gap the defaults
     can't anticipate is closed in config, not code.

This is *harm reduction, not watertightness*: a best-effort, admin-extensible
bar, leaning on administrator judgement. It deliberately does **not** build a
pattern-matcher over key names — chasing the name-universe is the rot class that
#731 warns about. The annotation is authoritative: it is the config author
declaring a field secret at the point they add it.

The module imports only the standard library. The ``Config`` class is obtained
from the config *instance* at call time (``type(config)``), so there is no
import into ``decafclaw.config`` and therefore no risk of an import cycle with
the tool layer.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import json
from dataclasses import fields
from typing import Any, get_args, get_origin, get_type_hints

# Fixed marker substituted for a redacted value, and refused in any write
# payload (a read->modify->write of the marker would otherwise clobber a real
# secret with the placeholder).
REDACTED = "<redacted>"

# The only admin files where redaction applies — these hold *known* config
# values, as opposed to arbitrary files. Every other admin file reads raw.
REDACTABLE_FILES = frozenset({"config.json", "mcp_servers.json"})

# Default refuse set — small and concrete, not a name-universe. Each entry is a
# glob relative to config.agent_path where `*` and `?` cross `/` (fnmatch
# semantics): patterns can over-match but are paired with is_refused's
# directory handling so they never under-match for refusal (fail-safe).
DEFAULT_REFUSE_PATTERNS: tuple[str, ...] = (
    "web_tokens.json",  # web UI auth tokens (one grants a full session)
    "browser_tokens.json",  # browser extension bearer tokens (#1008)
    "mcp_oauth/**",  # MCP OAuth tokens (#36) — the store dir and everything under it
    "*.pem",  # PEM private keys
    "*.key",  # key files (per-VM SSH private keys live in a keys/ dir)
    "**/keys/*",  # any file under a keys/ directory (per-VM SSH keys, #956)
    "service_account*.json",  # Vertex service-account key files (ProviderConfig.service_account_file is a path ref, not this file)
)

# Whole-subtree redact rules for raw-dict sections the annotation cannot reach
# (dict[str, X] with no per-key field). Fixed and bounded. A single `*` segment
# means "any one key/element"; these are applied on top of the annotation set.
STRUCTURAL_REDACT_PATTERNS: tuple[str, ...] = (
    "env.*",  # every value in the config.json env section
    "skills.*.*",  # every skill-config value (credentials often live here)
    "mcpServers.*.env.*",  # MCP stdio-server env tokens
    "mcpServers.*.headers.*",  # MCP http-server auth headers
)


def refusal_message(path: str) -> str:
    """The ToolResult error text returned when a path is refused."""
    return f"[error: path '{path}' is a protected secret and is not available to admin tools]"


def marker_message(path: str) -> str:
    """The ToolResult error text returned when a write payload holds the marker.

    Includes the phrase ``redaction marker`` so callers (and tests) can tell
    this apart from the secret-path refusal message.
    """
    return (
        f"[error: refusing to write '{path}': content contains the redaction marker "
        f"'{REDACTED}' — writing the marker would clobber a real secret value]"
    )


def marker_in(content: str | None) -> bool:
    """True if the write payload contains the redaction marker."""
    return content is not None and REDACTED in content


def _normalize_rel(path: str) -> list[str]:
    """Split an agent-root-relative path into non-empty, non-'.' segments."""
    return [seg for seg in str(path).replace("\\", "/").strip("/").split("/") if seg not in ("", ".")]


def refuse_patterns(config: Any) -> tuple[str, ...]:
    """The full refuse glob set: defaults plus the admin's additions."""
    admin = getattr(getattr(config, "secret_policy", None), "refuse_paths", None) or []
    return tuple(DEFAULT_REFUSE_PATTERNS) + tuple(admin)


def _refuse_match(pattern: str, candidate: str) -> bool:
    """True if `candidate` (a slash-relative path) is covered by `pattern`.

    Beyond plain fnmatch, a pattern that names a *store directory* (ending in
    ``/*`` or ``/**``, e.g. ``mcp_oauth/**`` or ``**/keys/*``) is treated as
    refusing the directory itself so the agent cannot list/descend into it.
    """
    if fnmatch.fnmatch(candidate, pattern):
        return True
    base = pattern
    for suffix in ("/**", "/*"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    if base != pattern and fnmatch.fnmatch(candidate, base):
        return True
    return False


def is_refused(rel_path: str, config: Any) -> bool:
    """True if the agent-root-relative path must be refused by every admin tool.

    Matches the path itself and each of its ancestor prefixes against the
    refuse set, so a pattern covering a store refuses the store, its files, and
    listing into it — fail-safe (over-match is fine for refusal).
    """
    parts = _normalize_rel(rel_path)
    patterns = refuse_patterns(config)

    seen: list[str] = []
    for part in parts:
        seen.append(part)
        candidate = "/".join(seen)
        for pattern in patterns:
            if _refuse_match(pattern, candidate):
                return True
    return False


def _is_secret_field(cls: type, field: dataclasses.Field) -> bool:
    return bool(field.metadata.get("secret"))


def _walk_annotation(dc: type, prefix: str) -> set[str]:
    """Dotted paths of every ``secret``-annotated field below ``dc``.

    Nested dataclasses recurse with a growing prefix; ``dict[str, SomeDataclass]``
    fields (e.g. ``providers``) expand to ``prefix.*.<secret field>``. This is a
    literal reuse of the existing ``secret`` annotation — no key-name matching.
    """
    out: set[str] = set()
    try:
        hints = get_type_hints(dc)
    except Exception:  # noqa: BLE001 - a field we can't resolve can't be a secret path
        hints = {}
    for f in fields(dc):
        seg = f"{prefix}.{f.name}" if prefix else f.name
        if _is_secret_field(dc, f):
            out.add(seg)
            continue
        t = hints.get(f.name)
        if t is None:
            continue
        origin = get_origin(t)
        if origin is dict:
            args = [a for a in get_args(t) if a is not type(None)]
            if len(args) == 2 and isinstance(args[1], type) and hasattr(args[1], "__dataclass_fields__"):
                out |= _walk_annotation(args[1], f"{seg}.*")
        elif isinstance(t, type) and hasattr(t, "__dataclass_fields__"):
            out |= _walk_annotation(t, seg)
    return out


def annotation_secret_paths(config: Any) -> set[str]:
    """The annotation-derived redact paths for the loaded Config dataclass tree."""
    cls = type(config) if config is not None else None
    if not (isinstance(cls, type) and hasattr(cls, "__dataclass_fields__")):
        return set()
    return _walk_annotation(cls, "")


def redaction_patterns(config: Any) -> tuple[str, ...]:
    """Every redact path pattern: annotation-derived + structural + admin additions."""
    admin = getattr(getattr(config, "secret_policy", None), "redact_paths", None) or []
    combined = set(annotation_secret_paths(config)) | set(STRUCTURAL_REDACT_PATTERNS) | set(admin)
    return tuple(sorted(combined))


def is_redactable(rel_path: str) -> bool:
    """True if the admin file (by its exact agent-root-relative path) is redactable."""
    return "/".join(_normalize_rel(rel_path)) in REDACTABLE_FILES


def _path_matches(pattern: str, segments: list[str]) -> bool:
    """Match a dotted pattern (single-``*`` segments) against concrete path segments."""
    pseg = pattern.split(".")
    if len(pseg) != len(segments):
        return False
    for pat, seg in zip(pseg, segments):
        if pat != "*" and pat != seg:
            return False
    return True


def redact_json(value: Any, patterns: tuple[str, ...]) -> tuple[Any, list[str]]:
    """Return (redacted_value, redacted_dotted_paths).

    Every leaf whose dotted path matches a pattern is replaced by :data:`REDACTED`.
    The returned path list holds the *concrete* dotted paths that were redacted
    (wildcards resolved to their actual keys), for the ``data`` envelope.
    """
    redacted_paths: list[str] = []
    pattern_list = list(patterns)

    def _leaf_segments(segments: list[str]) -> bool:
        return any(_path_matches(p, segments) for p in pattern_list)

    def walk(node: Any, segments: list[str]) -> Any:
        if isinstance(node, dict):
            out: dict = {}
            for key, v in node.items():
                segs = segments + [str(key)]
                if isinstance(v, (dict, list)):
                    out[key] = walk(v, segs)
                else:
                    if _leaf_segments(segs):
                        out[key] = REDACTED
                        redacted_paths.append(".".join(segs))
                    else:
                        out[key] = v
            return out
        if isinstance(node, list):
            return [walk(v, segments + [str(i)]) if isinstance(v, (dict, list)) else v for i, v in enumerate(node)]
        return node

    redacted = walk(value, [])
    # De-duplicate while preserving order (a pattern could match the same leaf once only,
    # but admin patterns may overlap annotation patterns).
    seen: set[str] = set()
    unique = []
    for p in redacted_paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return redacted, unique


def redact_text(text: str, config: Any) -> tuple[str, list[str]] | None:
    """Parse a redactable file's JSON, redact its secrets, and return (new_text, paths).

    Returns ``None`` if the JSON fails to parse — the caller must refuse
    (fail-closed), since it can't guarantee no leak.
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    patterns = redaction_patterns(config)
    redacted, paths = redact_json(data, patterns)
    return json.dumps(redacted, indent=2) + "\n", paths
