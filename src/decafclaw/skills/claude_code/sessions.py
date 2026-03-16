"""Session manager for Claude Code — tracks active sessions and handles expiration."""

import logging
import time
from dataclasses import dataclass, field
from uuid import uuid4

log = logging.getLogger(__name__)


@dataclass
class Session:
    """A Claude Code session tied to a working directory."""
    session_id: str
    cwd: str
    description: str = ""
    model: str | None = None
    budget_usd: float = 2.0
    sdk_session_id: str | None = None  # from ResultMessage.session_id
    created_at: float = field(default_factory=time.monotonic)
    last_active: float = field(default_factory=time.monotonic)
    total_cost_usd: float = 0
    send_count: int = 0


class SessionManager:
    """Manages Claude Code session lifecycle with idle expiration."""

    def __init__(self, timeout_sec: int, budget_default: float, budget_max: float):
        self.sessions: dict[str, Session] = {}
        self.cwd_to_session: dict[str, str] = {}  # cwd -> session_id
        self.timeout_sec = timeout_sec
        self.budget_default = budget_default
        self.budget_max = budget_max

    def create(self, cwd: str, description: str = "",
               model: str | None = None, budget_usd: float | None = None) -> Session:
        """Create a new session. Raises ValueError if cwd already has an active session."""
        # Resolve and normalize path
        cwd = str(cwd).rstrip("/")

        # Check for existing active session at this cwd
        existing_id = self.cwd_to_session.get(cwd)
        if existing_id and self.get(existing_id) is not None:
            raise ValueError(
                f"A session already exists for {cwd} (id={existing_id[:8]}). "
                f"Use claude_code_send to continue it, or claude_code_stop to end it first."
            )

        # Clamp budget
        if budget_usd is None or budget_usd <= 0:
            budget = self.budget_default
        else:
            budget = min(budget_usd, self.budget_max)

        now = time.monotonic()
        session = Session(
            session_id=uuid4().hex[:12],
            cwd=cwd,
            description=description,
            model=model,
            budget_usd=budget,
            created_at=now,
            last_active=now,
        )
        self.sessions[session.session_id] = session
        self.cwd_to_session[cwd] = session.session_id
        log.info(f"Created Claude Code session {session.session_id} for {cwd}")
        return session

    def get(self, session_id: str) -> Session | None:
        """Get a session, or None if expired/not found. Lazy expiration check."""
        session = self.sessions.get(session_id)
        if session is None:
            return None

        # Check expiration
        if time.monotonic() - session.last_active > self.timeout_sec:
            log.info(f"Session {session_id} expired (idle > {self.timeout_sec}s)")
            self._remove(session_id)
            return None

        return session

    def touch(self, session_id: str) -> None:
        """Update last_active timestamp."""
        session = self.sessions.get(session_id)
        if session:
            session.last_active = time.monotonic()

    def stop(self, session_id: str) -> Session | None:
        """Remove and return a session. Returns None if not found."""
        session = self.sessions.get(session_id)
        if session is None:
            return None
        self._remove(session_id)
        return session

    def list_active(self) -> list[Session]:
        """Return all non-expired sessions."""
        now = time.monotonic()
        expired = [
            sid for sid, s in self.sessions.items()
            if now - s.last_active > self.timeout_sec
        ]
        for sid in expired:
            self._remove(sid)
        return list(self.sessions.values())

    def close_all(self) -> list[Session]:
        """Remove all sessions. Returns the removed sessions."""
        removed = list(self.sessions.values())
        self.sessions.clear()
        self.cwd_to_session.clear()
        return removed

    def _remove(self, session_id: str) -> None:
        """Remove a session from both indexes."""
        session = self.sessions.pop(session_id, None)
        if session:
            self.cwd_to_session.pop(session.cwd, None)
