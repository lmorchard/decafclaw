---
name: daily-todo-migration
description: Moves all unchecked to-do items from yesterday's daily note to their respective sections in today's note. Creates today's note from a template if it doesn't exist. Use for daily to-do rollover, migrating tasks, or setting up today's journal page.
user-invocable: true
---

## Standard Operating Procedure

To perform the daily to-do migration, you MUST follow these steps in order:

1.  **Activate Skill**: Activate the `markdown_vault` skill.
2.  **Confirm Vault Path**: Search memory for the user's vault `base_path`. If not found, ask the user for it and save it. A common default is `obsidian/main`.
3.  **Get Paths**: Call `vault_daily_path` for both yesterday (`offset=-1`) and today.
4.  **Check for Today's Note**: Use `workspace_list` on the directory of today's note to check if the file exists.
5.  **Create if Missing**: **CRITICAL:** Only if the file does *not* exist, call `md_create` using the default template (e.g., `{base_path}/templates/daily.md`). Skip file creation, otherwise.
6.  **Scan and Move**: Define the sections to scan (`['today', 'tonight', 'tomorrow', 'this week']`). For each section, use `md_show` on yesterday's note to find unchecked `- [ ]` items and their line numbers.
7.  **Handle Someday Items**: Use `md_show` on yesterday's note to find unchecked `- [ ]` items in the `someday` section. Call `md_move_lines` with `position="prepend"` and **no `to_section`** to migrate these items to the top of the list in `{base_path}/pages/someday.md` (a sectionless file).
8.  **Execute Move**: Group the line numbers by section. For each group, call `md_move_lines` to migrate the lines to the corresponding `to_section` in today's note.
9.  **Verify**: Call `md_show` on the modified sections in today's note to confirm completion and announce success to the user.

## Tool Behavior and Side Effects

-   **`md_move_lines`**: This tool returns an error if the `to_file` does not exist — it will NOT create files automatically. This is why the **Check-then-Create** procedure for today's note is mandatory. The `to_section` parameter is optional: omit it when targeting sectionless files (like `pages/someday.md`). Use `position="prepend"` to insert before the first list item, or `position="append"` (default) to add after the last item.
-   **`md_create`**: This tool will fail with an error if the file already exists. It does not overwrite files.
-   **Error Handling**: If `md_show` returns an error because a section does not exist in the source file, ignore it and proceed to the next section.

## Idempotency and Recovery

This process is safe to run multiple times. `md_move_lines` removes lines from the source file, so a second run on the same day will find no unchecked items to move and will not create duplicate tasks.

**If the process fails or is interrupted:** You can safely restart it from the beginning. The skill will pick up where it left off. If you find that today's note was created but is empty or incomplete (due to an improper run), it is safe to delete it and restart the process to ensure it is created correctly from the template.
