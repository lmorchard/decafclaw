"""Python step functions for the interview workflow.

These are registered python step functions loaded by the engine via
importlib. Each function receives the full workflow state dict and
returns a dict that is written to state[step_id].

Imports must be absolute (decafclaw.skills.interview.tools) per CLAUDE.md
convention — the loader uses importlib.spec_from_file_location without
package context, so relative imports fail at runtime.
"""


def log_qa(state: dict) -> dict:
    """Append the latest (question, answer) pair to the qa_log.

    Demonstrates explicit accumulation under the engine's latest-wins
    state model. The prior qa_log is read from state["log_qa"]["qa_log"]
    (if it exists) and extended with the current Q&A pair.

    On clarify cycles, the same question may appear multiple times — each
    answer is recorded separately so the full interaction is preserved.
    """
    prior = state.get("log_qa", {}).get("qa_log", [])
    new_entry = {
        "q": state["pick_question"]["question"],
        "a": state["ask_user"]["value"],
    }
    return {"qa_log": prior + [new_entry]}
