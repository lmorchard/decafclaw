You are assessing the quality of an interview answer and deciding what to do next.

**Question asked:** {{ state.pick_question.question }}

**Answer received:** {{ state.ask_user.value }}

**Remaining topics:** {{ state.pick_question.remaining_topics | tojson }}

Evaluate the answer and pick one of:
- **clarify** — the answer is too vague, too short, or off-topic; re-ask the same question
- **next_question** — the answer is adequate and there are still topics remaining
- **summarize** — all topics have been covered (remaining_topics is empty or all key areas addressed)

Be generous: if the answer is reasonable even if brief, prefer next_question or summarize over clarify.
