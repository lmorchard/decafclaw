---
name: marimo
description: >-
  Pair with a running marimo notebook. Lists live notebooks and sessions,
  adds/edits/deletes and runs cells through marimo's code-mode API, evaluates
  scratchpad Python in the live kernel, inspects the notebook graph, and opens
  the notebook in a canvas tab. Use when the user wants to work in a marimo
  notebook they have open, probe live variables, or turn analysis into durable
  notebook cells.
summary: Drive a live marimo notebook from the agent
---

## Marimo live pairing

These tools talk to a marimo server over its HTTP API. The marimo Python
package does **not** need to be installed in DecafClaw: the tools discover the
server, list browser sessions, and post code to `/api/kernel/execute`.

The live kernel is the source of truth. A notebook's `.py` file on disk can be
stale, and editing it does not reach the user's open notebook. Make notebook
changes with `marimo_add_cell`, `marimo_edit_cell`, and
`marimo_delete_cell`, not with workspace file tools.

### Tools

- `marimo_list_sessions()` — all running notebooks. Resolves a bare filename
  like `smoke_test.py` to the right session and reports live cell counts.
- `marimo_inspect(file?)` — cell IDs, code, names, execution status, errors,
  dataflow `defs`/`refs`, and public variable names.
- `marimo_add_cell(code, file?, run?, hide_code?, after_cell_id?)` — append or
  insert a cell and (by default) run it.
- `marimo_edit_cell(cell_id, code, file?, run?)` — replace a cell's code and
  (by default) run it.
- `marimo_delete_cell(cell_id, file?)` — delete a cell from the notebook.
- `marimo_eval(code, file?)` — run exploratory Python in marimo's scratchpad.
- `marimo_open_in_canvas(file, title?)` — open the notebook as a `web_frame`
  canvas tab.

Every tool takes an optional `file`. It accepts a session ID, an absolute or
relative notebook path, a `file://` URL, or a unique filename. With one open
notebook you can omit it. When a filename matches several notebooks the tool
returns the candidates instead of guessing.

### Getting a server

The tools discover servers from marimo's local registry
(`~/.local/state/marimo/servers/*.json`). Only servers started without a token
(`--no-token`) register themselves, and a notebook session exists only while
its browser tab is open.

To start a discoverable notebook, run marimo without a token:

```sh
marimo edit --no-token smoke_test.py
```

Then open the notebook URL in a browser. `--headless` still registers the
server, but no session exists until a browser connects.

For a server elsewhere, or one started with a token, configure
`skills.marimo.default_url` (or `MARIMO_URL`) and optionally
`skills.marimo.token` (or `MARIMO_TOKEN`). See [docs/marimo.md](../../../../docs/marimo.md).

Do not start a server or open a browser unless the user asks. If no session is
found, report what is missing and ask for the notebook URL.

### Scratchpad scope

`marimo_eval` runs in marimo's scratchpad: notebook globals are readable, but
top-level assignments are discarded after the call. In-place mutations to
notebook objects can persist. Use the structured cell tools to persist work.
The scratchpad supports top-level `await`.

### Notebook rules

marimo keeps the notebook a DAG:

- no cycles between cells;
- no public name defined by two cells;
- no wildcard imports.

`marimo_add_cell` and `marimo_edit_cell` surface marimo's validation errors.
A name that starts with `_` is local to its cell. Reuse existing notebook
imports instead of adding aliases. Run cells deliberately: creating or editing
a cell does not execute it unless `run` is true (the default).
