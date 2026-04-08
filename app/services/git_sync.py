from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime


@dataclass
class SyncStatus:
    state: str = "synced"
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None
    pending_changes: int = 0
    branch: str = "main"
    head_commit: str = "local"


def utc_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class GitSyncService:
    """Minimal sync service contract for v1 endpoints.

    This implementation tracks status and can be replaced with real git ops.
    """

    def __init__(self) -> None:
        self._status = SyncStatus(last_success_at=utc_iso(), last_attempt_at=utc_iso())
        self._repo = ""
        self._token = ""

    def status(self) -> dict:
        return asdict(self._status)

    def configure(self, repo: str, token: str, branch: str) -> None:
        self._repo = repo
        self._token = token
        self._status.branch = branch

    def can_authenticate(self) -> tuple[bool, str]:
        if not self._repo:
            return False, "github_repo_missing"
        if not self._token:
            return False, "github_token_missing"
        return True, "ok"

    def mark_write(self) -> None:
        self._status.pending_changes = 0
        self._status.last_attempt_at = utc_iso()
        self._status.last_success_at = self._status.last_attempt_at
        self._status.state = "synced"

    def trigger_sync(self) -> dict:
        self._status.last_attempt_at = utc_iso()
        ok, reason = self.can_authenticate()
        if not ok:
            self._status.state = "error"
            self._status.last_error = reason
            return self.status()

        self._status.last_success_at = self._status.last_attempt_at
        self._status.last_error = None
        self._status.state = "synced"
        return self.status()
