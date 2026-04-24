"""Dev-mode watchfiles filter: Python sources + bundled widget descriptors.

Used by `make dev` so that editing a `widget.json` or `widget.js` under
`src/decafclaw/web/static/widgets/` also triggers a restart, the same
way `.py` edits do. Without this, the widget registry only re-scans
when the server restarts, making bundled-widget iteration annoying.
"""

from watchfiles import PythonFilter

_WIDGETS_MARKER = "/web/static/widgets/"


class DevFilter(PythonFilter):
    """PythonFilter plus bundled widget .json/.js files."""

    def __call__(self, change, path: str) -> bool:
        # Parent matches *.py files (plus excludes node_modules, .venv, etc.)
        if super().__call__(change, path):
            return True
        # Also trigger on widget.json / widget.js under the bundled tier.
        norm = path.replace("\\", "/")
        if _WIDGETS_MARKER in norm and norm.endswith((".json", ".js")):
            return True
        return False
