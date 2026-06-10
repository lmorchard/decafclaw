"""Interview → artifact: the #255 hero workflow.

Asks one question at a time, looping until the model says it has enough (or a
cap), then synthesizes an artifact. The whole thing is plain Python — the
only journaled boundary crossings are wf.user_input and wf.llm_call.
"""
from ..registry import workflow

MAX_Q = 6

_SYS_ASK = (
    "You are conducting a focused interview to gather material for a written "
    "artifact. Ask ONE good next question at a time. Decide when you have "
    "enough to write something useful."
)
_SYS_SYNTH = (
    "You synthesize an interview transcript into a clear, well-structured "
    "written artifact."
)

_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "done": {"type": "boolean",
                 "description": "True when you have enough to synthesize."},
        "question": {"type": "string",
                     "description": "The next question (empty if done)."},
    },
    "required": ["done", "question"],
}

_ARTIFACT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "body": {"type": "string", "description": "Markdown body."},
    },
    "required": ["title", "body"],
}


def _ask_prompt(topic: str, answers: list[dict]) -> str:
    lines = [f"Topic: {topic}", ""]
    if answers:
        lines.append("Answers so far:")
        for a in answers:
            lines.append(f"- Q: {a['q']}\n  A: {a['a']}")
        lines.append("")
    lines.append("Decide whether you have enough. If not, ask the next question.")
    return "\n".join(lines)


def _synth_prompt(topic: str, answers: list[dict]) -> str:
    lines = [f"Topic: {topic}", "", "Interview transcript:"]
    for a in answers:
        lines.append(f"- Q: {a['q']}\n  A: {a['a']}")
    lines.append("")
    lines.append("Write a titled markdown artifact synthesizing this.")
    return "\n".join(lines)


@workflow("interview")
async def interview(wf):
    topic = await wf.user_input("What should this interview be about?")

    answers: list[dict] = []
    while len(answers) < MAX_Q:
        decision = await wf.llm_call(
            prompt=_ask_prompt(topic, answers),
            schema=_DECISION_SCHEMA, system=_SYS_ASK)
        if decision.get("done"):
            break
        reply = await wf.user_input(decision["question"])
        answers.append({"q": decision["question"], "a": reply})

    return await wf.llm_call(
        prompt=_synth_prompt(topic, answers),
        schema=_ARTIFACT_SCHEMA, system=_SYS_SYNTH)
