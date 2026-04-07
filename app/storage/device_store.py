from __future__ import annotations

import json
import math
from pathlib import Path

from app.models.device import DeviceRecord, DeviceUniquenessIndexes


class DeviceStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, device_id: str) -> Path:
        return self.root / f"{device_id}.json"

    def list_all(self) -> list[DeviceRecord]:
        items: list[DeviceRecord] = []
        for file in sorted(self.root.glob("dev-*.json")):
            items.append(DeviceRecord.model_validate_json(file.read_text()))
        return items

    def uniqueness_indexes(self) -> DeviceUniquenessIndexes:
        items = self.list_all()
        return DeviceUniquenessIndexes(
            ids={item.id for item in items},
            raw_values={item.raw_value for item in items},
            dsks={item.dsk for item in items},
        )

    def create(self, record: DeviceRecord) -> DeviceRecord:
        self._path(record.id).write_text(record.model_dump_json(indent=2))
        return record

    def get(self, device_id: str) -> DeviceRecord | None:
        file = self._path(device_id)
        if not file.exists():
            return None
        return DeviceRecord.model_validate_json(file.read_text())

    def update(self, record: DeviceRecord) -> DeviceRecord:
        return self.create(record)

    def delete(self, device_id: str) -> bool:
        file = self._path(device_id)
        if not file.exists():
            return False
        file.unlink()
        return True

    def query(
        self,
        q: str | None,
        name: str | None,
        dsk: str | None,
        notes: str | None,
        sort: str,
        order: str,
        page: int,
        per_page: int,
    ) -> dict:
        items = self.list_all()

        def contains(v: str | None, needle: str) -> bool:
            return v is not None and needle.lower() in v.lower()

        if q:
            items = [
                i
                for i in items
                if contains(i.device_name, q)
                or contains(i.dsk, q.replace("-", ""))
                or contains(i.description, q)
            ]
        if name:
            items = [i for i in items if contains(i.device_name, name)]
        if dsk:
            ndsk = dsk.replace("-", "")
            items = [i for i in items if ndsk in i.dsk.replace("-", "")]
        if notes:
            items = [i for i in items if contains(i.description, notes)]

        sort_map = {
            "updated_at": lambda i: i.updated_at,
            "created_at": lambda i: i.created_at,
            "device_name": lambda i: i.device_name.lower(),
            "dsk": lambda i: i.dsk,
            "sync_state": lambda i: "synced",
        }
        items = sorted(items, key=sort_map[sort], reverse=order == "desc")
        total = len(items)
        start = (page - 1) * per_page
        paged = items[start : start + per_page]

        return {
            "items": paged,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total_items": total,
                "total_pages": max(1, math.ceil(total / per_page)) if total else 1,
            },
        }
