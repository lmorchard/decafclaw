"""Token-based authentication for the web gateway."""

import json
import logging
import secrets
from pathlib import Path

log = logging.getLogger(__name__)

TOKEN_PREFIX = "dfc_"


def tokens_path(config) -> Path:
    """Path to the web tokens file (admin-managed, outside workspace)."""
    return config.agent_path / "web_tokens.json"


def _load_raw(config) -> dict[str, str]:
    """Load {token: username} mapping from disk."""
    path = tokens_path(config)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Could not read web tokens: {e}")
        return {}


def _save_raw(config, data: dict[str, str]) -> None:
    """Write {token: username} mapping to disk."""
    path = tokens_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def create_token(config, username: str) -> str:
    """Generate and store a token for a user. Returns the token."""
    token = f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    data = _load_raw(config)
    data[token] = username
    _save_raw(config, data)
    log.info(f"Created web token for user '{username}'")
    return token


def validate_token(config, token: str) -> str | None:
    """Validate a token. Returns the username or None."""
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    data = _load_raw(config)
    return data.get(token)


def revoke_token(config, token: str) -> bool:
    """Revoke a token. Returns True if found and removed."""
    data = _load_raw(config)
    if token not in data:
        return False
    del data[token]
    _save_raw(config, data)
    log.info("Revoked web token")
    return True


def list_tokens(config) -> list[dict]:
    """List all tokens with usernames. Returns [{token, username}]."""
    data = _load_raw(config)
    return [{"token": t, "username": u} for t, u in data.items()]


def get_current_user(request, config) -> str | None:
    """Extract and validate the current user from a request cookie.

    Returns the username or None if not authenticated.
    """
    token = request.cookies.get("decafclaw_session", "")
    if not token:
        return None
    return validate_token(config, token)


# -- CLI entry point -----------------------------------------------------------


def token_cli():
    """CLI: decafclaw-token create <username> | list | revoke <token>"""
    import argparse
    import sys

    from ..config import load_config

    parser = argparse.ArgumentParser(
        description="Manage DecafClaw web gateway tokens",
        prog="decafclaw-token",
    )
    sub = parser.add_subparsers(dest="command")

    create_cmd = sub.add_parser("create", help="Create a token for a user")
    create_cmd.add_argument("username", help="Username to associate with the token")

    sub.add_parser("list", help="List all tokens")

    revoke_cmd = sub.add_parser("revoke", help="Revoke a token")
    revoke_cmd.add_argument("token", help="Token to revoke")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    config = load_config()

    if args.command == "create":
        token = create_token(config, args.username)
        print(f"Token created for '{args.username}':")
        print(f"  {token}")

    elif args.command == "list":
        tokens = list_tokens(config)
        if not tokens:
            print("No tokens configured.")
        else:
            for t in tokens:
                # Show first 12 chars + masked rest
                preview = t["token"][:16] + "..."
                print(f"  {preview}  →  {t['username']}")

    elif args.command == "revoke":
        if revoke_token(config, args.token):
            print("Token revoked.")
        else:
            print("Token not found.")
            sys.exit(1)
