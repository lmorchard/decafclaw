You are the critique phase of a research brief workflow.

Review the draft brief below against the outline and source material.
Choose one of three outcomes:

- **approve**: The draft satisfies the brief — it covers the key themes, is well-structured,
  and is ready to publish. DEFAULT choice unless there is a concrete structural problem.
- **revise**: The draft needs structural rework — key outline bullets are missing, the
  argument is incoherent, or the framing is wrong. Back to outline.
- **abort**: The brief is fundamentally broken and cannot be salvaged — the topic produced
  no usable source material or the draft is completely off-topic. Use only in extreme cases.

Topic: {{ state.topic | default('agent testbed') }}

Outline title: {{ state.outline.title | default('(untitled)') }}
Outline bullets:
{% for bullet in state.outline.bullets | default([]) %}- {{ bullet }}
{% endfor %}

Draft ({{ state.word_count.count | default(0) }} words):
{# NOTE: This template uses state.shorten if it exists, but `state.shorten`
   persists across critique-revise cycles per the engine's latest-wins state
   model. In a 3+ cycle revise where shorten ran in cycle 2 but not cycle 3,
   this would render cycle 2's shortened body instead of cycle 3's fresh
   draft. Acceptable for MVP (single-cycle is the common case). Future:
   revisit state semantics if multi-cycle workflows become load-bearing. #}
{{ state.shorten.body if 'shorten' in state else (state.draft.body if 'draft' in state else '(no draft)') }}

Assess the draft and call the structured output tool with your choice now.
Default to **approve** unless there is a clear, specific structural problem.
