# Changelog

All notable changes to DecafClaw will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### ⚠️ Deployment Notes
<!--
When changes require manual pre- or post-deployment steps (e.g. systemd units,
migrations, new env vars, reindexing), document them here and add the git trailer:
  Deploy-Action: manual
  Deploy-Note: <short summary of action>
to the commit/PR. deploy_runner.py will refuse automated rollouts until acknowledged.
-->

### Added
- External deploy runner guard refusing auto-updates when commits carry `Deploy-Action: manual` (#1860 follow-up).
- CLI flag `--ack-manual` on `deploy_runner.py` allowing manual overrides.

---

## [0.14.0] - 2026-11-26

### ⚠️ Deployment Notes
- **Scope**: Post-deploy
- **Manual Actions Required**:
  ```bash
  # Install external deploy runner systemd service & timer
  cp deploy/decafclaw-deploy.{service,timer} ~/.config/systemd/user/
  systemctl --user daemon-reload
  systemctl --user enable --now decafclaw-deploy.timer
  ```
- **Context**: Issue #1647 / PR #1860 introduces an external runner to automatically poll, verify `/health`, and roll back broken deployments.

### Added
- External deploy runner script `scripts/deploy_runner.py` with flock, quiesced backup, fast-forward pull, and verify/rollback (#1860).
- Calendar view, editor, REST endpoints, and RRULE recurrence support (#1921, #1926, #1927).
- Native SSH execution and file copy tools with host approval rules (#1929, #1938, #1943, #1945).
- Compact sidebar section selector replacing button tabs (#1922, #1928).
- Host capacity gauges on web dashboard (#1924, #1925).
