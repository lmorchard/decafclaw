"""Per-conversation sidecar file paths.

Convention (#576): every per-conversation sidecar lives in a directory
named for the conversation id — conversations/{conv_id}/{file} — e.g.
archive.jsonl, notes.md, decisions.json. This is the only layout.

Convention (#1112): conversations are grouped into sessions. A session is
a parent conversation plus its child conversations; a standalone
conversation is its own single-conversation session. The session-scoped
layout has conversations live under the session root —
sessions/{session_id}/{conv_id}/ — with the standalone case storing the
conversation's sidecars directly in sessions/{conv_id}/.

Instances upgrading from the pre-#1112 flat or per-conversation layouts run
scripts/migrate_sidecars_to_dirs.py (`make migrate-sidecars`) and
scripts/migrate_sessions.py (`make migrate-sessions`) once to move existing
files into the session layout. The legacy conversations/{conv_id}/ resolver
stays as a fallback during transition.

Delegates:
  - conversation_dir / sidecar_path: session-aware, legacy fallback (#1112)
  - iter_conversation_archives: both roots, deduped (#1112)
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Iterator
from pathlib import Path

log = logging.getLogger(__name__)

# legacy flat suffix → in-directory filename. Order: most-specific first
# so `.compacted.jsonl` is matched before `.jsonl` by the migration script.
SIDECAR_FILENAMES: tuple[tuple[str, str], ...] = (
    (".compacted.jsonl", "compacted.jsonl"),
    (".jsonl", "archive.jsonl"),
    (".notes.md", "notes.md"),
    (".decisions.json", "decisions.json"),
    (".context.json", "context.json"),
    (".last_request.json", "last_request.json"),
    (".canvas.json", "canvas.json"),
    (".skills.json", "skills.json"),
    (".skill_data.json", "skill_data.json"),
    (".vault_grants.json", "vault_grants.json"),
    (".scheduled_wakes.json", "scheduled_wakes.json"),
)


def _safe_conv_id(conv_id: str) -> str:
    safe = conv_id.replace("/", "").replace("\\", "").replace("..", "")
    return safe if safe not in ("", ".") else "_invalid"


def conversations_root(config) -> Path:
    return (config.workspace_path / "conversations").resolve()


def sessions_root(config) -> Path:
    """Session-scoped root (#1112): data/{agent_id}/sessions."""
    return (config.workspace_path / "sessions").resolve()


def session_dir(config, session_id: str) -> Path:
    """Session directory: sessions/{session_id}/ (#1112)."""
    return sessions_root(config) / _safe_conv_id(session_id)


def session_conversation_dir(config, session_id: str, conv_id: str) -> Path:
    """Conversation directory within its session: sessions/{session_id}/{conv_id}/ (#1112)."""
    return session_dir(config, session_id) / _safe_conv_id(conv_id)


def _is_child_conv_id(conv_id: str) -> bool:
    """True when the conv id carries a delegate child marker (#1112)."""
    return "--child-" in conv_id


def _session_id_for_conv(conv_id: str) -> str:
    """Session that owns a conversation: the parent conv id for children,
    else the conv id itself (a standalone conversation is its own session)."""
    if _is_child_conv_id(conv_id):
        return conv_id.split("--child-", 1)[0]
    return conv_id


def remap_legacy_rel_path(config, rel_path: str) -> str | None:
    """Session-layout workspace-relative path for a stored legacy path, or None.

    Data written before ``make migrate-sessions`` (#1112) — attachment
    dicts in message metadata, workspace file URLs — stores
    workspace-relative paths like ``conversations/{conv_id}/uploads/f``.
    After the migration those dirs live under ``sessions/`` (standalone at
    ``sessions/{conv_id}/``, children at ``sessions/{parent}/{child}/``),
    so stored paths go stale. Returns the remapped workspace-relative path
    when the file exists at the session location; None when ``rel_path``
    is not legacy-shaped or the file is not there either (callers keep
    their own not-found handling).
    """
    prefix = "conversations/"
    if not rel_path or not rel_path.startswith(prefix):
        return None
    rest = rel_path[len(prefix) :]
    conv_id, sep, tail = rest.partition("/")
    if not sep or not tail:
        return None
    if _is_child_conv_id(conv_id):
        target = session_conversation_dir(config, _session_id_for_conv(conv_id), conv_id) / tail
    else:
        target = session_dir(config, conv_id) / tail
    target = target.resolve()
    workspace = config.workspace_path.resolve()
    if not target.is_relative_to(sessions_root(config).resolve()):
        return None
    if not target.is_file():
        return None
    return str(target.relative_to(workspace))


def conversation_dir(config, conv_id: str, *, create: bool = False) -> Path:
    """Session-aware conversation dir with legacy fallback (#1112).

    New layout: standalone conversations live at sessions/{conv_id}/;
    delegated children live under their parent's session at
    sessions/{parent_session}/{child_conv_id}/. Legacy layout:
    conversations/{conv_id}/.

    A conversation is pinned to one root for its lifetime (#1112 review):
    the session location wins whenever it exists (migrated), the legacy
    location wins while it is the only one that exists (unmigrated), and a
    brand-new conversation — neither exists — resolves to the session
    location so its first write materializes and pins it there. Without
    the brand-new rule, a tool-less conversation would start in the legacy
    root and flip mid-life at the first `create=True` caller (e.g.
    tool_outputs materialization), splitting its archive across roots.
    """
    legacy_root = conversations_root(config)
    legacy = legacy_root / _safe_conv_id(conv_id)
    legacy_d = legacy.resolve()
    if not legacy_d.is_relative_to(legacy_root):
        return legacy_root / "_invalid"
    sid = _session_id_for_conv(conv_id)
    new = session_conversation_dir(config, sid, conv_id) if _is_child_conv_id(conv_id) else session_dir(config, sid)
    target = new if (new.exists() or not legacy.exists()) else legacy
    base = legacy_root if target == legacy else sessions_root(config)
    d = target.resolve()
    if not d.is_relative_to(base):
        return base / "_invalid"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def sidecar_path(config, conv_id: str, filename: str) -> Path:
    """Resolve a per-conversation sidecar under the session-aware conversation dir."""
    return conversation_dir(config, conv_id) / filename


def _conversation_dirs(config) -> list[Path]:
    """Conversation dirs across the legacy and session roots, deduped by conv id.

    The session root is two levels deep (#1112 review): standalone
    conversations live at sessions/{conv_id}/ and delegated children nest
    one level down at sessions/{parent}/{child_conv_id}/ — descend into
    session dirs for child-shaped entries so migrated children are
    enumerated. On a name collision with the legacy root (split state from
    a pre-fix root flip plus the migration's skip-if-exists), the
    session-root entry wins: it is the migrated, authoritative copy.
    """
    found: dict[str, Path] = {}
    for base, is_session in ((conversations_root(config), False), (sessions_root(config), True)):
        if not base.exists():
            continue
        try:
            children = sorted(base.iterdir())
        except OSError as exc:
            # Fail open: this feeds startup recovery, search, and UI listing —
            # a transient FS error shouldn't break those flows.
            log.warning("Failed to list conversations dir %s: %s", base, exc)
            continue
        for child in children:
            if not child.is_dir():
                continue
            if is_session:
                found[child.name] = child
                _add_session_children(found, child)
            else:
                found.setdefault(child.name, child)
    return sorted(found.values())


def _add_session_children(found: dict[str, Path], session_path: Path) -> None:
    """Add delegated-child conversation dirs nested in a session dir (#1112).

    Only child-shaped names ({parent}--child-{hex}) qualify, so
    session-internal subdirs (tool_outputs, uploads) are never mistaken
    for conversations. Session entries overwrite any legacy-root entry for
    the same conv id."""
    try:
        nested = sorted(session_path.iterdir())
    except OSError as exc:
        log.warning("Failed to list session dir %s: %s", session_path, exc)
        return
    for sub in nested:
        if sub.is_dir() and _is_child_conv_id(sub.name):
            found[sub.name] = sub


def iter_conversation_archives(config) -> Iterator[tuple[str, Path]]:
    """Yield (conv_id, archive_path) for every conversation directory."""
    for child in _conversation_dirs(config):
        archive = child / "archive.jsonl"
        if archive.exists():
            yield child.name, archive


def delete_conversation_files(config, conv_id: str) -> None:
    """Remove all on-disk state for a conversation: the {id}/ directory
    (archive, sidecars, workflow.json, uploads).

    Also best-effort removes any leftover pre-#576 flat sidecars
    (conversations/{id}.SUFFIX). The runtime no longer *reads* the flat
    layout, but a delete must still be thorough on a partially-migrated or
    migration-skipped instance — otherwise a "deleted" conversation could
    leave readable history on disk. This is cleanup-only; it never makes a
    flat file authoritative."""
    # Remove every directory the conversation may occupy. The resolver pins
    # a conversation to one root, but a conversation split across roots by
    # a pre-fix mid-life flip (plus the migration's skip-if-exists) has a
    # dir in both — deleting only the pinned one would let the stale copy
    # resurface in listings (#1112 review).
    sid = _session_id_for_conv(conv_id)
    session_loc = (
        session_conversation_dir(config, sid, conv_id) if _is_child_conv_id(conv_id) else session_dir(config, sid)
    )
    legacy_loc = conversations_root(config) / _safe_conv_id(conv_id)
    for d in (session_loc, legacy_loc):
        if not d.exists():
            continue
        try:
            shutil.rmtree(d)
        except OSError as exc:
            log.warning("Failed to remove conversation dir %s: %s", d, exc)
    base = conversations_root(config)
    safe = _safe_conv_id(conv_id)
    for legacy_suffix, _ in SIDECAR_FILENAMES:
        p = (base / f"{safe}{legacy_suffix}").resolve()
        if p.is_relative_to(base) and p.exists():
            try:
                p.unlink()
            except OSError as exc:
                log.warning("Failed to remove legacy sidecar %s: %s", p, exc)

    if hasattr(config, "agent_path"):
        wake_file = scheduled_wakes_path(config, conv_id)
        if wake_file.exists():
            try:
                wake_file.unlink()
            except OSError as exc:
                log.warning("Failed to remove scheduled wakes file %s: %s", wake_file, exc)
        approval_file = approval_state_path(config, conv_id)
        if approval_file.exists():
            try:
                approval_file.unlink()
            except OSError as exc:
                log.warning("Failed to remove approval state file %s: %s", approval_file, exc)


def last_request_path(config, conv_id: str) -> Path:
    """Resolve the last LLM request sidecar at conversations/{conv_id}/last_request.json."""
    return sidecar_path(config, conv_id, "last_request.json")


def scheduled_wakes_path(config, conv_id: str) -> Path:
    """Resolve the per-conversation scheduled wakes file under the admin data dir:
    data/{agent_id}/scheduled_wakes/{conv_id}.json.
    Stored outside the agent-writable workspace to prevent unauthorized wake scheduling or forgery (#1119).
    """
    base = (config.agent_path / "scheduled_wakes").resolve()
    safe = _safe_conv_id(conv_id)
    return base / f"{safe}.json"


def approval_state_path(config, conv_id: str) -> Path:
    """Resolve the per-conversation shell approval state file under the admin data dir:
    data/{agent_id}/approval_state/{conv_id}.json.
    Stored outside the agent-writable workspace: approval guidance steers the shell
    reviewer, so an agent that could write it could grant itself shell access (#1160).
    """
    base = (config.agent_path / "approval_state").resolve()
    safe = _safe_conv_id(conv_id)
    return base / f"{safe}.json"
