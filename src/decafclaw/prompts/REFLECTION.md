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
or contain significant errors.

Important guidelines:
- The assistant accumulates knowledge across turns. Referencing information
  from prior tool calls (listed below) is legitimate, NOT hallucination.
- Only fail if the response CONTRADICTS tool results or makes claims with
  no plausible source in either the current or prior turn tools.
- "Info not in this turn's results but consistent with prior turns" is acceptable.
- "Info that contradicts what tools actually returned" is a failure.

Common failure modes to watch for:
- The assistant deflected ("I don't have access to that") when tools were available
- Tool results were fetched but the response doesn't use or synthesize them
- A multi-part question was only partially answered
- The response contradicts what the tools returned

Do NOT fail a response just because it could be better. Fail only when
the response meaningfully misses what the user asked for.

---

{retrieved_context}

{prior_turn_tools}

User: {user_message}

{tool_results_summary}

Assistant response: {agent_response}
