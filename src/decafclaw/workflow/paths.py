"""Per-conversation workflow file paths.

The workflow journal lives in the per-conversation directory
``conversations/{conv_id}/workflow.json`` (see ``conversation_paths``
for the shared directory + sanitization logic).
"""
from pathlib import Path

from decafclaw.conversation_paths import conversation_dir


def workflow_dir(config, conv_id: str, *, create: bool = False) -> Path:
    return conversation_dir(config, conv_id, create=create)


def workflow_path(config, conv_id: str) -> Path:
    return workflow_dir(config, conv_id) / "workflow.json"
