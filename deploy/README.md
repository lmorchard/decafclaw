# DecafClaw user systemd units

Reference copies of the units that run DecafClaw and its maintenance jobs under
the `lmorchard` user manager.

## Which copy actually runs

`~/.config/systemd/user/` is rendered by Ansible from the **homelab** repository
(`lmorchard-homelab/roles/decafclaw_host/templates/`). That role owns the live
files: an apply overwrites them, so **a unit edit made only here is lost on the
next `decafclaw.yml` run.**

The files here exist so this repository is self-describing, and so a change can
be reviewed beside the code it runs. **Change both, or accept that homelab wins.**

That already bit us. `StartLimitBurst=5` / `StartLimitIntervalSec=300` was added
here to stop a broken deploy crash-looping; the homelab template never had it, so
the live unit never did. The tell was size: live 526 bytes, this repo 641.

## The units

| Unit | Purpose |
|---|---|
| `decafclaw.service` | The agent daemon |
| `decafclaw-backup.timer` -> `decafclaw-backup.service` | Daily data backup (`scripts/backup.py`) |
| `decafclaw-deploy.timer` -> `decafclaw-deploy.service` | Deploy runner, every 5 minutes (`scripts/deploy_runner.py`) |

`marimo.service` and `com.television.server.service` are homelab-only and are not
part of this application.

## Backup destination

Backups go to `/mnt/decafclaw-backups`, an NFS mount from Castiel, deliberately
outside `/home`. It used to be `~/.decafclaw/decafclaw/backups`, inside the tree
the agent walks recursively; a `hard` NFS stall there froze the host hard enough
to refuse SSH (2026-11-26), because a process in uninterruptible NFS wait holds
no CPU, negligible memory and few tasks, so every cgroup cap passes and `kill`
cannot reach it.

`scripts/backup.sh` defaults `BACKUP_DIR` to the same path so no caller falls
back to the old location.

## Installing by hand

Only for a host Ansible does not manage:

```sh
install -m 0664 decafclaw*.service decafclaw*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now decafclaw-backup.timer decafclaw-deploy.timer
```
