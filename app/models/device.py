"""FastAPI/Pydantic model contract for device records.

This module enforces the schema contract defined in:
`docs/schema/device-record-v1.md`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


SCHEMA_VERSION = "1"
ID_PATTERN = re.compile(r"^dev-[a-f0-9]{20}$")


class DeviceRecord(BaseModel):
    """Canonical persisted device record."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: str = Field(default=SCHEMA_VERSION)
    id: str = Field(min_length=4, max_length=64)
    device_name: str = Field(min_length=1, max_length=120)
    raw_value: str = Field(min_length=1)
    dsk: str = Field(min_length=1)

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

    @field_validator("raw_value", "dsk")
    @classmethod
    def validate_non_blank_identity_field(cls, value: str, info: Any) -> str:
        if not value or not value.strip():
            raise ValueError(f"{info.field_name} must not be blank")
        return value


class DeviceRecordUpdate(BaseModel):
    """Mutable fields allowed in PATCH/PUT operations."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    device_name: str | None = Field(default=None, min_length=1, max_length=120)
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


def validate_uniqueness_or_raise(
    record: DeviceRecord,
    indexes: DeviceUniquenessIndexes,
) -> None:
    """Raise explicit errors when uniqueness constraints are violated.

    Intended usage in route/service layer:
      1. Build DeviceRecord from request body.
      2. Load normalized uniqueness indexes from persistence.
      3. Call this function before insert.
    """

    if record.id in indexes.ids:
        raise ValueError("id must be unique; value already exists")
    if record.raw_value in indexes.raw_values:
        raise ValueError("raw_value must be unique; value already exists")
    if record.dsk in indexes.dsks:
        raise ValueError("dsk must be unique; value already exists")


def validate_identity_immutability_or_raise(
    current: DeviceRecord,
    candidate: DeviceRecord,
) -> None:
    """Reject mutations of immutable identity fields on updates."""

    if current.id != candidate.id:
        raise ValueError("id is immutable and cannot be changed")
    if current.raw_value != candidate.raw_value:
        raise ValueError("raw_value is immutable and cannot be changed")
    if current.dsk != candidate.dsk:
        raise ValueError("dsk is immutable and cannot be changed")
