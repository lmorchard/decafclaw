# DecafClaw test-gate image.
#
# Purpose: run CI's `lint-and-test` command off-box, so the ~7430-test corpus
# stops running on a small host (where `-n auto` gets OOM-killed partway
# through). Keep the command in sync with .github/workflows CI rather than
# trusting this comment: the recipe must exercise what CI gates, not a
# convenient approximation of it.
#
# Verified on a 16-core / 64 GB host: 7425 passed, 5 skipped, ~54s.
# The same corpus needs ~240s at `-n 2` on a 3 GB / 4-core box, and dies at
# `-n auto` there.
#
# Every package below exists because a bare image produced failures that
# looked like code bugs. Measured: 33 failed / 15 errors from a bare image,
# 12 failed after git alone, 0 after this file.
#
#   git             -> tests/test_run_dev.py, test_conftest_hygiene.py,
#                      test_pytest_collection.py shell out to git.
#   tini            -> test_shell_output_collection.py::test_timeout_kills_process_group
#                      asserts a killed process group is fully reaped. With
#                      `sleep infinity` as PID 1 nothing reaps, orphans stay
#                      zombies, and os.kill(pid, 0) still succeeds. tini reaps.
#   openssh-client  -> contrib/skills/scavenger/test_cli.py shells out to `ssh`;
#                      without it 4 probe/env-cli tests fail with
#                      "[Errno 2] No such file or directory: 'ssh'".
#                      NOTE: a plain `apt-get install -y -qq openssh-client`
#                      SILENTLY NO-OPS on bookworm-slim without
#                      DEBIAN_FRONTEND=noninteractive, which looks exactly like
#                      the bug still being present.
#   uv-managed 3.13 -> NOT the base image's interpreter. The
#                      uv:python3.13-bookworm-slim base links SQLite 3.40.1, and
#                      sqlite-vec 0.1.7 rejects `LIMIT ?` (a bound parameter) on
#                      vec0 KNN queries under it -> 10 failures across
#                      test_embeddings.py and test_garden_audit_tools.py. uv's
#                      3.13.x build links SQLite 3.50.4 and all pass. Do not try
#                      to force this with PYSQLITE_*; install the uv python.
#
# CALLER NOTES (these are about the host, not the image):
#   - After syncing a *git worktree* into the workspace, its `.git` is a FILE
#     holding an absolute path on the origin machine, so it dangles remotely
#     and git-dependent tests fail. Replace it with a throwaway repo:
#       rm -f .git && git init -q . && git add -A \
#         && git -c user.email=a@b -c user.name=s commit -qm snapshot
#     and `git config --global --add safe.directory /workspace` if the checkout
#     is owned by another user.
#   - `uv python install` + `uv sync --frozen` is ~90s cold, then warm.
#   - If you edit this file and a host reuses the old image: image tags are
#     content-addressed on the Dockerfile digest (see recipe_image_tag), so an
#     edit produces a new tag and rebuilds. If a host still serves stale
#     packages, the check is running as the `scavenger` user in scavenger's
#     rootless podman namespace -- `podman rmi` as your own login user removes
#     *your* namespace's copy and leaves theirs intact. Evict it there:
#       scavenger_exec_native(host, 'podman rmi -f <tag>')
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

RUN DEBIAN_FRONTEND=noninteractive apt-get update -qq \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
        git tini ca-certificates openssh-client \
    && rm -rf /var/lib/apt/lists/*

# uv-managed CPython with SQLite 3.50.4; see the note above.
RUN uv python install 3.13

# Reaps orphaned processes so process-group teardown is observable.
ENTRYPOINT ["/usr/bin/tini", "--"]

WORKDIR /workspace
