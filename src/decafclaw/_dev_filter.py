"""Dev-mode watchfiles filter: Python sources + bundled widgets + agent config.

Used by `make dev` so that editing a `widget.json` or `widget.js` under
`src/decafclaw/web/static/widgets/` also triggers a restart, the same
way `.py` edits do. Without this, the widget registry only re-scans
when the server restarts, making bundled-widget iteration annoying.

Also watches the agent config files under the agent data dir (#149): a
restart-required config change makes the in-process watcher
(config_watcher) stop the server, and this filter is what makes
`make dev` restart it. Safe config changes pass through without a
restart so the in-process watcher can apply them in place.
"""

import os
from pathlib import Path

from watchfiles import PythonFilter

from .config_watcher import (
    ConfigFileClassifier,
    classifier_baseline,
    load_env_file,
    watched_config_paths,
)

_WIDGETS_MARKER = "/web/static/widgets/"


class DevFilter(PythonFilter):
    """PythonFilter plus bundled widget files and agent config files."""

    def __init__(self) -> None:
        super().__init__()
        # Load .env for parity with the server process: DATA_HOME / AGENT_ID
        # may point the agent dir outside this checkout (#149).
        load_env_file()
        self._config_paths = {os.path.abspath(str(p)) for p in watched_config_paths()}
        # Baseline = the files as the starting server loaded them, so the
        # FIRST edit after startup is already classified. Without it a
        # restart-required change right after `make dev` stopped the server
        # with no supervisor restart (#149 review).
        self._config_classifier = ConfigFileClassifier(classifier_baseline(watched_config_paths()))

    def __call__(self, change, path: str) -> bool:
        # Parent matches *.py files (plus excludes node_modules, .venv, etc.)
        if super().__call__(change, path):
            return True
        norm = path.replace("\\", "/")
        # Also trigger on widget.json / widget.js under the bundled tier.
        if _WIDGETS_MARKER in norm and norm.endswith((".json", ".js")):
            return True
        # Agent config files: restart only for restart-required changes (#149)
        # — safe changes hot-apply without a restart. Exact resolved paths, so
        # a DATA_HOME outside the checkout and stray config.json files
        # elsewhere never match.
        if os.path.abspath(norm) in self._config_paths:
            return self._config_classifier.is_restart_required(Path(norm))
        return False
