"""Every import specifier in a browser-served module must resolve on disk.

`static/` is served verbatim by `StaticFiles` — there is no bundler and no
transpile step for app code. So a specifier is only loadable if a file with
exactly that name exists. A single unresolvable specifier is not a localized
failure: the browser aborts the whole module subgraph, so one bad leaf seven
imports deep takes down the entire web UI.

That shipped. PR #825 generated a TypeScript OpenAPI client into `static/`
and pointed `auth-client.js` at `./api-client/index.js`, but only
`index.ts` was ever emitted (`tsconfig.json` is `noEmit: true` — `make
check-js` type-checks, it never builds). `app.js` imports `auth-client.js`
on its 7th line, so the entry module never evaluated and the client was
dead for three days.

`make check` could not catch it. `moduleResolution: "bundler"` makes tsc
rewrite the specifier `./api-client/index.js` to `index.ts` and call it
valid — the typechecker was modelling a build step that does not exist.

Test files are excluded: they are never served, and vitest resolves things
the browser cannot (`?raw` suffixes, bare JSON imports).
"""

import pathlib
import re

STATIC_DIR = (
    pathlib.Path(__file__).resolve().parent.parent
    / "src"
    / "decafclaw"
    / "web"
    / "static"
)

# Directories holding files that are not hand-written app modules: `vendor/`
# is esbuild output (`make vendor`) and `node_modules/` is npm's.
EXCLUDED_DIRS = {"node_modules", "vendor"}

# `from '…'`, side-effect `import '…'`, and dynamic `import('…')`. Only
# single- and double-quoted literals — a template literal is not statically
# resolvable, so there is nothing to check.
_SPECIFIER_RE = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(?\s*)['"]([^'"]+)['"]"""
)


def _served_modules() -> list[pathlib.Path]:
    return [
        path
        for path in sorted(STATIC_DIR.rglob("*.js"))
        if not EXCLUDED_DIRS & set(path.relative_to(STATIC_DIR).parts)
        and not path.name.endswith(".test.js")
    ]


def _specifiers(path: pathlib.Path) -> list[tuple[int, str]]:
    found = []
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        for match in _SPECIFIER_RE.finditer(line):
            found.append((lineno, match.group(1)))
    return found


def _resolve(source: pathlib.Path, specifier: str) -> pathlib.Path | None:
    """Where the browser would look, or None if it wouldn't look on disk.

    Bare specifiers (`lit`, `@milkdown/kit`) go through the import map in
    `index.html` rather than the filesystem, so they are out of scope here.
    """
    target = specifier.split("?")[0].split("#")[0]
    if target.startswith("/static/"):
        return (STATIC_DIR / target[len("/static/") :]).resolve()
    if target.startswith("./") or target.startswith("../"):
        return (source.parent / target).resolve()
    return None


def test_served_module_specifiers_resolve_on_disk():
    broken = []
    for module in _served_modules():
        for lineno, specifier in _specifiers(module):
            resolved = _resolve(module, specifier)
            if resolved is None:
                continue
            rel = module.relative_to(STATIC_DIR)
            # A specifier that climbs out of the mount is unreachable even if
            # the file exists. `..` does not mean the same thing on both sides:
            # the browser clamps at the URL root, so `../../../foo.js` from
            # `/static/lib/` requests `/foo.js` — off the mount, always a 404 —
            # while `pathlib` keeps walking up into the repo and could land on
            # a real file. Checking containment keeps the two from diverging.
            if not resolved.is_relative_to(STATIC_DIR):
                broken.append(
                    f"  {rel}:{lineno} imports {specifier!r} — climbs outside "
                    "static/, so the browser requests a URL off the mount"
                )
            elif not resolved.is_file():
                broken.append(f"  {rel}:{lineno} imports {specifier!r} — no such file")

    assert not broken, (
        "Browser-served modules import files the browser cannot fetch. It "
        "aborts the whole subgraph on any of these, so each one can blank the "
        "web UI:\n" + "\n".join(broken)
    )


def test_guard_covers_the_entry_module():
    """A resolution bug in `app.js` is the one that blanks the whole UI.

    If `app.js` ever drops out of the scanned set, the test above still
    passes while covering nothing that matters.
    """
    assert STATIC_DIR / "app.js" in _served_modules()
