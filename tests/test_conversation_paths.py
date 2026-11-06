import json
from pathlib import Path
from types import SimpleNamespace

from decafclaw.archive import append_message
from decafclaw.conversation_paths import (
    conversation_dir,
    conversations_root,
    delete_conversation_files,
    iter_conversation_archives,
    session_conversation_dir,
    session_dir,
    sessions_root,
    sidecar_path,
)


def _cfg(tmp_path):
    return SimpleNamespace(workspace_path=tmp_path, agent_path=tmp_path / "agent")


# --- conversation_dir -------------------------------------------------------


def test_conversation_dir_brand_new_resolves_to_session_root(tmp_path):
    # #1112 review: a brand-new conversation (no dir in either root) resolves
    # to the session-scoped root even without create=True, so its first write
    # (which mkdirs the parent) materializes and pins it under sessions/ —
    # instead of starting in the legacy root and flipping mid-life.
    cfg = _cfg(tmp_path)
    assert conversation_dir(cfg, "abc") == sessions_root(cfg) / "abc"


def test_conversation_dir_create_makes_dir(tmp_path):
    cfg = _cfg(tmp_path)
    d = conversation_dir(cfg, "abc", create=True)
    assert d.is_dir()
    # #1112: new conversations create under the session-scoped root.
    assert d == sessions_root(cfg) / "abc"


def test_conversation_dir_standalone_prefers_session_layout(tmp_path):
    cfg = _cfg(tmp_path)
    sessions_root(cfg).mkdir(parents=True)
    convdir = sessions_root(cfg) / "abc"
    convdir.mkdir(parents=True)
    (convdir / "archive.jsonl").write_text("{}")
    assert conversation_dir(cfg, "abc") == convdir


def test_conversation_dir_legacy_fallback_before_migration(tmp_path):
    cfg = _cfg(tmp_path)
    convdir = conversations_root(cfg) / "abc"
    convdir.mkdir(parents=True)
    (convdir / "archive.jsonl").write_text("{}")
    assert conversation_dir(cfg, "abc") == convdir
    assert not (sessions_root(cfg) / "abc").exists()


def test_conversation_dir_child_resolves_under_parent_session(tmp_path):
    cfg = _cfg(tmp_path)
    parent_conv = "parent-abc"
    child_conv = "parent-abc--child-xyz"
    parent_dir = sessions_root(cfg) / parent_conv
    child_dir = parent_dir / child_conv
    child_dir.mkdir(parents=True)
    assert conversation_dir(cfg, child_conv) == child_dir


def test_conversation_dir_without_create_does_not_make_dir(tmp_path):
    cfg = _cfg(tmp_path)
    d = conversation_dir(cfg, "abc")
    assert not d.exists()
    assert not (conversations_root(cfg) / "abc").exists()
    assert not (sessions_root(cfg) / "abc").exists()


# --- sandboxing -------------------------------------------------------------


def test_conversation_dir_traversal_stays_under_root(tmp_path):
    # Sandboxed to an allowed root (sessions for a brand-new id, legacy when
    # the conversation is resident there) — never outside either root.
    cfg = _cfg(tmp_path)
    d = conversation_dir(cfg, "../../etc")
    assert d.resolve().is_relative_to(sessions_root(cfg)) or d.resolve().is_relative_to(conversations_root(cfg))


def test_conversation_dir_empty_id_resolves_to_invalid(tmp_path):
    cfg = _cfg(tmp_path)
    d = conversation_dir(cfg, "")
    assert d == sessions_root(cfg) / "_invalid"


def test_conversation_dir_dotdot_only_resolves_to_invalid(tmp_path):
    cfg = _cfg(tmp_path)
    d = conversation_dir(cfg, "..")
    assert d == sessions_root(cfg) / "_invalid"


# --- sidecar_path -----------------------------------------------------------


def test_sidecar_path_returns_dir_path(tmp_path):
    # #1112 review: brand-new conversations resolve under the session root.
    cfg = _cfg(tmp_path)
    p = sidecar_path(cfg, "abc", "notes.md")
    assert p == sessions_root(cfg) / "abc" / "notes.md"


def test_sidecar_path_returns_dir_path_when_file_exists(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    convdir = root / "abc"
    convdir.mkdir(parents=True)
    new = convdir / "notes.md"
    new.write_text("hi")
    p = sidecar_path(cfg, "abc", "notes.md")
    assert p == new


# --- iter_conversation_archives --------------------------------------------


def test_iter_archives_empty_when_root_missing(tmp_path):
    cfg = _cfg(tmp_path)
    assert list(iter_conversation_archives(cfg)) == []


def test_iter_archives_fails_open_on_oserror(tmp_path, monkeypatch):
    # A transient FS error while listing must not propagate — this helper
    # feeds startup recovery, search, and UI listing.
    cfg = _cfg(tmp_path)
    conversations_root(cfg).mkdir(parents=True)

    def _boom(self):
        raise OSError("simulated listing failure")

    monkeypatch.setattr(Path, "iterdir", _boom)
    assert list(iter_conversation_archives(cfg)) == []


def test_iter_archives_yields_dir_layout(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    convdir = root / "abc"
    convdir.mkdir(parents=True)
    archive = convdir / "archive.jsonl"
    archive.write_text("{}")
    assert list(iter_conversation_archives(cfg)) == [("abc", archive)]


def test_iter_archives_skips_compacted(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    convdir = root / "abc"
    convdir.mkdir(parents=True)
    (convdir / "compacted.jsonl").write_text("{}")
    assert list(iter_conversation_archives(cfg)) == []


def test_iter_archives_skips_dir_without_archive(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    convdir = root / "abc"
    convdir.mkdir(parents=True)
    (convdir / "notes.md").write_text("hi")
    assert list(iter_conversation_archives(cfg)) == []


# --- iter_conversation_archives: session layout (#1112 review finding 1) ----


def test_iter_archives_yields_migrated_child_under_parent_session(tmp_path):
    # #1112 review: the migration moves delegated children to
    # sessions/{parent}/{child_conv_id}/ — enumeration must descend into
    # session dirs for child-shaped entries, or migrated children vanish
    # from search, the UI list, and startup orphan recovery.
    cfg = _cfg(tmp_path)
    parent_dir = sessions_root(cfg) / "parent-abc"
    child_dir = parent_dir / "parent-abc--child-xyz"
    child_dir.mkdir(parents=True)
    parent_archive = parent_dir / "archive.jsonl"
    child_archive = child_dir / "archive.jsonl"
    parent_archive.write_text("{}\n")
    child_archive.write_text("{}\n")
    assert list(iter_conversation_archives(cfg)) == [
        ("parent-abc", parent_archive),
        ("parent-abc--child-xyz", child_archive),
    ]


def test_iter_archives_session_root_beats_legacy_on_collision(tmp_path):
    # #1112 review: on a name collision (split state from a pre-fix root
    # flip plus the migration's skip-if-exists), the session-root copy is
    # authoritative for listings.
    cfg = _cfg(tmp_path)
    legacy = conversations_root(cfg) / "abc"
    legacy.mkdir(parents=True)
    (legacy / "archive.jsonl").write_text("legacy\n")
    session = sessions_root(cfg) / "abc"
    session.mkdir(parents=True)
    (session / "archive.jsonl").write_text("session\n")
    assert list(iter_conversation_archives(cfg)) == [("abc", session / "archive.jsonl")]


def test_iter_archives_ignores_session_internal_subdirs(tmp_path):
    # Session-internal subdirs (tool_outputs, uploads) are not conversations.
    cfg = _cfg(tmp_path)
    convdir = sessions_root(cfg) / "abc"
    (convdir / "tool_outputs").mkdir(parents=True)
    (convdir / "uploads").mkdir()
    archive = convdir / "archive.jsonl"
    archive.write_text("{}\n")
    assert list(iter_conversation_archives(cfg)) == [("abc", archive)]


# --- root pinning (#1112 review finding 2) ----------------------------------


def test_new_conversation_pinned_to_session_root_across_materialization(tmp_path):
    # #1112 review: reproduces the mid-life-flip demo. A conversation that
    # starts without any create=True caller (tool-less turn 1) must stay at
    # one root across the first create=True caller (tool_outputs
    # materialization) — one archive holding every turn.
    cfg = _cfg(tmp_path)
    append_message(cfg, "abc", {"role": "user", "content": "turn 1"})
    tool_outputs = conversation_dir(cfg, "abc", create=True) / "tool_outputs"
    tool_outputs.mkdir(parents=True, exist_ok=True)
    append_message(cfg, "abc", {"role": "assistant", "content": "turn 2"})

    # Exactly one location holds the archive, with both turns.
    assert not (conversations_root(cfg) / "abc").exists()
    archive = sessions_root(cfg) / "abc" / "archive.jsonl"
    contents = [json.loads(line)["content"] for line in archive.read_text().splitlines()]
    assert contents == ["turn 1", "turn 2"]


def test_legacy_conversation_pinned_against_mid_life_flip(tmp_path):
    # #1112 review: a conversation resident in the legacy root (unmigrated)
    # stays pinned there — a create=True caller must not materialize the
    # session dir mid-life and strand later writes in a second location.
    cfg = _cfg(tmp_path)
    legacy = conversations_root(cfg) / "abc"
    legacy.mkdir(parents=True)
    (legacy / "archive.jsonl").write_text("{}\n")

    append_message(cfg, "abc", {"role": "user", "content": "turn N"})
    d = conversation_dir(cfg, "abc", create=True)

    assert d == legacy
    assert not (sessions_root(cfg) / "abc").exists()
    assert (legacy / "archive.jsonl").exists()


def test_delete_removes_both_roots_for_split_conversation(tmp_path):
    # #1112 review: a conversation split across roots by a pre-fix flip must
    # be fully purged — deleting only the pinned (session) location would
    # let the stale legacy copy resurface in listings.
    cfg = _cfg(tmp_path)
    legacy = conversations_root(cfg) / "abc"
    legacy.mkdir(parents=True)
    (legacy / "archive.jsonl").write_text("{}\n")
    session = sessions_root(cfg) / "abc"
    session.mkdir(parents=True)
    (session / "archive.jsonl").write_text("{}\n")

    delete_conversation_files(cfg, "abc")

    assert not legacy.exists()
    assert not session.exists()


# --- delete_conversation_files ---------------------------------------------


def test_delete_removes_dir_with_nested_files(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    convdir = root / "abc"
    uploads = convdir / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "file").write_text("data")
    (convdir / "workflow.json").write_text("{}")
    (convdir / "archive.jsonl").write_text("{}")
    (convdir / "last_request.json").write_text("{}")
    delete_conversation_files(cfg, "abc")
    assert not convdir.exists()


def test_delete_also_removes_leftover_flat_sidecars(tmp_path):
    # Defense for a partially-migrated / migration-skipped instance: delete
    # must still purge pre-#576 flat sidecars so "deleted" history doesn't
    # linger on disk, even though the runtime no longer reads them.
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    root.mkdir(parents=True)
    flat_archive = root / "abc.jsonl"
    flat_notes = root / "abc.notes.md"
    flat_last_request = root / "abc.last_request.json"
    flat_archive.write_text("{}")
    flat_notes.write_text("- note")
    flat_last_request.write_text("{}")
    delete_conversation_files(cfg, "abc")
    assert not flat_archive.exists()
    assert not flat_notes.exists()
    assert not flat_last_request.exists()


def test_delete_is_noop_when_nothing_exists(tmp_path):
    cfg = _cfg(tmp_path)
    # Should not raise.
    delete_conversation_files(cfg, "abc")


def test_last_request_path(tmp_path):
    from decafclaw.conversation_paths import last_request_path

    cfg = _cfg(tmp_path)
    p = last_request_path(cfg, "conv-123")
    # #1112 review: brand-new conversations resolve under the session root.
    assert p == sessions_root(cfg) / "conv-123" / "last_request.json"


def test_scheduled_wakes_path(tmp_path):
    from decafclaw.conversation_paths import scheduled_wakes_path

    cfg = _cfg(tmp_path)
    p = scheduled_wakes_path(cfg, "conv-123")
    assert p == cfg.agent_path / "scheduled_wakes" / "conv-123.json"


# --- remap_legacy_rel_path ----------------------------------------------------


def test_remap_legacy_rel_path_standalone(tmp_path):
    from decafclaw.conversation_paths import remap_legacy_rel_path

    cfg = _cfg(tmp_path)
    target = sessions_root(cfg) / "abc" / "uploads" / "f.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")

    assert remap_legacy_rel_path(cfg, "conversations/abc/uploads/f.pdf") == "sessions/abc/uploads/f.pdf"


def test_remap_legacy_rel_path_child_nested_under_parent(tmp_path):
    from decafclaw.conversation_paths import remap_legacy_rel_path

    cfg = _cfg(tmp_path)
    target = sessions_root(cfg) / "parent1" / "parent1--child-abc" / "uploads" / "f.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")

    assert (
        remap_legacy_rel_path(cfg, "conversations/parent1--child-abc/uploads/f.pdf")
        == "sessions/parent1/parent1--child-abc/uploads/f.pdf"
    )


def test_remap_legacy_rel_path_non_legacy_returns_none(tmp_path):
    from decafclaw.conversation_paths import remap_legacy_rel_path

    cfg = _cfg(tmp_path)
    assert remap_legacy_rel_path(cfg, "vault/pages/Foo.md") is None
    assert remap_legacy_rel_path(cfg, "sessions/abc/uploads/f.pdf") is None
    assert remap_legacy_rel_path(cfg, "") is None


def test_remap_legacy_rel_path_missing_target_returns_none(tmp_path):
    from decafclaw.conversation_paths import remap_legacy_rel_path

    cfg = _cfg(tmp_path)
    # No file at the session location — the remap must not claim success.
    assert remap_legacy_rel_path(cfg, "conversations/ghost/uploads/f.pdf") is None


def test_remap_legacy_rel_path_stays_under_sessions(tmp_path):
    from decafclaw.conversation_paths import remap_legacy_rel_path

    cfg = _cfg(tmp_path)
    # A hostile stored path must not remap to something outside sessions/.
    assert remap_legacy_rel_path(cfg, "conversations/../secrets/f.pdf") is None
