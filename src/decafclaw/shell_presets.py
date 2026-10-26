"""Shared, serialized custom shell auto-approval preset store.

Manages custom saved presets at ``data/{agent_id}/shell_approval_presets.json``
under an ``asyncio.Lock`` with atomic file replacement (temp file + replace).
Malformed or corrupt preset files raise ValueError and are never overwritten.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .config import Config

log = logging.getLogger(__name__)

DEFAULT_AUX_APPROVAL_PRESETS: dict[str, str] = {
    "developer": (
        "Auto-approve standard software development commands within active workspace repositories, "
        "including running test suites (e.g. pytest, npm test, vitest), linters and formatters (e.g. ruff, black, eslint, prettier), "
        "typecheckers (e.g. pyright, mypy, tsc), build commands, executing repository development tasks via environment runners "
        "(e.g. uv run <task>, poetry run <task>, npm run <script>, cargo test/check/build), "
        "non-destructive local git operations (e.g. git status, git diff, git log, git add, git commit, git branch, git checkout, git switch), "
        "non-destructive remote git operations (e.g. git fetch, git pull, and pushing to new or existing feature branches matching feat/*, fix/*, or test/*), "
        "and safe sequential command chains (&&) between approved development commands. "
        "Do not auto-approve package installations (e.g. pip install, uv add, npm install), package publication, "
        "destructive operations (e.g. git reset --hard, deleting uncommitted work, force-pushing, pushing directly to main/master, "
        "or deleting branches/repositories), or commands operating outside the workspace."
    ),
    "github": (
        "Auto-approve GitHub CLI (gh) commands for issue/PR inspection, creation, and safe workflow management "
        "(e.g. gh issue list/view/create, gh pr list/view/diff/checkout/create/checks, gh run list/view/watch). "
        "Do not auto-approve destructive operations (such as repository deletion, deleting releases/tags, or closing/deleting issues without context)."
    ),
}

PresetSource = Literal["builtin", "config", "custom"]


def split_rules(text: str) -> list[str]:
    """Parse a preset's text into individual rule strings.

    Strips leading `- ` bullet prefixes and whitespace, skipping empty lines.
    """
    rules: list[str] = []
    for line in text.splitlines():
        cleaned = line.strip().lstrip("-").strip()
        if cleaned and cleaned not in rules:
            rules.append(cleaned)
    return rules


def format_rules(rules: list[str]) -> str:
    """Format a list of rules into preset text (bullet list or single sentence)."""
    cleaned_rules: list[str] = []
    for r in rules:
        c = str(r).strip().lstrip("-").strip()
        if c and c not in cleaned_rules:
            cleaned_rules.append(c)
    if not cleaned_rules:
        return ""
    if len(cleaned_rules) == 1:
        return cleaned_rules[0]
    return "\n".join(f"- {r}" for r in cleaned_rules)


class ShellPresetStore:
    """Shared, serialized store for shell approval presets.

    Reads and writes custom presets at ``data/{agent_id}/shell_approval_presets.json``
    under an ``asyncio.Lock``. Atomic writes guarantee concurrent writes are serialized
    and never lose updates or truncate data. Malformed files raise ValueError and are
    never overwritten.
    """

    # Global mapping of resolved agent_path -> asyncio.Lock to ensure even distinct
    # ShellPresetStore instances for the same agent directory serialize writes across coroutines.
    _locks: dict[Path, asyncio.Lock] = {}

    def __init__(self, config: "Config"):
        self._config = config
        self._path = config.agent_path / "shell_approval_presets.json"
        resolved_path = self._path.resolve()
        if resolved_path not in self._locks:
            self._locks[resolved_path] = asyncio.Lock()
        self._lock = self._locks[resolved_path]

    @property
    def path(self) -> Path:
        return self._path

    def _load_saved_unlocked(self) -> dict[str, str]:
        """Load saved custom presets from disk.

        Raises ValueError if the file exists but contains invalid or corrupt data.
        """
        if not self._path.exists():
            return {}
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError(f"Presets file root is not a JSON object: {type(data).__name__}")
            result: dict[str, str] = {}
            for k, v in data.items():
                if not isinstance(k, str) or not isinstance(v, str):
                    raise ValueError(f"Invalid preset entry ({k!r}: {v!r}); keys and values must be strings")
                result[k] = v
            return result
        except json.JSONDecodeError as e:
            log.warning("Could not read saved shell approval presets from %s: %s", self._path, e)
            raise ValueError(f"Corrupt or unreadable presets file: {e}") from e
        except OSError as e:
            log.warning("OS error reading saved shell approval presets from %s: %s", self._path, e)
            raise ValueError(f"Corrupt or unreadable presets file: {e}") from e

    def _save_saved_unlocked(self, presets: dict[str, str]) -> None:
        """Atomically write presets dict to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(presets, indent=2) + "\n"
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with open(fd, "w", encoding="utf-8") as f:
                f.write(content)
            Path(tmp).replace(self._path)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise

    def get_config_presets(self) -> dict[str, str]:
        """Return configured presets from config.shell.aux_approval_presets."""
        raw_config_presets = getattr(self._config.shell, "aux_approval_presets", {})
        if isinstance(raw_config_presets, str):
            try:
                cfg = json.loads(raw_config_presets)
                if isinstance(cfg, dict):
                    return {str(k): str(v) for k, v in cfg.items() if isinstance(k, str) and isinstance(v, str)}
            except (json.JSONDecodeError, ValueError):
                return {}
            return {}
        elif isinstance(raw_config_presets, dict):
            return {str(k): str(v) for k, v in raw_config_presets.items() if isinstance(k, str) and isinstance(v, str)}
        return {}

    async def get_all_presets_dict(self) -> dict[str, str]:
        """Merge built-in presets with config presets and saved custom presets.

        Returns {preset_name: text}. A custom preset shadows a config preset with the same name.
        """
        async with self._lock:
            saved: dict[str, str] = {}
            try:
                saved = self._load_saved_unlocked()
            except ValueError as e:
                log.warning("Could not load custom saved presets from disk: %s", e)
            config_presets = self.get_config_presets()
            return {**DEFAULT_AUX_APPROVAL_PRESETS, **config_presets, **saved}

    def get_all_presets_dict_sync(self) -> dict[str, str]:
        """Sync version of get_all_presets_dict for sync callers (e.g. resolve_aux_approval_guidance)."""
        saved: dict[str, str] = {}
        try:
            saved = self._load_saved_unlocked()
        except ValueError as e:
            log.warning("Could not load custom saved presets from disk: %s", e)
        config_presets = self.get_config_presets()
        return {**DEFAULT_AUX_APPROVAL_PRESETS, **config_presets, **saved}

    async def list_presets(self) -> list[dict]:
        """Return all presets with name, rules, source, and shadows_config flag.

        Output item shape:
        {
            "name": str,
            "rules": list[str],
            "source": "builtin" | "config" | "custom",
            "shadows_config": bool,
        }
        """
        async with self._lock:
            saved = self._load_saved_unlocked()
            config_presets = self.get_config_presets()
            builtins = DEFAULT_AUX_APPROVAL_PRESETS

            result: list[dict] = []
            all_names = sorted(set(builtins.keys()) | set(config_presets.keys()) | set(saved.keys()))
            for name in all_names:
                if name in saved:
                    source = "custom"
                    text = saved[name]
                    shadows = name in config_presets
                elif name in config_presets:
                    source = "config"
                    text = config_presets[name]
                    shadows = False
                else:
                    source = "builtin"
                    text = builtins[name]
                    shadows = False

                result.append(
                    {
                        "name": name,
                        "rules": split_rules(text),
                        "source": source,
                        "shadows_config": shadows,
                    }
                )
            return result

    async def get_preset(self, name: str) -> dict | None:
        """Get a single preset by name, or None if not found."""
        presets = await self.list_presets()
        for p in presets:
            if p["name"] == name:
                return p
        return None

    async def save_preset(self, name: str, rules: list[str]) -> tuple[bool, str]:
        """Create or replace a custom preset with the given rules.

        Raises ValueError if name is a built-in or config preset (or invalid), or if the file is corrupt.
        Returns (is_new, updated_text).
        """
        if not name or not name.strip():
            raise ValueError("Preset name cannot be empty")
        name = name.strip()

        if name in DEFAULT_AUX_APPROVAL_PRESETS:
            raise ValueError(f"Cannot overwrite built-in preset '{name}'")
        config_presets = self.get_config_presets()
        if name in config_presets:
            raise ValueError(f"Cannot overwrite config preset '{name}'")

        clean_rules = [str(r).strip() for r in rules if str(r).strip()]
        if not clean_rules:
            raise ValueError("Preset rules cannot be empty")

        updated_text = format_rules(clean_rules)

        async with self._lock:
            saved = self._load_saved_unlocked()
            is_new = name not in saved
            saved[name] = updated_text
            self._save_saved_unlocked(saved)
            log.info("Saved custom shell approval preset '%s' to %s", name, self._path)
            return is_new, updated_text

    async def delete_preset(self, name: str) -> bool:
        """Delete a custom preset.

        Raises ValueError if name is a built-in or config preset, or if the file is corrupt.
        Returns True if deleted, False if preset was not in saved custom presets.
        """
        if not name or not name.strip():
            raise ValueError("Preset name cannot be empty")
        name = name.strip()

        if name in DEFAULT_AUX_APPROVAL_PRESETS:
            raise ValueError(f"Cannot delete built-in preset '{name}'")
        config_presets = self.get_config_presets()
        if name in config_presets:
            raise ValueError(f"Cannot delete config preset '{name}'")

        async with self._lock:
            saved = self._load_saved_unlocked()
            if name not in saved:
                return False
            del saved[name]
            self._save_saved_unlocked(saved)
            log.info("Deleted custom shell approval preset '%s' from %s", name, self._path)
            return True

    def _append_rules_unlocked(self, name: str, rules: list[str]) -> tuple[bool, str]:
        saved = self._load_saved_unlocked()
        config_presets = self.get_config_presets()

        is_new = name not in saved and name not in config_presets
        raw_existing = saved.get(name) or config_presets.get(name) or ""
        existing_rules = split_rules(str(raw_existing).strip())

        for r in rules:
            c = str(r).strip().lstrip("-").strip()
            if c and c not in existing_rules:
                existing_rules.append(c)

        updated_text = format_rules(existing_rules)
        saved[name] = updated_text
        self._save_saved_unlocked(saved)
        log.info("Saved custom shell approval preset '%s' from tool to %s", name, self._path)
        return is_new, updated_text

    async def append_rules_from_tool(self, name: str, rules: list[str]) -> tuple[bool, str]:
        """Save or append rules from the shell_guidance tool.

        Allows shadowing config presets and appends rules to existing preset text.
        """
        if not name or not name.strip():
            raise ValueError("Preset name is required")
        name = name.strip()
        if name in DEFAULT_AUX_APPROVAL_PRESETS:
            raise ValueError(f"Cannot overwrite built-in preset '{name}'. Please choose a custom preset name.")

        async with self._lock:
            return self._append_rules_unlocked(name, rules)

    def _save_custom_preset_sync(self, name: str, rules: list[str]) -> tuple[bool, str]:
        """Synchronous wrapper for legacy callers and tests."""
        if not name or not name.strip():
            raise ValueError("Preset name is required")
        name = name.strip()
        if name in DEFAULT_AUX_APPROVAL_PRESETS:
            raise ValueError(f"Cannot overwrite built-in preset '{name}'. Please choose a custom preset name.")

        return self._append_rules_unlocked(name, rules)
