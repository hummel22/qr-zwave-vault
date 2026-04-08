from __future__ import annotations

import json
from pathlib import Path

from app.models.settings import StoredSettings


class SettingsStore:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.file_path.exists()

    def load(self) -> StoredSettings | None:
        if not self.file_path.exists():
            return None
        body = json.loads(self.file_path.read_text())
        return StoredSettings(**body)

    def save(self, settings: StoredSettings) -> StoredSettings:
        self.file_path.write_text(json.dumps(settings.__dict__, indent=2))
        return settings
