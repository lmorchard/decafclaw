"""Archive-replay survey harness for suggested-next-prompt (#1738).

The survey measures whether a predicted next user message is worth showing as
ghost text *before* any UI work. It replays archived turn boundaries, scores
predictions at several granularities, and calibrates the vault-direction proxy
against trivial predictors.

Entry point: ``python -m decafclaw.survey``.
"""

from .corpus import CorpusScan, TurnBoundary, scan_corpus
from .pages import PageRetriever, create_retriever
from .stage0 import run_stage0

__all__ = [
    "CorpusScan",
    "PageRetriever",
    "TurnBoundary",
    "create_retriever",
    "run_stage0",
    "scan_corpus",
]
