# Bundled Scavenger Dockerfiles

Standard container recipes bundled with the `scavenger` skill for common compute-intensive workloads.

## Available Recipes

- **`android-builder.Dockerfile`**: Android SDK 35 (`android-35`, `build-tools;35.0.0`), OpenJDK 17, Node.js 22 LTS, and Gradle build toolchain for React Native / Expo Continuous Native Generation (CNG).
- **`node-dev.Dockerfile`**: Node.js 22 LTS Bookworm with standard C/C++ compilation tools (`build-essential`, `python3`, `git`) for compiling native npm modules.
- **`rust-builder.Dockerfile`**: Rust stable with `cargo-nextest` and `sccache` for accelerated compilation.
