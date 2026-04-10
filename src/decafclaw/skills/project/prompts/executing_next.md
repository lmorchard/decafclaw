EXECUTING: {done}/{total} steps done.

**Next step: {number}. {description}**

1. Mark it in_progress: project_update_step(step="{number}", status="in_progress")
2. Do the work for THIS STEP ONLY
3. Mark it done: project_update_step(step="{number}", status="done", note="...")
4. Call project_next_task for the next step

Write output files to the project directory: {directory}

Complete one step at a time. Do NOT skip ahead to later steps.
