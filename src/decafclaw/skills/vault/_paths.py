"""Vault path helpers — page/folder resolution and path-safety.

Extracted from ``tools.py`` (#1366). Pure path/name manipulation: resolve a
page or folder to a vault-contained file path, sanitize page names for
cross-platform filesystems, and reject inputs that would escape the vault.

No dependency on the confirmation gate, tool results, or event machinery —
those stay in ``tools.py``.
"""

import re
from pathlib import Path

from decafclaw.util import find_fuzzy_matches


def _vault_root(config) -> Path:
    return config.vault_root


def _agent_dir(config) -> Path:
    return config.vault_agent_dir


def resolve_page(config, page: str, from_page: str | None = None) -> Path | None:
    """Resolve a page name to a file path within the vault.

    Resolution order:
    1. Exact path match (relative to vault root)
    2. Search all subdirectories for stem match, preferring closest to from_page
    Returns None if the page doesn't exist or the name is invalid.
    """
    if ".." in page or page.startswith("/"):
        return None
    # Strip .md suffix if the caller included it (prevents Foo.md.md)
    if page.endswith(".md"):
        page = page[:-3]
    vault = _vault_root(config).resolve()
    if not vault.is_dir():
        return None

    # Try direct path first
    direct = (vault / f"{page}.md").resolve()
    if direct.is_relative_to(vault) and direct.exists():
        return direct

    # Search subdirectories by comparing stems
    matches: list[Path] = []
    # The page param might be a bare name or a partial path — match on stem
    target_stem = Path(page).stem
    for path in vault.rglob("*.md"):
        if path.stem == target_stem and path.resolve().is_relative_to(vault):
            matches.append(path)

    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    # Multiple matches — sort by path for deterministic results,
    # then prefer closest to from_page's directory if provided
    matches.sort(key=lambda p: str(p))
    if from_page:
        from_dir = (vault / from_page).parent

        def _distance(p: Path) -> int:
            """Count path components between from_dir and p's parent."""
            try:
                rel = p.parent.relative_to(from_dir)
                return len(rel.parts)
            except ValueError:
                try:
                    rel = from_dir.relative_to(p.parent)
                    return len(rel.parts)
                except ValueError:
                    return 999

        matches.sort(key=_distance)

    return matches[0]


def suggest_pages(config, page: str) -> list[str]:
    """Return up to three existing page names close to a missing ``page``.

    A bare name is compared with page stems; a name with a folder is compared
    with vault-relative paths. Suggestions are vault-relative paths without
    ``.md``, the form ``vault_read`` accepts. This never resolves the page.
    """
    if ".." in page or page.startswith("/"):
        return []
    if page.endswith(".md"):
        page = page[:-3]
    vault = _vault_root(config).resolve()
    if not vault.is_dir():
        return []

    # Map each comparison key to the page names it stands for. Several
    # pages in different folders can share a stem.
    by_key: dict[str, list[str]] = {}
    match_on_stem = "/" not in page
    for path in sorted(vault.rglob("*.md")):
        name = path.relative_to(vault).with_suffix("").as_posix()
        key = path.stem if match_on_stem else name
        by_key.setdefault(key, []).append(name)

    target = Path(page).stem if match_on_stem else page
    suggestions = [name for key in find_fuzzy_matches(target, by_key) for name in by_key[key]]
    return suggestions[:3]


def sanitize_vault_page_name(page: str) -> str:
    """Sanitize a vault page path for cross-platform compatibility (Android/Windows).

    Replaces colons and pipes with spaced dashes ('-') and removes characters
    forbidden on Android / Windows filesystems (: * ? " < > | \\).
    """
    if not isinstance(page, str) or not page.strip():
        return page
    has_md = page.endswith(".md")
    raw = page[:-3] if has_md else page

    parts = raw.split("/")
    cleaned = []
    for part in parts:
        if not part:
            cleaned.append(part)
            continue
        # Replace colons and pipes with spaced dashes
        s = re.sub(r"\s*[:|]\s*", " - ", part)
        # Strip illegal characters and control chars
        s = re.sub(r"[\x00-\x1f?*\"<>\\^]", "", s)
        # Collapse whitespace
        s = re.sub(r"\s+", " ", s).strip()
        # Windows / Android forbid trailing dots or spaces in path segments
        s = s.rstrip(". ")
        cleaned.append(s)

    result = "/".join(cleaned)
    if has_md:
        result += ".md"
    return result


def _safe_write_path(config, page: str, sanitize: bool = False) -> Path | None:
    """Validate and return a safe write path within the vault root.

    Returns None for invalid names (empty, trailing slash, path traversal)
    or paths that would escape the vault directory. When sanitize=True,
    cleans illegal characters (: * ? " < > | \\) in page names.
    """
    if not isinstance(page, str):
        return None
    page = page.strip()
    if not page:
        return None
    if sanitize:
        page = sanitize_vault_page_name(page)
    if ".." in page or page.startswith("/") or page.endswith("/"):
        return None
    # Strip .md suffix if the caller included it (prevents Foo.md.md)
    if page.endswith(".md"):
        page = page[:-3]
    # After stripping, the final path component must still be a real name —
    # reject inputs like "" / "foo/" / ".md" that would resolve to a hidden
    # ".md" file with an empty stem.
    if not Path(page).name:
        return None
    vault = _vault_root(config).resolve()
    path = (vault / f"{page}.md").resolve()
    if not path.is_relative_to(vault):
        return None
    return path


def _is_in_agent_dir(config, path: Path) -> bool:
    """Check if a path is within the agent's folder."""
    try:
        agent = _agent_dir(config).resolve()
        return path.resolve().is_relative_to(agent)
    except (ValueError, OSError):
        return False


def _safe_folder(config, folder: str) -> Path | None:
    """Validate a folder path within the vault. Returns resolved path or None."""
    if not folder:
        return _vault_root(config)
    if ".." in folder or folder.startswith("/"):
        return None
    vault = _vault_root(config)
    resolved = (vault / folder).resolve()
    if not resolved.is_relative_to(vault.resolve()):
        return None
    return resolved
