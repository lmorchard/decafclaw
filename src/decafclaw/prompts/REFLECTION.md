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

Common failure modes to watch for:
- The assistant deflected ("I don't have access to that") when tools were available
- Tool results were fetched but the response doesn't use or synthesize them
- A multi-part question was only partially answered
- The response contradicts what the tools returned

Do NOT fail a response just because it could be better. Fail only when
the response meaningfully misses what the user asked for.

---

{retrieved_context}

User: {user_message}

{tool_results_summary}

Assistant response: {agent_response}