You are writing a final summary of an interview.

## Full Q&A Log

{% for entry in state.log_qa.qa_log %}
**Q:** {{ entry.q }}
**A:** {{ entry.a }}

{% endfor %}

Write a concise but complete summary of what was covered in this interview. Capture the key points, any notable themes or patterns, and any open threads that might be worth following up on. The summary should read as a coherent paragraph or two, not just a list of answers.
