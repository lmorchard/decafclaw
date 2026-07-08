# blog-develop — spec

A new on-demand contrib skill that takes an **arbitrary blog-post idea** and runs
a fresh session of **scout → interview → deep research → draft**, producing a
take-or-leave first draft in the vault. Sibling to the existing `blog-ideas`
skill, but decoupled from it: the idea can come from the blog-ideas living page
or from nowhere in particular.

## Motivation

`blog-ideas` (`contrib/skills/blog-ideas/SKILL.md`) maintains a living weekly
page of post ideas. The next step Les wants is to *develop* a single idea: flesh
it out through a short interview, back it with web research, and draft it. This
is the natural follow-on, and structurally it is the interview→artifact pipeline
discussed in the #255 workflow-replay-engine work — built here as a skill, not on
the (not-yet-shipped) replay engine.

## Non-goals (v1)

- Not a batch/eager developer — does **not** auto-develop headliners on a
  schedule. On-demand only. (Decided: "selective, on-demand.")
- Not a publishing pipeline — output is a disposable first draft, not a finished
  post. No deploy, no commit of the draft, no touching the live blog.
- Not durable across restart — single on-demand conversation, short-horizon. No
  replay-engine dependency.
- No automated reviewer/editor pass in v1 (see Deferred work).

## Surface & configuration

- **Location:** `contrib/skills/blog-develop/` (SKILL.md + optional `tools.py`).
  Personal blogging workflow → contrib, like `blog-ideas`.
- **Frontmatter:**
  - `user-invocable: true` — triggers via `/blog-develop` (web UI) / `!blog-develop` (Mattermost).
  - `context: inline` — runs in the conversation with the full tool set. (NOTE:
    originally specced as `context: fork`; corrected during implementation —
    `fork` runs the orchestrator through `run_child_turn`, which strips
    `delegate_task` + vault tools AND is a one-shot child turn that cannot
    conduct the multi-turn interview. The "fresh session" intent is met by
    running the command in a new conversation.)
  - `required-skills: [vault, tabstack]` — vault for read/write, tabstack for web research.
  - `effort: strong`.
  - **No** `SCHEDULE.md` — on-demand only.
- **Idea input:** `$ARGUMENTS` is the seed idea (any free text).
  - No arg → first turn asks "what do you want to write about?" and offers to
    list this week's `blog-ideas` headliners as a convenience seed source.
  - The `blog-ideas` living page is **one optional source** of a seed, not a
    dependency or entry contract.

## Phase flow

Orchestrated by the SKILL.md body (the main forked agent follows it). Two child
worker *roles* dispatched via `delegate_task`: **researcher** (invoked twice) and
**drafter**.

1. **Scout** — `delegate_task` → *researcher* (shallow). Quick lay-of-the-land +
   prior art on the idea. Returns a short scout brief. Purpose: make the
   interview questions sharp instead of asked in a vacuum.
2. **Interview** — back in the main forked session. Conversational, **one
   question per turn**, adaptive, informed by the scout brief. Shapes angle,
   audience, core claim, voice, and whether to engage/differentiate from prior
   art the scout found. Caps ~5–6 questions, or ends early when Les says "go."
   **Human-driven transitions** (each user answer advances the phase).
3. **Deep research** — `delegate_task` → *researcher* (deep), steered by the idea
   + interview answers. Returns a structured **research brief**: key claims,
   sources/URLs, supporting angles, counterpoints.
4. **Draft** — `delegate_task` → *drafter*: research brief + interview answers +
   Les's **voice docs** (voice-and-tone / stop-slop equivalents from the vault) →
   first draft.
5. **Write** — `vault_write blog/drafts/<slug>.md`.

### Why this is safe from the #255 "LLM-crank" failure mode

The only place the LLM drives multi-phase flow autonomously is the tail
(deep-research → draft → write = 2 bounded `delegate_task` calls). Everything
before it is **human-driven** (the interview). That is the human + persona-as-
worker combination established as reliable in the #255 retrospective — not a
long-horizon autonomous state machine the model must crank.

**Known risk:** the orchestrator could narrate-instead-of-delegate (the failure
seen in #557 live smokes). Mitigations: loud imperative MUST instructions in the
SKILL.md body around each `delegate_task` boundary; bounded, single-purpose
worker contracts. Validation is **live smoke testing**, not just evals (per the
project rule: evals guard structure, prompt tuning comes from live smoke). If it
proves flaky in practice, this skill is the natural first workload to lift onto
the #255 replay engine.

## Worker contracts

- **Researcher (scout mode):** input = idea seed. Output = short scout brief
  (what's already out there, notable prior art, obvious angles/gaps). Shallow:
  a few searches, light fetching.
- **Researcher (deep mode):** input = idea + interview answers. Output =
  structured research brief: claims, sources (with URLs), supporting angles,
  counterpoints. Heavier: targeted searches + fetches.
- **Drafter:** input = research brief + interview answers + voice docs.
  Output = first-draft markdown in Les's voice.

## Output contract

- File: `blog/drafts/<slug>.md` (vault). `<slug>` derived from the title.
- Frontmatter: `title`, `tags: [blog-draft]`, `status: draft`, `created`,
  `seed:` (the original idea), `sources:` (from the research brief).
- The research brief is appended as a collapsed `## Research notes` section at
  the bottom of the same file — sources stay visible without sibling-file
  clutter.
- **Does not mutate** the `blog-ideas` living page (decoupled). Because
  `blog/drafts` is already a location `blog-ideas` reads ("Stalled drafts —
  `vault_list folder=blog/drafts`"), developed drafts naturally feed back into
  future brainstorms with no extra wiring.

## Deferred work (v2)

- **Reviewer agent** as an additional `delegate_task` phase after Draft, running
  two skills as quality lenses:
  - a **"humanizer"** skill that scans for "AI tells" and flags/removes them, and
  - an **"editor"** skill that applies Strunk & White ("The Elements of Style")
    advice to the prose.
  This is the reliable reviewer/verifier-persona pattern (independent fresh
  context, fixed return contract) identified across SAW, the agentic-harness
  video, and our own #255 retro. Deliberately deferred from v1, not an oversight.

- **Replace `tabstack_research` with an owned/local research workflow.** v1's
  scout + deep-research workers call `tabstack_research`, a hosted external
  service. v2 could swap it for a DecafClaw-owned research loop (e.g. a
  delegate-orchestrated or #255-replay-engine research workflow over `web_fetch`
  + a search primitive), reducing the external dependency and making the research
  loop inspectable and tunable — the "own the rig" theme from this session.
  Wrinkle: a fully-local loop needs a **search** primitive; DecafClaw currently
  has only `web_fetch` locally (search comes via tabstack), so this means either
  adding a local search tool or accepting fetch-only research. The worker
  contracts (structured `return_schema`) stay the same; only the tool the worker
  uses changes — so this is a localized swap, not a redesign.

## Open items

- Identify the actual voice-doc paths in the vault to feed the drafter (the
  blog-ideas skill references "my actual voice" / voice standards — confirm exact
  page paths during implementation).
