# Knowledge Base (Wiki) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an Obsidian-compatible wiki as a bundled always-loaded skill. The agent can create, read, search, and maintain wiki pages as curated knowledge alongside episodic memory. Wiki pages are indexed in semantic search with a score boost.

**Architecture:** Wiki lives at `workspace/wiki/`. A native skill (`skills/wiki/`) provides tools and gardening guidance. A new `always-loaded: true` frontmatter field auto-activates the skill at startup. Wiki pages are indexed as `source_type: "wiki"` in the embeddings DB with a configurable score boost.

**Tech Stack:** Python, existing skills/embeddings/prompts patterns.

**Key integration points:**
- `skills/__init__.py` — parse `always-loaded` field on SkillInfo
- `prompts/__init__.py` — auto-activate always-loaded skills in system prompt
- `tools/tool_registry.py` — exempt always-loaded skill tools from deferral
- `embeddings.py` — add wiki indexing, score boost for wiki source_type
- `agent.py` — register always-loaded skill tools at turn start

---

### Task 1: Add `always-loaded` field to SkillInfo and parsing

**Files:**
- Modify: `src/decafclaw/skills/__init__.py` — add field, parse from frontmatter
- Modify: `tests/test_skills.py` — parsing tests

- [ ] **Step 1: Write failing test**

```python
def test_parse_always_loaded(tmp_path):
    """Parse always-loaded field from frontmatter."""
    skill_dir = tmp_path / "wiki"
    _write_skill(skill_dir,
        'name: wiki\ndescription: "Knowledge base"\nalways-loaded: true')
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info is not None
    assert info.always_loaded is True

def test_parse_always_loaded_default(tmp_path):
    """always-loaded defaults to False."""
    skill_dir = tmp_path / "basic"
    _write_skill(skill_dir, 'name: basic\ndescription: "Basic"')
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info.always_loaded is False
```

- [ ] **Step 2: Add `always_loaded` field to SkillInfo**

In `skills/__init__.py`, add to the SkillInfo dataclass:
```python
always_loaded: bool = False
```

In `parse_skill_md()`, add:
```python
always_loaded=bool(meta.get("always-loaded", False)),
```

- [ ] **Step 3: Run tests**

Run: `make check && make test`

- [ ] **Step 4: Commit**

```
feat: add always-loaded field to SkillInfo frontmatter
```

---

### Task 2: Auto-activate always-loaded skills in system prompt and agent loop

**Files:**
- Modify: `src/decafclaw/prompts/__init__.py` — append always-loaded skill bodies to system prompt
- Modify: `src/decafclaw/agent.py` — register always-loaded skill tools at turn start
- Modify: `src/decafclaw/tools/tool_registry.py` — exempt always-loaded skill tools from deferral
- Create: `tests/test_always_loaded.py` — integration tests

- [ ] **Step 1: Write failing tests**

```python
def test_always_loaded_skills_in_system_prompt(config, tmp_path):
    """Always-loaded skills have their body appended to the system prompt."""
    # Create a skill dir with always-loaded: true and a body
    # Call load_system_prompt, verify body appears in the prompt

def test_always_loaded_skill_tools_registered(ctx):
    """Always-loaded skill tools are available without activate_skill."""
    # Verify that tools from always-loaded skills appear in ctx
```

- [ ] **Step 2: Modify `load_system_prompt` to collect always-loaded skills**

In `prompts/__init__.py`, after discovering skills:
```python
# Auto-activate always-loaded skills
always_loaded_bodies = []
always_loaded_skill_names = []
for skill in skills:
    if skill.always_loaded and skill.body:
        always_loaded_bodies.append(skill.body)
        always_loaded_skill_names.append(skill.name)

if always_loaded_bodies:
    sections.append("\n\n".join(always_loaded_bodies))
    log.info(f"Always-loaded skills: {', '.join(always_loaded_skill_names)}")
```

Return `always_loaded_skill_names` alongside `skills` so the agent loop knows which skills to auto-register tools for.

- [ ] **Step 3: Register always-loaded skill tools in agent turn startup**

In `agent.py`, at the start of `run_agent_turn`, after skill restoration:
- Check `config.always_loaded_skill_names` (set during prompt assembly)
- For each, load native tools via `_load_native_tools` and register on ctx
- Add their tool names to the always-loaded set so they're exempt from deferral

- [ ] **Step 4: Update `tool_registry.py`**

In `get_always_loaded_names()`, include tool names from always-loaded skills:
```python
# Add tools from always-loaded skills
for name in getattr(config, "always_loaded_skill_names", []):
    skill_map = {s.name: s for s in getattr(config, "discovered_skills", [])}
    skill = skill_map.get(name)
    if skill and skill.has_native_tools:
        # Tool names will be added when the skill is loaded
        pass  # handled by agent.py registration
```

Actually simpler: store the tool names on config when registering them, and include in `get_always_loaded_names`.

- [ ] **Step 5: Run tests**

Run: `make check && make test`

- [ ] **Step 6: Commit**

```
feat: auto-activate always-loaded skills in system prompt and agent loop
```

---

### Task 3: Wiki tools — `wiki_read`, `wiki_write`, `wiki_list`

**Files:**
- Create: `src/decafclaw/skills/wiki/tools.py` — tool implementations
- Create: `tests/test_wiki_tools.py` — tool tests

- [ ] **Step 1: Write failing tests**

```python
class TestWikiRead:
    def test_read_existing_page(self, config):
        wiki_dir = config.workspace_path / "wiki"
        wiki_dir.mkdir(parents=True)
        (wiki_dir / "Test Page.md").write_text("# Test Page\n\nContent here.")
        result = wiki_read(config, "Test Page")
        assert "Content here." in result

    def test_read_nonexistent(self, config):
        result = wiki_read(config, "Nope")
        assert "not found" in result.lower() or "error" in result.lower()

    def test_read_finds_in_subdirectory(self, config):
        wiki_dir = config.workspace_path / "wiki" / "people"
        wiki_dir.mkdir(parents=True)
        (wiki_dir / "Alice.md").write_text("# Alice\n\nA person.")
        result = wiki_read(config, "Alice")
        assert "A person." in result

class TestWikiWrite:
    def test_create_new_page(self, config):
        wiki_write(config, "New Page", "# New Page\n\nFresh content.")
        path = config.workspace_path / "wiki" / "New Page.md"
        assert path.exists()
        assert "Fresh content." in path.read_text()

    def test_overwrite_existing(self, config):
        wiki_dir = config.workspace_path / "wiki"
        wiki_dir.mkdir(parents=True)
        (wiki_dir / "Existing.md").write_text("Old content.")
        wiki_write(config, "Existing", "New content.")
        assert "New content." in (wiki_dir / "Existing.md").read_text()

    def test_rejects_path_traversal(self, config):
        result = wiki_write(config, "../../../etc/passwd", "hack")
        assert "error" in result.lower()

class TestWikiList:
    def test_list_pages(self, config):
        wiki_dir = config.workspace_path / "wiki"
        wiki_dir.mkdir(parents=True)
        (wiki_dir / "Alpha.md").write_text("# Alpha")
        (wiki_dir / "Beta.md").write_text("# Beta")
        result = wiki_list(config)
        assert "Alpha" in result
        assert "Beta" in result

    def test_empty_wiki(self, config):
        result = wiki_list(config)
        assert "no pages" in result.lower() or "empty" in result.lower()
```

- [ ] **Step 2: Implement `wiki_read`, `wiki_write`, `wiki_list`**

In `skills/wiki/tools.py`:

- `wiki_read(ctx, page)` — resolve page name to file path (search wiki root + subdirs), return content or error
- `wiki_write(ctx, page, content)` — validate path stays within wiki root, create dirs if needed, write file
- `wiki_list(ctx, pattern="")` — glob wiki dir for `*.md` files, return names with last-modified dates

Path safety: resolve the full path and verify it starts with the wiki root.

- [ ] **Step 3: Define TOOLS dict and TOOL_DEFINITIONS list**

Standard skill tools pattern: `TOOLS = {"wiki_read": ..., "wiki_write": ..., "wiki_list": ...}` and matching `TOOL_DEFINITIONS`.

- [ ] **Step 4: Run tests**

Run: `make check && pytest tests/test_wiki_tools.py -v`

- [ ] **Step 5: Commit**

```
feat: add wiki_read, wiki_write, wiki_list tools
```

---

### Task 4: Wiki tools — `wiki_search` and `wiki_backlinks`

**Files:**
- Modify: `src/decafclaw/skills/wiki/tools.py` — add search and backlinks
- Modify: `tests/test_wiki_tools.py` — tests

- [ ] **Step 1: Write failing tests**

```python
class TestWikiSearch:
    def test_search_by_content(self, config):
        wiki_dir = config.workspace_path / "wiki"
        wiki_dir.mkdir(parents=True)
        (wiki_dir / "Drinks.md").write_text("# Drinks\n\nBoulevardier, Old Fashioned")
        (wiki_dir / "Food.md").write_text("# Food\n\nPizza, Tacos")
        result = wiki_search(config, "Boulevardier")
        assert "Drinks" in result
        assert "Food" not in result

    def test_search_by_title(self, config):
        wiki_dir = config.workspace_path / "wiki"
        wiki_dir.mkdir(parents=True)
        (wiki_dir / "DecafClaw.md").write_text("# DecafClaw\n\nAn agent.")
        result = wiki_search(config, "DecafClaw")
        assert "DecafClaw" in result

    def test_search_no_results(self, config):
        result = wiki_search(config, "nonexistent")
        assert "no" in result.lower()

class TestWikiBacklinks:
    def test_finds_backlinks(self, config):
        wiki_dir = config.workspace_path / "wiki"
        wiki_dir.mkdir(parents=True)
        (wiki_dir / "DecafClaw.md").write_text("# DecafClaw\n\nAn agent.")
        (wiki_dir / "Les Orchard.md").write_text("# Les\n\nWorks on [[DecafClaw]].")
        (wiki_dir / "Blog.md").write_text("# Blog\n\nNo links here.")
        result = wiki_backlinks(config, "DecafClaw")
        assert "Les Orchard" in result
        assert "Blog" not in result

    def test_no_backlinks(self, config):
        wiki_dir = config.workspace_path / "wiki"
        wiki_dir.mkdir(parents=True)
        (wiki_dir / "Orphan.md").write_text("# Orphan\n\nNobody links here.")
        result = wiki_backlinks(config, "Orphan")
        assert "no" in result.lower()
```

- [ ] **Step 2: Implement `wiki_search` and `wiki_backlinks`**

- `wiki_search(ctx, query)` — scan all `*.md` files in wiki root (recursive), match query against filename and file content (case-insensitive substring), return page names with excerpts
- `wiki_backlinks(ctx, page)` — scan all `*.md` files for `[[page]]` pattern (case-insensitive), return linking pages with context lines

- [ ] **Step 3: Add to TOOLS and TOOL_DEFINITIONS**

- [ ] **Step 4: Run tests**

Run: `make check && pytest tests/test_wiki_tools.py -v`

- [ ] **Step 5: Commit**

```
feat: add wiki_search and wiki_backlinks tools
```

---

### Task 5: SKILL.md — wiki gardening guidance

**Files:**
- Create: `src/decafclaw/skills/wiki/SKILL.md` — skill definition with always-loaded flag and gardening guidance

- [ ] **Step 1: Write SKILL.md**

```yaml
---
name: wiki
description: Obsidian-compatible knowledge base for curated, evolving knowledge
always-loaded: true
---
```

Body contains the wiki gardening principles from the spec: search before create, revise and rewrite, link liberally, sources section, entity pages, merge related content, split when large, update over duplicate. Also include the memory boundary rule (wiki tools only modify files in workspace/wiki/).

- [ ] **Step 2: Verify skill discovery picks it up**

Run: `make check && make test`
Verify the wiki skill appears in discovered skills and its `always_loaded` flag is True.

- [ ] **Step 3: Commit**

```
feat: add wiki SKILL.md with gardening guidance
```

---

### Task 6: Semantic search integration — wiki indexing

**Files:**
- Modify: `src/decafclaw/embeddings.py` — add wiki reindexing, score boost
- Modify: `src/decafclaw/skills/wiki/tools.py` — index on wiki_write
- Modify: `tests/test_wiki_tools.py` — indexing tests

- [ ] **Step 1: Add `reindex_wiki` to embeddings.py**

Similar pattern to `reindex_all` (memories) and `reindex_conversations`:
```python
async def reindex_wiki(config):
    """Index all wiki pages into the embeddings database."""
    wiki_dir = config.workspace_path / "wiki"
    if not wiki_dir.is_dir():
        return
    for path in wiki_dir.rglob("*.md"):
        text = path.read_text().strip()
        if text:
            rel_path = str(path.relative_to(config.workspace_path))
            await index_entry(config, rel_path, text, source_type="wiki")
```

Call it from the `reindex_cli` function alongside the existing memory/conversation reindexing.

- [ ] **Step 2: Add score boost for wiki results in `search_similar_sync`**

In the search ranking, apply a multiplier to wiki results:
```python
# After computing similarity scores
WIKI_BOOST = 1.2
if row_source_type == "wiki":
    similarity *= WIKI_BOOST
```

This requires the search to also fetch `source_type` from the DB.

- [ ] **Step 3: Index on `wiki_write`**

In `wiki_write`, after writing the file, call:
```python
await index_entry(config, rel_path, content, source_type="wiki")
```

- [ ] **Step 4: Write tests**

```python
class TestWikiEmbeddings:
    @pytest.mark.asyncio
    async def test_wiki_write_indexes(self, config):
        """wiki_write should create an embeddings entry."""
        ...

    def test_wiki_boost_applied(self, config):
        """Wiki results should have boosted scores."""
        ...
```

- [ ] **Step 5: Update `make reindex` / reindex_cli**

Add `await reindex_wiki(config)` to the reindex pipeline.

- [ ] **Step 6: Run tests**

Run: `make check && make test`

- [ ] **Step 7: Commit**

```
feat: add wiki pages to semantic search with score boost
```

---

### Task 7: Docs and CLAUDE.md update

**Files:**
- Create: `docs/wiki.md` — feature documentation
- Modify: `docs/index.md` — add wiki to feature list
- Modify: `CLAUDE.md` — key files, conventions

- [ ] **Step 1: Create `docs/wiki.md`**

Full feature doc: storage layout, Obsidian compatibility, tools, gardening guidance, semantic search integration, linking to memories, always-loaded skills concept.

- [ ] **Step 2: Update `docs/index.md`**

Add wiki to the Features section.

- [ ] **Step 3: Update CLAUDE.md**

Add to key files:
- `src/decafclaw/skills/wiki/` — Bundled wiki skill: Obsidian-compatible knowledge base, always-loaded

Add to conventions:
- "Always-loaded skills." Skills with `always-loaded: true` in SKILL.md are auto-activated at startup — their body is in the system prompt and tools are always available. No permission check. Wiki is the first.
- "Wiki is curated knowledge, memory is episodic." Memory is append-only daily entries. Wiki pages are living documents revised over time. The agent should use wiki for distilled facts and memory for timestamped observations.

- [ ] **Step 4: Run checks**

Run: `make check && make test`

- [ ] **Step 5: Commit**

```
docs: add wiki knowledge base documentation
```
