# Deployment — Session Notes

## Deliverables

- `deploy/decafclaw.service` — systemd user service unit
- `scripts/setup-vm.sh` — fresh Debian VM setup (Python, uv, Node.js, repo, systemd)
- `scripts/deploy.sh` — git pull + uv sync + restart
- `docs/deployment.md` — full deployment guide with troubleshooting

## Design decisions

- **systemctl --user** over system service — no root needed, user-scoped
- **loginctl enable-linger** for boot persistence without login
- **EnvironmentFile** loads .env directly — no need to duplicate config
- **Restart=on-failure** at systemd level + built-in auto-restart in the app = double resilience
- **git pull workflow** over rsync/ansible — simple, matches existing dev workflow
