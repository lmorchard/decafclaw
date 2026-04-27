# Notes

## Implementation notes

- **Task 1 — traversal guard divergence:** `canvas._canvas_sidecar_path` rejects conv_ids containing `/`, `\`, or `..` outright rather than stripping those characters as the plan and `context_composer._context_sidecar_path` do. Stripping has a subtle bug: `..conv` becomes `conv` and silently looks valid. Strict rejection is unambiguous. Worth aligning `context_composer` to the same shape in a future cleanup (separate PR).
