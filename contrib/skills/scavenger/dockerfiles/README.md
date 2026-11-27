# Bundled Scavenger Dockerfiles

Standard container recipes bundled with the `scavenger` skill for common compute-intensive workloads.

## Available Recipes

- **`android-builder.Dockerfile`**: Android SDK 35 (`android-35`, `build-tools;35.0.0`), OpenJDK 17, Node.js 22 LTS, and Gradle build toolchain for React Native / Expo Continuous Native Generation (CNG).
- **`node-dev.Dockerfile`**: Node.js 22 LTS Bookworm with standard C/C++ compilation tools (`build-essential`, `python3`, `git`) for compiling native npm modules.
- **`rust-builder.Dockerfile`**: Rust stable with `cargo-nextest` and `sccache` for accelerated compilation.
- **`decafclaw-test.Dockerfile`**: Runner for DecafClaw's own `lint-and-test` gate. Carries git, tini (orphan reaping for process-group tests), openssh-client, and a uv-managed CPython whose SQLite handles sqlite-vec's bound-`LIMIT` KNN queries. Intended for hosts too small to run the full corpus locally. Read the file header before editing: every package in it exists because a bare image produced a failure that looked like a code bug.
