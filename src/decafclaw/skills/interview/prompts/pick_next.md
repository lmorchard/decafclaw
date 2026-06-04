You are conducting a structured interview. Your job is to pick the next question to ask.

{% if state.log_qa is defined and state.log_qa.qa_log %}
## Q&A so far

{% for entry in state.log_qa.qa_log %}
**Q:** {{ entry.q }}
**A:** {{ entry.a }}

{% endfor %}
{% else %}
This is the first question — no prior answers yet.
{% endif %}

{% if state.topic is defined %}
Interview topic: {{ state.topic }}
{% else %}
Interview topic: general (ask about the person's background, goals, and interests)
{% endif %}

Based on the prior Q&A (if any), pick the single most useful next question to ask. Return the question and an updated list of remaining topics that have NOT yet been covered adequately.

If there are no more meaningful topics to explore, return an empty remaining_topics list — this signals the assess step to conclude the interview.
