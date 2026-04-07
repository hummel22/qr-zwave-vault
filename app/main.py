from __future__ import annotations

import io
import os
from pathlib import Path

import qrcode
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from app.models.device import DeviceCreate, DeviceRecord, DeviceRecordUpdate, build_device_record, now_utc, validate_uniqueness_or_raise
from app.services.git_sync import GitSyncService
from app.services.parser import extract_dsk
from app.storage.device_store import DeviceStore


APP_VERSION = "1.0.0"
DATA_DIR = Path(os.getenv("DATA_DIR", "./data/repo/devices"))

store = DeviceStore(DATA_DIR)
sync = GitSyncService()
app = FastAPI(title="QR Z-Wave Vault", version=APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return Path("app/templates/index.html").read_text()


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "qr-zwave-vault",
        "version": APP_VERSION,
        "time": now_utc().isoformat().replace("+00:00", "Z"),
        "records": {"count": len(store.list_all())},
        "sync": sync.status(),
    }


@app.get("/api/v1/devices")
def list_devices(
    q: str | None = None,
    name: str | None = None,
    dsk: str | None = None,
    notes: str | None = None,
    sort: str = Query(default="updated_at", pattern="^(updated_at|created_at|device_name|dsk|sync_state)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=200),
) -> dict:
    queried = store.query(q, name, dsk, notes, sort, order, page, per_page)
    items = [
        {
            **item.model_dump(mode="json"),
            "sync": {
                "state": sync.status()["state"],
                "last_success_at": sync.status()["last_success_at"],
                "last_error": sync.status()["last_error"],
                "head_commit": sync.status()["head_commit"],
            },
        }
        for item in queried["items"]
    ]
    return {
        "items": items,
        "pagination": queried["pagination"],
        "sort": {"field": sort, "order": order},
        "applied_filters": {"q": q, "name": name, "dsk": dsk, "notes": notes},
    }


@app.post("/api/v1/devices", status_code=201)
def create_device(payload: DeviceCreate) -> DeviceRecord:
    try:
        dsk = extract_dsk(payload.raw_value)
        record = build_device_record(payload, dsk)
        validate_uniqueness_or_raise(record, store.uniqueness_indexes())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    created = store.create(record)
    sync.mark_write()
    return created


@app.get("/api/v1/devices/{device_id}")
def get_device(device_id: str) -> DeviceRecord:
    record = store.get(device_id)
    if not record:
        raise HTTPException(status_code=404, detail="not_found")
    return record


@app.put("/api/v1/devices/{device_id}")
def update_device(device_id: str, payload: DeviceRecordUpdate) -> DeviceRecord:
    current = store.get(device_id)
    if not current:
        raise HTTPException(status_code=404, detail="not_found")

    updated = current.model_copy(
        update={
            **payload.model_dump(exclude_unset=True),
            "updated_at": now_utc(),
        }
    )
    store.update(updated)
    sync.mark_write()
    return updated


@app.delete("/api/v1/devices/{device_id}", status_code=204)
def delete_device(device_id: str) -> Response:
    if not store.delete(device_id):
        raise HTTPException(status_code=404, detail="not_found")
    sync.mark_write()
    return Response(status_code=204)


@app.post("/api/v1/import")
def import_devices(payload: list[DeviceCreate]) -> dict:
    created = 0
    errors: list[dict] = []
    for idx, item in enumerate(payload):
        try:
            dsk = extract_dsk(item.raw_value)
            record = build_device_record(item, dsk)
            validate_uniqueness_or_raise(record, store.uniqueness_indexes())
            store.create(record)
            created += 1
        except ValueError as exc:
            errors.append({"index": idx, "error": str(exc)})
    sync.mark_write()
    return {"created": created, "errors": errors}


@app.get("/api/v1/devices/{device_id}/qr.png")
def device_qr(device_id: str) -> Response:
    record = store.get(device_id)
    if not record:
        raise HTTPException(status_code=404, detail="not_found")
    image = qrcode.make(record.raw_value)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.post("/api/v1/sync")
def trigger_sync() -> dict:
    return sync.trigger_sync()


@app.get("/api/v1/sync/status")
def sync_status() -> dict:
    return sync.status()
