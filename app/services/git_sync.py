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

    def status(self) -> dict:
        return asdict(self._status)

    def mark_write(self) -> None:
        self._status.pending_changes = 0
        self._status.last_attempt_at = utc_iso()
        self._status.last_success_at = self._status.last_attempt_at
        self._status.state = "synced"

    def trigger_sync(self) -> dict:
        self._status.last_attempt_at = utc_iso()
        self._status.last_success_at = self._status.last_attempt_at
        self._status.state = "synced"
        return self.status()
