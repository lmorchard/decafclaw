# Vault Tools Redesign — Notes

## Session Recap

Redesigned the markdown vault skill from 29 tools to 5. The key insight: most vault operations overlap with existing workspace tools. The vault skill only needs to add what workspace tools can't do — section awareness, cross-file line moves, daily path computation, and template creation.

### What we built
- `vault_daily_path` — date → workspace path (base_path as parameter, no state)
- `md_show` — section display with absolute line numbers
- `md_move_lines` — move specific lines by number between files
- `md_section` — consolidated section ops (add/remove/rename/move)
- `md_create` — template-based file creation

### What we removed
24 tools including all the substring-matching checklist operations (check, uncheck, replace_item, find_items), all tag operations, vault base path state management (set_path, get_path), and duplicate file operations (vault_read, vault_list, vault_append, vault_prepend, vault_insert, vault_delete).

### Key design decisions
1. **Line numbers, not substring matching** — md_show always shows absolute line numbers. The agent uses these with workspace tools for editing.
2. **No vault base path state** — dropped set_path/get_path. vault_daily_path takes base_path as a parameter. Everything else uses workspace-relative paths.
3. **Workspace tools for basic ops** — read, edit, insert, delete, list all use existing workspace tools. No duplication.
4. **General-purpose markdown tools** — renamed from vault_* to md_* since they work on any markdown file.

### Stats
- 29 tools → 5 tools
- -1173/+202 lines in tools.py
- 130 library tests preserved, 16 new tool tests added
- Tested with real daily notes — works

## Session observations

- This was the last item in a marathon session that also covered: concurrent tools (#71), web UI tests (#72), tool search (#35), code quality sweep (13 items), user commands (#45), and a TypeError error message fix.
- The brainstorm was unusually productive — Les's question "what's the overlap with workspace tools?" collapsed the scope dramatically. We went from "redesign 29 tools" to "keep 5, delete the rest."
- The "what do you actually use" question cut the tool list from 29 to ~12. Then recognizing workspace overlap cut it to 5.
