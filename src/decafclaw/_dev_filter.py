"""Dev-mode watchfiles filter: Python sources + bundled widgets + agent config.

Used by `make dev` so that editing a `widget.json` or `widget.js` under
`src/decafclaw/web/static/widgets/` also triggers a restart, the same
way `.py` edits do. Without this, the widget registry only re-scans
when the server restarts, making bundled-widget iteration annoying.

Also watches the agent config files under `data/` (#149): a
restart-required config change makes the in-process watcher
(config_watcher) stop the server, and this filter is what makes
`make dev` restart it. Safe config changes pass through without a
restart so the in-process watcher can apply them in place.
"""

from pathlib import Path

from watchfiles import PythonFilter

from .config_watcher import ConfigFileClassifier

_WIDGETS_MARKER = "/web/static/widgets/"
_CONFIG_FILENAMES = {"config.json", "mcp_servers.json"}


class DevFilter(PythonFilter):
    """PythonFilter plus bundled widget files and agent config files."""

    def __init__(self) -> None:
        super().__init__()
        self._config_classifier = ConfigFileClassifier()

    def __call__(self, change, path: str) -> bool:
        # Parent matches *.py files (plus excludes node_modules, .venv, etc.)
        if super().__call__(change, path):
            return True
        norm = path.replace("\\", "/")
        # Also trigger on widget.json / widget.js under the bundled tier.
        if _WIDGETS_MARKER in norm and norm.endswith((".json", ".js")):
            return True
        # Agent config files under data/: restart only for restart-required
        # changes (#149) — safe changes hot-apply without a restart.
        parts = norm.split("/")
        if len(parts) >= 2 and "data" in parts[:-1] and parts[-1] in _CONFIG_FILENAMES:
            return self._config_classifier.is_restart_required(Path(norm))
        return False
