"""``curl`` read-only contract (#1641).

Contract: a remote HEAD request (``-I``/``--head``) to an ``http(s)`` URL. A
GET, an output file, a request body, a config file, a credential material
file, or an ``@file`` value all leave that contract. ``@``-prefixed values are
refused wholesale because curl reads them as local files, which can send
workspace or system data outbound. Path arguments still have to resolve inside
the workspace.
"""

import re
from pathlib import Path

from ..containment import paths_contained

VERBS = frozenset({"curl"})

_CURL_WRITE_SHORT_FLAGS = frozenset("oODcTdFKXnE")
_CURL_WRITE_LONG_FLAGS = (
    "--output",
    "--output-dir",
    "--remote-name",
    "--remote-header-name",
    "--dump-header",
    "--cookie-jar",
    "--upload-file",
    "--data",
    "--data-ascii",
    "--data-binary",
    "--data-raw",
    "--data-urlencode",
    "--form",
    "--form-string",
    "--config",
    "--request",
    "--json",
    "--trace",
    "--trace-ascii",
    "--stderr",
    "--libcurl",
    "--etag-save",
    "--hsts",
    "--alt-svc",
    # Sends ~/.netrc credentials to the URL; a silent HEAD must not.
    "--netrc",
    "--netrc-optional",
    "--sslkeylogfile",
    "--ssl-keylogfile",
    # Read a local credential/material file and send it (or load code).
    "--cert",
    "--key",
    "--cacert",
    "--capath",
    "--crlfile",
    "--netrc-file",
    "--proxy-cert",
    "--proxy-key",
    "--proxy-cacert",
    "--engine",
)

_URL_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


def _curl_read_only(args: list[str]) -> bool:
    head_request = False
    urls: list[str] = []
    for token in args:
        # `-H @file`, `-b @file`, and friends read a local file into the
        # request. That can send workspace or system data outbound, so the
        # read-only tier refuses @-prefixed values entirely.
        if token.startswith("@") or "=@" in token:
            return False
        if token in ("-I", "--head"):
            head_request = True
            continue
        if token.startswith("--"):
            name, _, value = token.partition("=")
            if name in _CURL_WRITE_LONG_FLAGS:
                return False
            if value and "://" in value:
                urls.append(value)
            continue
        if token.startswith("-") and token != "-":
            if any(flag in _CURL_WRITE_SHORT_FLAGS for flag in token[1:]):
                return False
            continue
        if "://" in token:
            urls.append(token)
    if not head_request or not urls:
        return False
    return all(_URL_SCHEME_RE.match(url) for url in urls)


def allows(verb: str, args: list[str], cwd: Path, workspace: Path) -> bool:
    if not _curl_read_only(args):
        return False
    return paths_contained(args, cwd, workspace)
