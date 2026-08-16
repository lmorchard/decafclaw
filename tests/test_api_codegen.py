"""`make gen-api-client` produces the generated OpenAPI client tree.

This asserted `lib/api-client.ts` until #843. That path was a zero-byte file
the Makefile `touch`ed into place purely to satisfy this assertion — the
codegen tool emits a *directory* of `.ts` files, never a single module. So
the test passed while checking an artifact the build fabricated for it, and
covered none of the real output.
"""

import pathlib
import re
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CLIENT_DIR = REPO_ROOT / "src" / "decafclaw" / "web" / "static" / "lib" / "api-client"

# Require the `from` / `import` context rather than matching `api-client/`
# anywhere, so a comment or doc string mentioning the path is not a "reference".
_API_CLIENT_IMPORT_RE = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(?\s*)['"][^'"]*api-client/[^'"]*['"]"""
)


def test_gen_api_client_emits_the_client_tree():
    result = subprocess.run(
        ["make", "gen-api-client"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"make gen-api-client failed: {result.stderr}"

    for relative in ("index.ts", "core/request.ts", "services/DefaultService.ts"):
        assert (CLIENT_DIR / relative).is_file(), f"codegen did not emit {relative}"


def test_generated_client_is_not_imported_by_served_code():
    """The client is `.ts` and nothing compiles it, so importing it 404s.

    PR #825 pointed `auth-client.js` at `./api-client/index.js`, which was
    never emitted — that aborted the `app.js` module subgraph and blanked the
    whole web UI. Until #843 adds an emit step, browser-served code must not
    import this tree. `tests/test_web_static_module_graph.py` is the general
    guard; this one names the specific trap so re-adding the import fails
    with the reason attached.
    """
    static_dir = REPO_ROOT / "src" / "decafclaw" / "web" / "static"
    importers = [
        path.relative_to(static_dir)
        for path in static_dir.rglob("*.js")
        if "node_modules" not in path.parts
        and "vendor" not in path.parts
        # `*.test.js` is never served, and vitest transpiles TS, so a unit test
        # importing this tree is legitimate — matching the exclusion in
        # `tests/test_web_static_module_graph.py`.
        and not path.name.endswith(".test.js")
        and _API_CLIENT_IMPORT_RE.search(path.read_text())
    ]
    assert not importers, (
        "These browser-served modules reference the generated api-client, which "
        f"has no compiled .js and will 404: {importers}. See #843."
    )
