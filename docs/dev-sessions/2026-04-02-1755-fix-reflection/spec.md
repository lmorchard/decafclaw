# Fix Reflection Truncation — Spec

## Related issue

GitHub issue #200 — Reflection judge incorrectly flags tool results as hallucinations due to truncation

## Problem

The reflection judge truncates tool results to `max_tool_result_len` (default 2000 chars) before evaluating responses. When tools like `vault_search` return large results (full page content, 20k+ chars), the judge sees only a tiny snippet and incorrectly concludes the agent hallucinated details that were actually present in the full results.

This causes the agent to retract correct answers, which is worse than no reflection at all.

## Root cause

`_extract_tool_lines()` in `reflection.py` truncates each tool result to `max_result_len`:
```python
if len(content) > max_result_len:
    content = content[:max_result_len] + "..."
```

For `vault_search` returning 10 pages, only the first result's partial text is visible to the judge. The judge sees "3 result(s)" in the header but can't see results 2-10.

## Fix approach

Rather than just increasing the truncation limit (which burns tokens), we should **summarize** what was returned instead of truncating. The judge doesn't need the full page content — it needs to know:

1. What tool was called and with what args
2. How many results were returned
3. What the results contained (titles, key identifiers) — enough to verify the agent's response isn't fabricated

For vault_search specifically: "vault_search('creative writing') returned 10 results: [Comparison of Short Stories, There was no minimum safe size, Rays of a Distant Sun, When the Halloween invite, ...]"

This gives the judge enough to say "the agent mentioned these stories, and they were in the search results" without needing 20k chars of full content.

## Design

### Smart result summarization

Replace blind truncation with structured summarization in `_extract_tool_lines()`:

1. For each tool result, extract a **summary** rather than truncating raw text:
   - Parse the result content for structure (headings, tl;dr lines, result counts)
   - Extract key identifiers (page names, titles, file paths)
   - Include the result count and first-line summaries

2. Keep the truncation as a fallback for unstructured results.

3. Make the summary strategy pluggable — different tools may benefit from different summarization. Start with vault_search and vault_read as the high-value targets.

### Specific patterns to extract

- **vault_search results**: Parse "Found N result(s)" header, extract page titles from `# Title` or `> tl;dr:` lines
- **vault_read**: Extract page title and tl;dr
- **web_fetch**: Extract URL and page title
- **Generic**: First N lines or first paragraph

### Config

Keep `max_tool_result_len` but use it as the budget for the *summarized* output, not for raw truncation. Default can stay at 2000 since summaries are much more compact.

## Success criteria

1. Reflection judge sees enough context to verify tool-based responses
2. The creative writing scenario from #200 passes reflection without false flagging
3. Token usage for reflection calls doesn't significantly increase
4. All existing tests pass
