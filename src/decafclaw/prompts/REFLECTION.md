You are evaluating whether an AI assistant's response adequately addresses
the user's request.

Review the interaction below, then:
1. Identify what the user asked for
2. Assess whether the response addresses it
3. Note any specific gaps or problems

Then output your verdict as JSON:
{{"pass": true/false, "critique": "specific feedback if failed"}}

If the response is adequate, even if imperfect, pass it.
Only fail responses that clearly miss the point, ignore the question,
or contain significant errors relative to the tool results.

---

User: {user_message}

{tool_results_summary}

Assistant response: {agent_response}