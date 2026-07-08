# blog-develop

On-demand workflow that develops **any** blog-post idea into a take-or-leave
first draft: **scout → interview → deep research → draft**. Sibling to
`blog-ideas`, but decoupled — the idea can come from your weekly ideas page or
from anywhere.

Run it with `/blog-develop <your idea>` (web UI) or `!blog-develop <your idea>`
(Mattermost). With no argument it asks what you want to write about. The scout,
research, and draft phases run as child agents; the finished draft lands in
`blog/drafts/<slug>.md` in your vault for you to take or leave.

## Install

Add to your agent's `extra_skill_paths` in `data/{agent_id}/config.json`:

```json
{
  "extra_skill_paths": [
    "$CONTRIB/skills/blog-develop"
  ]
}
```

Requires the `tabstack` skill configured (web research) and a vault. No extra
binaries or API keys beyond what `tabstack` needs.
