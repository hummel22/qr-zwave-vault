from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path

from app.models.settings import StoredSettings

logger = logging.getLogger(__name__)

SETTINGS_SCHEMA_VERSION = 2


def _migrate(body: dict) -> dict:
    """Apply sequential migrations to bring stored settings up to date."""
    version = body.get("_schema_version", 1)

    if version < 2:
        # v2: added ha_mode, ha_addon_slug, zwave_base_url, zwave_api_token,
        #     request_timeout_seconds, retry_count, ha_zwave_path, ha_verify_ssl
        body.setdefault("ha_mode", "ingress")
        body.setdefault("ha_addon_slug", "zwavejs2mqtt")
        body.setdefault("zwave_base_url", None)
        body.setdefault("zwave_api_token", None)
        body.setdefault("request_timeout_seconds", 10)
        body.setdefault("retry_count", 3)
        body.setdefault("ha_zwave_path", "/api/nodes")
        body.setdefault("ha_verify_ssl", True)
        logger.info("Migrated settings from v1 to v2")

    body["_schema_version"] = SETTINGS_SCHEMA_VERSION
    return body


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
        version = body.get("_schema_version", 1)
        if version < SETTINGS_SCHEMA_VERSION:
            body = _migrate(body)
            self.file_path.write_text(json.dumps(body, indent=2))
            logger.info("Settings migrated and saved (v%d -> v%d)", version, SETTINGS_SCHEMA_VERSION)
        # Strip unknown keys so old/future fields don't crash the constructor
        known_fields = {f.name for f in dataclasses.fields(StoredSettings)}
        filtered = {k: v for k, v in body.items() if k in known_fields}
        return StoredSettings(**filtered)

    def save(self, settings: StoredSettings) -> StoredSettings:
        data = settings.__dict__.copy()
        data["_schema_version"] = SETTINGS_SCHEMA_VERSION
        self.file_path.write_text(json.dumps(data, indent=2))
        return settings
