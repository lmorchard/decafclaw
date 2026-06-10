"""Durable, ordered record of journaled-call results for one workflow run.

Entries are keyed positionally by execution order: the Nth journaled call
executed gets sequence N. This is what makes loops replay correctly — same
control flow produces the same execution order, hence the same keys.
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
    seq: int
    kind: str
    args_fingerprint: str
    result: Any


@dataclasses.dataclass
class Journal:
    workflow_name: str
    status: str = "running"  # running | suspended | done | error
    entries: list[JournalEntry] = dataclasses.field(default_factory=list)

    def get(self, seq: int) -> JournalEntry | None:
        if 0 <= seq < len(self.entries):
            return self.entries[seq]
        return None

    def append(self, seq: int, kind: str, args_fingerprint: str,
               result: Any) -> None:
        if seq != len(self.entries):
            raise ValueError(
                f"non-contiguous journal append: seq={seq}, "
                f"len={len(self.entries)}")
        self.entries.append(JournalEntry(seq, kind, args_fingerprint, result))

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Journal":
        j = cls(workflow_name=d["workflow_name"],
                status=d.get("status", "running"))
        j.entries = [JournalEntry(**e) for e in d.get("entries", [])]
        return j


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
