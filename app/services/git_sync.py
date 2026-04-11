from __future__ import annotations

import io
import logging
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import qrcode

logger = logging.getLogger(__name__)


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
    """Git sync service that commits and pushes device data to GitHub.

    Manages a git repo in the data directory. On each write, generates
    QR code images alongside JSON files, commits, and pushes.
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        self._status = SyncStatus(last_success_at=utc_iso(), last_attempt_at=utc_iso())
        self._repo = ""
        self._token = ""
        self._data_dir = data_dir

    def status(self) -> dict:
        s = asdict(self._status)
        if self._data_dir:
            s["head_commit"] = self._get_head_commit()
        return s

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

    def _auth_url(self) -> str:
        """Build authenticated HTTPS URL from repo and token."""
        repo = self._repo.rstrip("/")
        if repo.startswith("git@github.com:"):
            repo = repo.replace("git@github.com:", "https://github.com/")
        if repo.endswith(".git"):
            repo = repo[:-4]
        if not repo.startswith("https://"):
            repo = f"https://github.com/{repo}"
        # Insert token for auth: https://TOKEN@github.com/owner/repo.git
        return repo.replace("https://", f"https://x-access-token:{self._token}@") + ".git"

    def _run_git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """Run a git command in the data directory."""
        cmd = ["git", "-C", str(self._data_dir), *args]
        env = {
            "GIT_AUTHOR_NAME": "QR Z-Wave Vault",
            "GIT_AUTHOR_EMAIL": "vault@localhost",
            "GIT_COMMITTER_NAME": "QR Z-Wave Vault",
            "GIT_COMMITTER_EMAIL": "vault@localhost",
            "GIT_TERMINAL_PROMPT": "0",
        }
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env={**__import__("os").environ, **env}, check=False)
        if check and result.returncode != 0:
            logger.error("git %s failed: %s", " ".join(args), result.stderr.strip())
            raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()}")
        return result

    def _is_git_repo(self) -> bool:
        """Check if data dir is the root of its own git repo (not a subdirectory of a parent repo)."""
        if not self._data_dir:
            return False
        result = self._run_git("rev-parse", "--show-toplevel", check=False)
        if result.returncode != 0:
            return False
        git_root = Path(result.stdout.strip()).resolve()
        return git_root == self._data_dir.resolve()

    def _get_head_commit(self) -> str:
        if not self._data_dir or not self._is_git_repo():
            return "local"
        result = self._run_git("rev-parse", "--short", "HEAD", check=False)
        return result.stdout.strip() if result.returncode == 0 else "local"

    def _ensure_repo(self) -> bool:
        """Ensure data directory is a git repo. Clone or init as needed."""
        if not self._data_dir:
            return False
        ok, _ = self.can_authenticate()
        if not ok:
            return False

        if self._is_git_repo():
            # Ensure remote is configured correctly
            self._run_git("remote", "set-url", "origin", self._auth_url(), check=False)
            return True

        # Try to clone into the data dir
        self._data_dir.mkdir(parents=True, exist_ok=True)
        existing_files = list(self._data_dir.glob("*"))

        if existing_files:
            # Data dir has files but no git repo — init and push
            self._run_git("init", "-b", self._status.branch)
            self._run_git("remote", "add", "origin", self._auth_url())
            # Try to pull remote first
            pull_result = self._run_git("pull", "origin", self._status.branch, "--allow-unrelated-histories", check=False)
            if pull_result.returncode != 0:
                logger.info("No remote branch yet or pull failed, will push fresh: %s", pull_result.stderr.strip())
            return True
        else:
            # Empty dir — try clone
            clone_result = subprocess.run(
                ["git", "clone", "-b", self._status.branch, self._auth_url(), str(self._data_dir)],
                capture_output=True, text=True, timeout=30, check=False,
            )
            if clone_result.returncode == 0:
                return True
            # Clone failed (maybe empty repo) — init fresh
            logger.info("Clone failed, initializing fresh repo: %s", clone_result.stderr.strip())
            self._run_git("init", "-b", self._status.branch)
            self._run_git("remote", "add", "origin", self._auth_url())
            return True

    def _generate_qr_images(self) -> int:
        """Generate QR PNG images for all device JSON files that have raw_value."""
        if not self._data_dir:
            return 0
        import json
        count = 0
        for json_file in self._data_dir.glob("dev-*.json"):
            try:
                data = json.loads(json_file.read_text())
                raw_value = data.get("raw_value", "")
                if not raw_value:
                    continue
                png_path = json_file.with_suffix(".png")
                image = qrcode.make(raw_value)
                buf = io.BytesIO()
                image.save(buf, format="PNG")
                png_path.write_bytes(buf.getvalue())
                count += 1
            except Exception as exc:
                logger.warning("Failed to generate QR for %s: %s", json_file.name, exc)
        return count

    def _cleanup_orphan_images(self) -> None:
        """Remove PNG files that don't have a matching JSON file."""
        if not self._data_dir:
            return
        for png_file in self._data_dir.glob("dev-*.png"):
            json_file = png_file.with_suffix(".json")
            if not json_file.exists():
                png_file.unlink()
                logger.info("Removed orphan QR image: %s", png_file.name)

    def mark_write(self) -> None:
        """Commit and push changes after a data mutation."""
        self._status.last_attempt_at = utc_iso()
        self._status.pending_changes = 0

        if not self._data_dir:
            self._status.state = "synced"
            self._status.last_success_at = self._status.last_attempt_at
            return

        try:
            if not self._ensure_repo():
                self._status.state = "synced"
                self._status.last_success_at = self._status.last_attempt_at
                return

            # Generate QR images and clean up orphans
            self._generate_qr_images()
            self._cleanup_orphan_images()

            # Stage all changes
            self._run_git("add", "-A")

            # Check if there are changes to commit
            status_result = self._run_git("status", "--porcelain", check=False)
            if not status_result.stdout.strip():
                self._status.state = "synced"
                self._status.last_success_at = self._status.last_attempt_at
                return

            # Commit
            self._run_git("commit", "-m", f"vault sync {utc_iso()}")

            # Push
            self._run_git("push", "-u", "origin", self._status.branch)

            self._status.state = "synced"
            self._status.last_success_at = self._status.last_attempt_at
            self._status.last_error = None
            self._status.head_commit = self._get_head_commit()
            logger.info("Git sync completed: %s", self._status.head_commit)
        except Exception as exc:
            self._status.state = "error"
            self._status.last_error = str(exc)
            logger.error("Git sync failed: %s", exc)

    def trigger_sync(self) -> dict:
        """Manual sync trigger — pull then push."""
        self._status.last_attempt_at = utc_iso()
        ok, reason = self.can_authenticate()
        if not ok:
            self._status.state = "error"
            self._status.last_error = reason
            return self.status()

        try:
            if not self._ensure_repo():
                self._status.state = "error"
                self._status.last_error = "failed_to_init_repo"
                return self.status()

            # Pull latest
            self._run_git("pull", "origin", self._status.branch, "--rebase", check=False)

            self._status.last_success_at = self._status.last_attempt_at
            self._status.last_error = None
            self._status.state = "synced"
            self._status.head_commit = self._get_head_commit()
        except Exception as exc:
            self._status.state = "error"
            self._status.last_error = str(exc)
            logger.error("Trigger sync failed: %s", exc)

        return self.status()

    def force_pull(self) -> dict:
        """Force pull from remote, overwriting local changes."""
        self._status.last_attempt_at = utc_iso()
        ok, reason = self.can_authenticate()
        if not ok:
            self._status.state = "error"
            self._status.last_error = reason
            return self.status()

        try:
            if not self._ensure_repo():
                self._status.state = "error"
                self._status.last_error = "failed_to_init_repo"
                return self.status()

            self._run_git("fetch", "origin", self._status.branch)
            self._run_git("reset", "--hard", f"origin/{self._status.branch}")

            self._status.last_success_at = self._status.last_attempt_at
            self._status.last_error = None
            self._status.state = "synced"
            self._status.head_commit = self._get_head_commit()
        except Exception as exc:
            self._status.state = "error"
            self._status.last_error = str(exc)
            logger.error("Force pull failed: %s", exc)

        return self.status()
