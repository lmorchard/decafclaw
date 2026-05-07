"""Per-conversation vault folder grants — sidecar persistence.

Each conversation that has been granted folder trust gets a JSON sidecar at
{workspace}/conversations/{conv_id}.vault_grants.json. Stored as a sorted,
deduped list of folder paths (vault-relative, trailing slash enforced).

Sidecar shape:

    {"schema_version": 1, "folders": ["creative/foo/", ...]}

`schema_version` is written for forward compatibility — readers may key off
it to migrate older shapes. The current reader ignores the field and reads
`folders` directly, so older sidecars (no `schema_version` field) load
unchanged.
"""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _grants_sidecar_path(config, conv_id: str) -> Path:
    """Path to the grants JSON sidecar; guarded against directory traversal.

    Mirrors `_canvas_sidecar_path` in src/decafclaw/canvas.py:41-50.
    """
    base_dir = (config.workspace_path / "conversations").resolve()
    if not conv_id or "/" in conv_id or "\\" in conv_id or ".." in conv_id:
        return base_dir / "_invalid.vault_grants.json"
    path = (base_dir / f"{conv_id}.vault_grants.json").resolve()
    if not path.is_relative_to(base_dir):
        return base_dir / "_invalid.vault_grants.json"
    return path


def normalize_folder(folder: str, *, warn_on_invalid: bool = False) -> str:
    """Normalize a vault-relative folder path.

    Strips leading `/`, enforces trailing `/`, rejects `..` substrings and
    non-string/empty inputs. Returns "" for invalid inputs (caller treats
    as no-match).

    Args:
        folder: The folder path to normalize.
        warn_on_invalid: If True, log a warning when a non-empty entry is
            rejected (used by the allowlist config-load path so users see
            misconfigured entries).
    """
    if not isinstance(folder, str):
        return ""
    f = folder.strip()
    if not f or ".." in f:
        if f and warn_on_invalid:
            log.warning("Skipping invalid vault folder entry: %r", folder)
        return ""
    if f.startswith("/"):
        f = f.lstrip("/")
    if not f.endswith("/"):
        f = f + "/"
    return f


def read_grants(config, conv_id: str) -> set[str]:
    """Read the grant set; fail-open with empty set on any error."""
    if not conv_id:
        return set()
    path = _grants_sidecar_path(config, conv_id)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
        folders = data.get("folders", [])
        # Filter out invalid (normalized to "") entries — they would act as
        # a wildcard prefix in is_path_in_grants and let everything through.
        result: set[str] = set()
        for f in folders:
            if isinstance(f, str):
                norm = normalize_folder(f)
                if norm:
                    result.add(norm)
        return result
    except (json.JSONDecodeError, OSError):
        log.warning("Failed to read vault grants for %s; treating as empty",
                    conv_id, exc_info=True)
        return set()


def add_grant(config, conv_id: str, folder: str) -> bool:
    """Append a folder grant atomically. Returns True on success."""
    if not conv_id:
        return False
    folder = normalize_folder(folder)
    if not folder:
        return False
    grants = read_grants(config, conv_id)
    grants.add(folder)
    path = _grants_sidecar_path(config, conv_id)
    tmp = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 1, "folders": sorted(grants)}
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        tmp.replace(path)
        return True
    except OSError:
        log.warning("Failed to write vault grants for %s", conv_id, exc_info=True)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError as cleanup_exc:
            log.debug("vault_grants tmp cleanup failed: %s", cleanup_exc)
        return False


def is_path_in_grants(config, conv_id: str, path: Path) -> bool:
    """Check if a vault-relative path is under any granted folder."""
    grants = read_grants(config, conv_id)
    if not grants:
        return False
    try:
        rel = str(path.resolve().relative_to(config.vault_root.resolve()))
    except (ValueError, OSError):
        return False
    rel = rel.replace("\\", "/")
    return any(rel.startswith(g) for g in grants)
