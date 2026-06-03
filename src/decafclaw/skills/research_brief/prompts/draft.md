You are the drafting phase of a research brief workflow.

Using the outline and source material below, compose a research brief as a single `body` field.
Target length: 400-600 words. Structure: framing paragraph, 2-3 themed body sections, a brief
open-questions paragraph.

Topic: {{ state.topic | default('agent testbed') }}

Outline:
Title: {{ state.outline.title | default('(untitled)') }}
Bullets:
{% for bullet in state.outline.bullets | default([]) %}- {{ bullet }}
{% endfor %}

Source material:
{{ state.read_sources.text | default('(no source material)') }}

Write the full brief body now. Include section headings using Markdown (##).
You MUST call the structured output tool with your `body` field now.
