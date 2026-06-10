"""WorkflowHandle — the `wf` object an orchestrator drives.

Exposes the two journaled primitives. Positional cursor advances once per
journaled call. Replay returns cached results (after a determinism check);
new llm_calls run live and journal; new user_inputs raise WorkflowSuspended.

Replay invariants the orchestrator author must respect:
  * The engine re-runs the orchestrator from the top on every resume; the
    cursor is reconstructed from 0 each run (it is NOT persisted). Re-running
    after a failed live llm_call is therefore safe — nothing was journaled, so
    the next run reaches the same step and runs it live again.
  * Orchestrators MUST NOT catch WorkflowSuspended. Swallowing it (e.g. a broad
    `except Exception`) lets the cursor advance past a step the journal never
    recorded, desynchronizing every later positional key. (Same constraint as
    Temporal's "don't catch workflow control-flow exceptions" rule.)
  * Any per-call `model=` override passed to `llm_call` must be deterministic
    (it is not part of the journal fingerprint).
"""
import logging

from . import llm as wf_llm
from .errors import WorkflowNonDeterministic, WorkflowSuspended
from .journal import fingerprint, save_journal

log = logging.getLogger(__name__)


async def _default_llm_call(ctx, **kw):
    return await wf_llm.call_structured(ctx, **kw)


class WorkflowHandle:
    def __init__(self, ctx, journal, *, llm_caller=None,
                 model: str = "vertex-gemini-flash"):
        self.ctx = ctx
        self.journal = journal
        self._cursor = 0
        self._llm_caller = llm_caller or _default_llm_call
        self._model = model

    def _check_or_none(self, seq: int, kind: str, fp: str):
        """Return (cached_result, True) for an already-journaled call at seq,
        else (None, False). Raises WorkflowNonDeterministic on a mismatch.
        """
        existing = self.journal.get(seq)
        if existing is None:
            return None, False
        if existing.kind != kind or existing.args_fingerprint != fp:
            raise WorkflowNonDeterministic(
                seq, existing.kind, existing.args_fingerprint, kind, fp)
        return existing.result, True

    async def llm_call(self, *, prompt: str, schema: dict, system: str = "",
                       tool_name: str = "submit", model: str | None = None):
        seq = self._cursor
        self._cursor += 1
        # tool_name and the per-call `model` override are intentionally NOT
        # part of the fingerprint: tool_name is a cosmetic label for the single
        # forced tool, and model is an execution detail. prompt + schema +
        # system fully determine the call's logical identity. CONSTRAINT: a
        # per-call model override therefore MUST be deterministic across
        # replays — computing it from nondeterministic state would let replay
        # reuse a result produced under a different model without the
        # determinism guard firing. (v1 orchestrators pass no per-call model.)
        fp = fingerprint("llm_call",
                         {"prompt": prompt, "schema": schema, "system": system})
        cached, hit = self._check_or_none(seq, "llm_call", fp)
        if hit:
            return cached
        result = await self._llm_caller(
            self.ctx, system=system, user_msg=prompt, schema=schema,
            tool_name=tool_name, model=model or self._model)
        self.journal.append(seq, "llm_call", fp, result)
        save_journal(self.ctx.config, self.ctx.conv_id, self.journal)
        return result

    async def user_input(self, prompt: str, *, choices: list[str] | None = None):
        seq = self._cursor
        self._cursor += 1
        fp = fingerprint("user_input", {"prompt": prompt, "choices": choices})
        cached, hit = self._check_or_none(seq, "user_input", fp)
        if hit:
            return cached
        # New, unanswered → suspend. The journal (entries 0..seq-1) is already
        # persisted by the preceding live call (or empty on a fresh start).
        raise WorkflowSuspended(seq=seq, args_fingerprint=fp, prompt=prompt,
                                choices=choices)
