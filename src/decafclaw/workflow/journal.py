"""Durable, ordered record of journaled-call results for one workflow run.

Entries are keyed by a tuple-path "seq": a tuple of ints denoting the
hierarchical position of the journaled call. Top-level calls are 1-tuples
like (0,), (1,), …; nested calls inside a sub-handle (e.g. one branch of a
parallel batch) use longer tuples like (3, 0), (3, 1), …. Same control flow
produces the same execution order, hence the same keys — which is what makes
loops replay correctly.

On disk we serialize the tuple as a dotted string ("3.1.0") for readability
and for forward compatibility with non-Python consumers. Legacy journals
that wrote a bare int seq (pre-#574) are transparently upgraded to 1-tuples
by `from_dict` so we don't need a migration step.
"""
import dataclasses
import hashlib
import json
from typing import Any

from .paths import workflow_dir, workflow_path


def fingerprint(kind: str, args: dict) -> str:
    """Stable hash of a journaled call's kind + args (order-insensitive).

    args must be JSON-serializable; non-serializable values raise TypeError by
    design (a replay guard must fail loudly rather than risk a silent
    fingerprint collision).
    """
    payload = json.dumps({"kind": kind, "args": args}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclasses.dataclass
class JournalEntry:
    seq: tuple[int, ...]
    kind: str
    args_fingerprint: str
    result: Any


@dataclasses.dataclass
class Journal:
    workflow_name: str
    status: str = "running"  # running | suspended | done | error
    entries: dict[tuple[int, ...], JournalEntry] = dataclasses.field(
        default_factory=dict)

    def get(self, seq: tuple[int, ...]) -> JournalEntry | None:
        return self.entries.get(seq)

    def append(self, seq: tuple[int, ...], kind: str,
               args_fingerprint: str, result: Any) -> None:
        if seq in self.entries:
            prev = self.entries[seq]
            raise ValueError(
                f"duplicate journal append at seq={seq}: "
                f"recorded {prev.kind}/{prev.args_fingerprint}, "
                f"now {kind}/{args_fingerprint}")
        self.entries[seq] = JournalEntry(seq, kind, args_fingerprint, result)

    def to_dict(self) -> dict:
        # Hand-rolled rather than dataclasses.asdict: tuple dict-keys aren't
        # JSON-serializable, and entries needs custom path serialization.
        # Sort by seq so the on-disk order is stable — parallel branches can
        # land out-of-order in `entries`, which would otherwise produce noisy
        # diffs and confuse manual journal inspection.
        return {
            "workflow_name": self.workflow_name,
            "status": self.status,
            "entries": [
                {"seq": path_to_str(e.seq), "kind": e.kind,
                 "args_fingerprint": e.args_fingerprint, "result": e.result}
                for e in sorted(self.entries.values(), key=lambda e: e.seq)
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Journal":
        j = cls(workflow_name=d["workflow_name"],
                status=d.get("status", "running"))
        for entry_d in d.get("entries", []):
            seq = path_from_any(entry_d["seq"])
            if seq in j.entries:
                raise ValueError(f"duplicate seq in journal file: {seq}")
            j.entries[seq] = JournalEntry(
                seq, entry_d["kind"], entry_d["args_fingerprint"],
                entry_d["result"])
        return j


def path_to_str(path: tuple[int, ...]) -> str:
    return ".".join(str(i) for i in path)


def path_from_any(v) -> tuple[int, ...]:
    """Accept tuple (in-memory), int (legacy flat seq), or dotted str (new
    on-disk)."""
    if isinstance(v, int):
        return (v,)
    if isinstance(v, str):
        return tuple(int(p) for p in v.split("."))
    if isinstance(v, (tuple, list)):
        return tuple(int(p) for p in v)
    raise TypeError(f"unrecognized journal seq: {v!r}")


def save_journal(config, conv_id: str, journal: Journal) -> None:
    """Persist the journal. Flushed on every call for crash-safety."""
    workflow_dir(config, conv_id, create=True)
    path = workflow_path(config, conv_id)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(json.dumps(journal.to_dict(), indent=2))
    tmp.replace(path)  # atomic on POSIX


def load_journal(config, conv_id: str) -> Journal | None:
    path = workflow_path(config, conv_id)
    if not path.exists():
        return None
    return Journal.from_dict(json.loads(path.read_text()))
