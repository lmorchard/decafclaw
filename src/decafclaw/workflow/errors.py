"""Workflow engine exceptions."""


class WorkflowError(Exception):
    """Base for workflow engine failures."""


class WorkflowSuspended(Exception):
    """Raised by a journaled primitive that needs to suspend for the user.

    Deliberately extends Exception, NOT WorkflowError: a suspension is normal
    control flow (the engine catches it and posts a confirmation), not a
    failure. Do not change the base class to WorkflowError — `except
    WorkflowError` in the engine must NOT swallow suspensions.

    Carries everything the harness needs to post a confirmation and, on
    response, journal the answer at the right position.
    """

    def __init__(self, *, seq: int, args_fingerprint: str, prompt: str,
                 choices: list[str] | None = None):
        super().__init__(f"workflow suspended at step {seq}: {prompt!r}")
        self.seq = seq
        self.args_fingerprint = args_fingerprint
        self.prompt = prompt
        self.choices = choices


class WorkflowNonDeterministic(WorkflowError):
    """Replay reached a journaled call whose args don't match the record.

    Means control flow diverged between runs — a determinism bug in the
    orchestrator. Fail loudly rather than return a stale result.
    """

    def __init__(self, seq: int, recorded_kind: str, recorded_fp: str,
                 got_kind: str, got_fp: str):
        super().__init__(
            f"workflow non-deterministic at step {seq}: recorded "
            f"{recorded_kind}/{recorded_fp}, replay produced {got_kind}/{got_fp}"
        )
        self.seq = seq
        self.recorded_kind = recorded_kind
        self.recorded_fp = recorded_fp
        self.got_kind = got_kind
        self.got_fp = got_fp
