"""FastAPI/Pydantic model contract for device records.

This module enforces the schema contract defined in:
`docs/schema/device-record-v1.md`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


SCHEMA_VERSION = "1"
ID_PATTERN = re.compile(r"^dev-[a-f0-9]{20}$")


class DeviceRecord(BaseModel):
    """Canonical persisted device record."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    schema_version: str = Field(default=SCHEMA_VERSION)
    id: str = Field(min_length=4, max_length=64)
    device_name: str = Field(min_length=1, max_length=120)
    raw_value: str = Field(min_length=1)
    dsk: str | None = Field(default=None)
    zwave_node_id: str | None = Field(default=None, max_length=20)

    location: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    manufacturer: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be '{SCHEMA_VERSION}'")
        return value

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not ID_PATTERN.match(value):
            raise ValueError("id must match pattern '^dev-[a-f0-9]{20}$'")
        return value

    @field_validator("raw_value")
    @classmethod
    def validate_non_blank_raw_value(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("raw_value must not be blank")
        return value


class DeviceCreate(BaseModel):
    """Create payload for devices."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    raw_value: str = Field(min_length=1)
    device_name: str = Field(min_length=1, max_length=120)
    location: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    manufacturer: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    metadata: dict[str, Any] | None = None


class DeviceRecordUpdate(BaseModel):
    """Mutable fields allowed in PATCH/PUT operations."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    device_name: str | None = Field(default=None, min_length=1, max_length=120)
    dsk: str | None = Field(default=None)
    location: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    manufacturer: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class DeviceUniquenessIndexes:
    """Pre-fetched normalized value indexes used for explicit uniqueness checks."""

    ids: set[str]
    raw_values: set[str]
    dsks: set[str]


def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def normalize_dsk(value: str) -> str:
    digits = "".join(ch for ch in value.strip() if ch.isdigit())
    if len(digits) != 40:
        raise ValueError("dsk must contain exactly 40 digits")
    return "-".join(digits[i : i + 5] for i in range(0, 40, 5))


def generate_device_id(raw_value: str, dsk: str | None) -> str:
    dsk_part = normalize_dsk(dsk) if dsk else "no-dsk"
    source = f"{raw_value.strip()}::{dsk_part}"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return f"dev-{digest[:20]}"


def build_device_record(payload: DeviceCreate, derived_dsk: str | None, zwave_node_id: str | None = None) -> DeviceRecord:
    normalized_dsk = normalize_dsk(derived_dsk) if derived_dsk else None
    created = now_utc()
    return DeviceRecord(
        schema_version=SCHEMA_VERSION,
        id=generate_device_id(payload.raw_value, normalized_dsk),
        device_name=payload.device_name,
        raw_value=payload.raw_value,
        dsk=normalized_dsk,
        zwave_node_id=zwave_node_id,
        location=payload.location,
        description=payload.description,
        manufacturer=payload.manufacturer,
        model=payload.model,
        created_at=created,
        updated_at=created,
        metadata=payload.metadata,
    )


def validate_uniqueness_or_raise(
    record: DeviceRecord,
    indexes: DeviceUniquenessIndexes,
) -> None:
    if record.id in indexes.ids:
        raise ValueError("id must be unique; value already exists")
    if record.raw_value in indexes.raw_values:
        raise ValueError("raw_value must be unique; value already exists")
    if record.dsk and record.dsk in indexes.dsks:
        raise ValueError("dsk must be unique; value already exists")
