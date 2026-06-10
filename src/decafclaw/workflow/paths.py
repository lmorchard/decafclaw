"""Per-conversation workflow file paths.

New convention (#255): per-conversation files live in a directory named
for the conversation id — ``conversations/{conv_id}/workflow.json`` —
rather than the flat ``{conv_id}.*`` sidecar pattern. Only the workflow
file adopts this now; existing sidecars migrate later (see spec).
"""
from pathlib import Path


def _safe_conv_id(conv_id: str) -> str:
    safe = conv_id.replace("/", "").replace("\\", "").replace("..", "")
    return safe if safe not in ("", ".") else "_invalid"


def workflow_dir(config, conv_id: str, *, create: bool = False) -> Path:
    base = (config.workspace_path / "conversations").resolve()
    d = (base / _safe_conv_id(conv_id)).resolve()
    if not d.is_relative_to(base):
        d = base / "_invalid"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def workflow_path(config, conv_id: str) -> Path:
    return workflow_dir(config, conv_id) / "workflow.json"
