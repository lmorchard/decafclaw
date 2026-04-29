"""Widget catalog registry — scans bundled + admin catalog dirs, validates.

See docs/widgets.md for the admin-facing guide.
"""

import html as html_lib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema.validators import validator_for

log = logging.getLogger(__name__)


_BUNDLED_DIR = Path(__file__).parent / "web" / "static" / "widgets"


_META_SCHEMA = {
    "type": "object",
    "required": ["name", "description", "modes", "data_schema"],
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "modes": {
            "type": "array",
            "items": {"type": "string", "enum": ["inline", "canvas"]},
            "minItems": 1,
        },
        "accepts_input": {"type": "boolean"},
        "data_schema": {"type": "object"},
    },
}


@dataclass
class WidgetDescriptor:
    """Parsed widget.json plus filesystem metadata."""

    name: str
    tier: str                          # "bundled" | "admin"
    description: str
    modes: list[str]
    accepts_input: bool
    data_schema: dict
    js_path: Path
    tier_root: Path = field(default_factory=Path)  # filesystem root of the tier this widget was found under
    mtime: float = 0.0
    _validator: Any = field(default=None, repr=False, compare=False)


class WidgetRegistry:
    """In-memory widget catalog.

    Admin-tier widgets override bundled on name collision.
    """

    def __init__(self, descriptors: dict[str, WidgetDescriptor] | None = None):
        self._descriptors: dict[str, WidgetDescriptor] = descriptors or {}

    def get(self, name: str) -> WidgetDescriptor | None:
        return self._descriptors.get(name)

    def list(self) -> list[WidgetDescriptor]:
        return list(self._descriptors.values())

    def tier(self, name: str) -> str | None:
        d = self._descriptors.get(name)
        return d.tier if d else None

    def resolve_path(self, name: str) -> Path:
        """Return js_path for a registered widget or raise KeyError."""
        d = self._descriptors.get(name)
        if d is None:
            raise KeyError(f"unknown widget: {name!r}")
        return d.js_path

    def normalize(self, name: str, data: dict) -> dict:
        """Apply per-widget post-validate normalization, if registered.

        Used to inject server-controlled fields (e.g. iframe_sandbox's
        wrapped CSP-locked HTML document). Returns ``data`` unchanged when
        no normalizer is registered for ``name``. Idempotent — normalizers
        regenerate derived fields rather than compounding them.

        Normalizers are bundled-tier only: admin-tier widgets may
        intentionally override bundled widgets on name collision (see
        ``load_widget_registry``), and an admin-defined widget should not
        silently inherit a bundled-only normalizer just because the name
        matches — its data shape may be entirely different.
        """
        fn = _NORMALIZERS.get(name)
        if fn is None:
            return data
        desc = self._descriptors.get(name)
        if desc is not None and desc.tier != "bundled":
            return data
        return fn(data)

    def validate(self, name: str, data: dict) -> tuple[bool, str | None]:
        """Validate widget payload against the widget's data_schema.

        Returns (ok, error_message). Unknown widget name returns
        (False, "unknown widget: ..."). Never raises.
        """
        d = self._descriptors.get(name)
        if d is None:
            return False, f"unknown widget: {name!r}"
        try:
            # Lazy validator construction; reused on repeat calls.
            if d._validator is None:
                validator_cls = validator_for(d.data_schema)
                d._validator = validator_cls(d.data_schema)
            d._validator.validate(data)
            return True, None
        except jsonschema.ValidationError as exc:
            return False, str(exc.message)
        except Exception as exc:  # defensive — malformed schema, etc.
            log.warning(
                "unexpected error validating widget %r: %s", name, exc)
            return False, f"validation error: {exc}"


def _scan_tier(root: Path, tier: str) -> dict[str, WidgetDescriptor]:
    """Scan one catalog tier root, return name → descriptor."""
    out: dict[str, WidgetDescriptor] = {}
    if not root.is_dir():
        return out
    tier_root_resolved = root.resolve()
    for subdir in sorted(root.iterdir()):
        if not subdir.is_dir():
            continue
        json_path = subdir / "widget.json"
        js_path = subdir / "widget.js"
        if not json_path.is_file():
            log.debug("widget dir %s missing widget.json — skipping", subdir)
            continue
        if not js_path.is_file():
            log.warning(
                "widget %s has widget.json but no widget.js — skipping",
                subdir.name)
            continue
        try:
            raw = json.loads(json_path.read_text())
        except json.JSONDecodeError as exc:
            log.warning("widget %s widget.json invalid JSON: %s",
                        subdir.name, exc)
            continue
        try:
            jsonschema.validate(raw, _META_SCHEMA)
        except jsonschema.ValidationError as exc:
            log.warning("widget %s widget.json failed meta-schema: %s",
                        subdir.name, exc.message)
            continue
        name = raw["name"]
        if name in out:
            # Same tier, same name in two dirs — keep first, warn.
            log.warning("duplicate widget name %r within tier %s — "
                        "ignoring %s", name, tier, subdir)
            continue
        desc = WidgetDescriptor(
            name=name,
            tier=tier,
            description=raw["description"],
            modes=list(raw["modes"]),
            accepts_input=bool(raw.get("accepts_input", False)),
            data_schema=raw["data_schema"],
            js_path=js_path,
            tier_root=tier_root_resolved,
            mtime=js_path.stat().st_mtime,
        )
        out[name] = desc
    return out


def load_widget_registry(config,
                         bundled_dir: Path | None = None,
                         admin_dir: Path | None = None) -> WidgetRegistry:
    """Scan bundled + admin catalog dirs, return a WidgetRegistry.

    Admin tier overrides bundled on name collision. The `bundled_dir` and
    `admin_dir` params are for tests; production uses the defaults.
    """
    bundled_root = bundled_dir if bundled_dir is not None else _BUNDLED_DIR
    admin_root = (admin_dir if admin_dir is not None
                  else config.agent_path / "widgets")

    merged: dict[str, WidgetDescriptor] = {}
    merged.update(_scan_tier(bundled_root, "bundled"))
    admin = _scan_tier(admin_root, "admin")
    # Admin wins on collision.
    for name, desc in admin.items():
        if name in merged:
            log.info("admin widget %r overrides bundled", name)
        merged[name] = desc

    log.info("widget registry loaded: %d widget(s)", len(merged))
    return WidgetRegistry(merged)


# Module-level global registry — populated at startup.
_registry: WidgetRegistry | None = None


def get_widget_registry() -> WidgetRegistry | None:
    """Return the global widget registry, or None if not initialized."""
    return _registry


def init_widgets(config) -> WidgetRegistry:
    """Initialize the global widget registry. Idempotent; returns the registry."""
    global _registry
    _registry = load_widget_registry(config)
    return _registry


def _reset_registry_for_tests() -> None:
    """Test helper — clears the module-level singleton."""
    global _registry
    _registry = None


# ---------------------------------------------------------------------------
# Per-widget normalizers
#
# A normalizer is a pure function ``(input_data) -> normalized_data`` invoked
# AFTER successful schema validation. Used for server-controlled fields the
# agent shouldn't be trusted to author. iframe_sandbox uses it to wrap
# agent-supplied body content into a CSP-locked HTML document.
#
# Normalizers must be idempotent: ``normalize(normalize(d))`` should equal
# ``normalize(d)`` for the same input. They typically achieve this by
# regenerating derived fields from canonical source fields rather than
# preserving prior derived state.
# ---------------------------------------------------------------------------

_NORMALIZERS: dict[str, Callable[[dict], dict]] = {}


# Locked CSP for iframe_sandbox documents. ``default-src 'none'`` blocks all
# network and resource loading; ``script-src 'unsafe-inline'`` and
# ``style-src 'unsafe-inline'`` permit the agent's self-contained inline
# scripts and styles; ``img-src data:`` and ``font-src data:`` permit
# data-URI images and fonts so demos can embed assets without network.
_IFRAME_SANDBOX_CSP = (
    "default-src 'none'; "
    "style-src 'unsafe-inline'; "
    "script-src 'unsafe-inline'; "
    "img-src data:; "
    "font-src data:;"
)

_IFRAME_SANDBOX_BASE_STYLE = (
    "html,body{margin:0;padding:0;"
    "font-family:system-ui,-apple-system,Segoe UI,sans-serif;}"
)


def _normalize_iframe_sandbox(data: dict) -> dict:
    """Wrap agent-provided body into a CSP-locked HTML document.

    Input shape: ``{body: str, title?: str}`` (validated by the data_schema
    before this is called). Output shape: ``{body, title?, html}`` where
    ``html`` is the wrapped document the iframe consumes via ``srcdoc``.

    Idempotent: a stale ``html`` key in the input is overwritten by the
    regenerated wrapper, so a round-tripped value (e.g. via canvas_read →
    canvas_update) doesn't compound.
    """
    body = data.get("body", "")
    if not isinstance(body, str):
        body = ""
    title = data.get("title")
    title_tag = ""
    if isinstance(title, str) and title:
        # html.escape handles `<`, `>`, `&`, and quotes — sufficient inside
        # a <title> element where the only parser-meaningful sequence is
        # ``</title>``.
        title_tag = f"<title>{html_lib.escape(title)}</title>"
    wrapped = (
        '<!doctype html>'
        '<html>'
        '<head>'
        f'<meta http-equiv="Content-Security-Policy" content="{_IFRAME_SANDBOX_CSP}">'
        '<meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'{title_tag}'
        f'<style>{_IFRAME_SANDBOX_BASE_STYLE}</style>'
        '</head>'
        '<body>'
        f'{body}'
        '</body>'
        '</html>'
    )
    out = {"body": body, "html": wrapped}
    if isinstance(title, str) and title:
        out["title"] = title
    return out


_NORMALIZERS["iframe_sandbox"] = _normalize_iframe_sandbox
