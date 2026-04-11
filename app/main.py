from __future__ import annotations

import io
import os
from pathlib import Path

import qrcode
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.models.device import DeviceCreate, DeviceRecord, DeviceRecordUpdate, build_device_record, normalize_dsk, now_utc, validate_uniqueness_or_raise
from app.models.settings import HomeAssistantConfigTestRequest, LoginRequest, SettingsUpdateRequest, SetupBootstrapRequest, StoredSettings, hash_password, verify_password
from app.services.git_sync import GitSyncService
from app.services.home_assistant_sync import HomeAssistantSyncConfig, HomeAssistantSyncService, build_record_from_candidate, merge_candidate
from app.services.parser import extract_dsk
from app.storage.device_store import DeviceStore
from app.storage.settings_store import SettingsStore


APP_VERSION = "1.2.0"
DATA_DIR = Path(os.getenv("DATA_DIR", "./data/repo/devices"))
SETTINGS_FILE = Path(os.getenv("SETTINGS_FILE", "./data/settings/settings.json"))
store = DeviceStore(DATA_DIR)
settings_store = SettingsStore(SETTINGS_FILE)
sync = GitSyncService()
ha_sync = HomeAssistantSyncService()
app = FastAPI(title="QR Z-Wave Vault", version=APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def _require_auth(request: Request) -> None:
    if request.cookies.get("vault_user"):
        return
    raise HTTPException(status_code=401, detail="unauthorized")


def _setup_complete() -> bool:
    return settings_store.exists()


def _current_settings_or_404() -> StoredSettings:
    loaded = settings_store.load()
    if not loaded:
        raise HTTPException(status_code=404, detail="setup_incomplete")
    return loaded


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    path = request.url.path
    public_paths = {
        "/",
        "/health",
        "/api/v1/setup/status",
        "/api/v1/setup/bootstrap",
        "/api/v1/auth/login",
    }
    if path.startswith("/static") or path in public_paths:
        return await call_next(request)
    if path.startswith("/api/v1") and not request.cookies.get("vault_user"):
        return Response(status_code=401)
    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
@app.get("/profile", response_class=HTMLResponse)
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
        "setup_complete": _setup_complete(),
        "sync": sync.status(),
    }


@app.get("/api/v1/setup/status")
def setup_status() -> dict:
    return {"setup_complete": _setup_complete()}


@app.post("/api/v1/setup/bootstrap")
def setup_bootstrap(payload: SetupBootstrapRequest) -> dict:
    if _setup_complete():
        raise HTTPException(status_code=409, detail="setup_already_completed")
    salt, password_hash = hash_password(payload.password)
    settings = StoredSettings(
        username=payload.username,
        password_salt=salt,
        password_hash=password_hash,
        github_repo=payload.github_repo,
        github_token=payload.github_token,
        github_branch=payload.github_branch,
        ha_zwave_path="/api/nodes",
        ha_mode="ingress",
        ha_addon_slug="zwavejs2mqtt",
        request_timeout_seconds=10,
        retry_count=3,
    )
    settings_store.save(settings)
    sync.configure(settings.github_repo, settings.github_token, settings.github_branch)
    sync.trigger_sync()
    return {"ok": True, "settings": settings.masked()}


@app.post("/api/v1/auth/login")
def login(payload: LoginRequest, request: Request) -> dict:
    settings = _current_settings_or_404()
    if payload.username != settings.username:
        raise HTTPException(status_code=401, detail="invalid_credentials")
    if not verify_password(payload.password, settings.password_salt, settings.password_hash):
        raise HTTPException(status_code=401, detail="invalid_credentials")
    response = JSONResponse({"ok": True, "user": settings.username})
    response.set_cookie("vault_user", settings.username, httponly=True, samesite="lax")
    return response


@app.post("/api/v1/auth/logout")
def logout() -> Response:
    response = JSONResponse({"ok": True})
    response.delete_cookie("vault_user")
    return response


@app.get("/api/v1/auth/me")
def auth_me(request: Request) -> dict:
    _require_auth(request)
    settings = _current_settings_or_404()
    sync.configure(settings.github_repo, settings.github_token, settings.github_branch)
    return {"authenticated": True, "user": request.cookies.get("vault_user"), "settings": settings.masked(), "version": APP_VERSION}


@app.get("/api/v1/admin/settings")
def admin_settings(request: Request) -> dict:
    _require_auth(request)
    settings = _current_settings_or_404()
    return settings.masked()


@app.put("/api/v1/admin/settings")
def admin_settings_update(payload: SettingsUpdateRequest, request: Request) -> dict:
    _require_auth(request)
    settings = _current_settings_or_404()

    username = payload.username or settings.username
    if payload.new_password:
        salt, password_hash = hash_password(payload.new_password)
    else:
        salt = settings.password_salt
        password_hash = settings.password_hash

    updated = StoredSettings(
        username=username,
        password_salt=salt,
        password_hash=password_hash,
        github_repo=payload.github_repo or settings.github_repo,
        github_token=payload.github_token or settings.github_token,
        github_branch=payload.github_branch or settings.github_branch,
        ha_url=payload.ha_url if payload.ha_url is not None else settings.ha_url,
        ha_token=payload.ha_token if payload.ha_token is not None else settings.ha_token,
        ha_zwave_path=payload.ha_zwave_path or settings.ha_zwave_path,
        ha_verify_ssl=payload.ha_verify_ssl if payload.ha_verify_ssl is not None else settings.ha_verify_ssl,
        ha_mode=payload.ha_mode or settings.ha_mode,
        ha_addon_slug=payload.ha_addon_slug or settings.ha_addon_slug,
        zwave_base_url=payload.zwave_base_url if payload.zwave_base_url is not None else settings.zwave_base_url,
        zwave_api_token=payload.zwave_api_token if payload.zwave_api_token is not None else settings.zwave_api_token,
        request_timeout_seconds=payload.request_timeout_seconds or settings.request_timeout_seconds,
        retry_count=payload.retry_count if payload.retry_count is not None else settings.retry_count,
    )
    settings_store.save(updated)
    sync.configure(updated.github_repo, updated.github_token, updated.github_branch)
    return {"ok": True, "settings": updated.masked()}


@app.post("/api/v1/admin/test-repo-auth")
def admin_test_repo_auth(request: Request) -> dict:
    _require_auth(request)
    settings = _current_settings_or_404()
    sync.configure(settings.github_repo, settings.github_token, settings.github_branch)
    ok, reason = sync.can_authenticate()
    return {"ok": ok, "reason": reason}


@app.post("/api/v1/admin/force-pull-update")
def admin_force_pull_update(request: Request) -> dict:
    _require_auth(request)
    settings = _current_settings_or_404()
    sync.configure(settings.github_repo, settings.github_token, settings.github_branch)
    result = sync.trigger_sync()
    return {"ok": result.get("state") == "synced", "sync": result}


@app.post("/api/v1/admin/test-home-assistant-config")
def admin_test_home_assistant_config(request: Request, payload: HomeAssistantConfigTestRequest | None = None) -> dict:
    _require_auth(request)
    settings = _current_settings_or_404()
    config = HomeAssistantSyncConfig(
        mode=payload.ha_mode if payload and payload.ha_mode else settings.ha_mode,
        ha_base_url=payload.ha_url if payload and payload.ha_url is not None else settings.ha_url,
        ha_auth_token=payload.ha_token if payload and payload.ha_token is not None else settings.ha_token,
        addon_slug=payload.ha_addon_slug if payload and payload.ha_addon_slug else settings.ha_addon_slug,
        zwave_base_url=payload.zwave_base_url if payload and payload.zwave_base_url is not None else settings.zwave_base_url,
        zwave_api_token=payload.zwave_api_token if payload and payload.zwave_api_token is not None else settings.zwave_api_token,
        request_timeout_seconds=min(payload.request_timeout_seconds if payload and payload.request_timeout_seconds else settings.request_timeout_seconds, 15),
        retry_count=min(payload.retry_count if payload and payload.retry_count is not None else settings.retry_count, 1),
        verify_ssl=payload.ha_verify_ssl if payload and payload.ha_verify_ssl is not None else settings.ha_verify_ssl,
        zwave_path=payload.ha_zwave_path if payload and payload.ha_zwave_path else settings.ha_zwave_path,
    )
    if config.mode == "ingress" and (not config.ha_base_url or not config.ha_auth_token):
        return {"ok": False, "reason": "missing_home_assistant_config", "count": 0}
    if config.mode == "direct" and not config.zwave_base_url:
        return {"ok": False, "reason": "missing_zwave_base_url", "count": 0}
    ok, reason, count = ha_sync.test_config(config)
    return {"ok": ok, "reason": reason, "count": count}


@app.post("/api/v1/admin/preview-home-assistant-sync")
def admin_preview_home_assistant_sync(request: Request) -> dict:
    _require_auth(request)
    settings = _current_settings_or_404()
    config = HomeAssistantSyncConfig(
        mode=settings.ha_mode,
        ha_base_url=settings.ha_url,
        ha_auth_token=settings.ha_token,
        addon_slug=settings.ha_addon_slug,
        zwave_base_url=settings.zwave_base_url,
        zwave_api_token=settings.zwave_api_token,
        request_timeout_seconds=settings.request_timeout_seconds,
        retry_count=settings.retry_count,
        verify_ssl=settings.ha_verify_ssl,
        zwave_path=settings.ha_zwave_path,
    )
    if config.mode == "ingress" and (not config.ha_base_url or not config.ha_auth_token):
        raise HTTPException(status_code=400, detail="missing_home_assistant_config")
    if config.mode == "direct" and not config.zwave_base_url:
        raise HTTPException(status_code=400, detail="missing_zwave_base_url")

    candidates = ha_sync.fetch_nodes(config)
    existing = store.list_all()
    by_dsk = {item.dsk: item for item in existing if item.dsk}
    by_node_id = {item.zwave_node_id: item for item in existing if item.zwave_node_id}
    preview: list[dict] = []
    for candidate in candidates:
        try:
            normalized_dsk = normalize_dsk(candidate.dsk.replace(" ", "")) if candidate.dsk else None
        except ValueError:
            normalized_dsk = None
        # Match by DSK first, then by zwave_node_id
        current = by_dsk.get(normalized_dsk) if normalized_dsk else None
        if not current:
            current = by_node_id.get(candidate.node_id)
        if current:
            changes: list[dict] = []
            for field in ("device_name", "location", "description", "manufacturer", "model"):
                old_val = getattr(current, field, None) or ""
                new_val = getattr(candidate, field, None) or ""
                if new_val and new_val != old_val:
                    changes.append({"field": field, "old": old_val or None, "new": new_val})
            preview.append({
                "action": "update" if changes else "unchanged",
                "node_id": candidate.node_id,
                "device_name": candidate.device_name,
                "dsk": normalized_dsk,
                "existing_name": current.device_name,
                "changes": changes,
            })
        else:
            preview.append({
                "action": "new",
                "node_id": candidate.node_id,
                "device_name": candidate.device_name,
                "dsk": normalized_dsk,
                "location": candidate.location,
                "manufacturer": candidate.manufacturer,
                "model": candidate.model,
                "changes": [],
            })
    return {"ok": True, "preview": preview}


@app.post("/api/v1/admin/sync-from-home-assistant")
def admin_sync_from_home_assistant(request: Request, body: dict | None = None) -> dict:
    _require_auth(request)
    settings = _current_settings_or_404()
    selected_node_ids: set[str] | None = None
    if body and isinstance(body.get("node_ids"), list):
        selected_node_ids = {str(n) for n in body["node_ids"] if n}
    config = HomeAssistantSyncConfig(
        mode=settings.ha_mode,
        ha_base_url=settings.ha_url,
        ha_auth_token=settings.ha_token,
        addon_slug=settings.ha_addon_slug,
        zwave_base_url=settings.zwave_base_url,
        zwave_api_token=settings.zwave_api_token,
        request_timeout_seconds=settings.request_timeout_seconds,
        retry_count=settings.retry_count,
        verify_ssl=settings.ha_verify_ssl,
        zwave_path=settings.ha_zwave_path,
    )
    if config.mode == "ingress" and (not config.ha_base_url or not config.ha_auth_token):
        raise HTTPException(status_code=400, detail="missing_home_assistant_config")
    if config.mode == "direct" and not config.zwave_base_url:
        raise HTTPException(status_code=400, detail="missing_zwave_base_url")

    candidates = ha_sync.fetch_nodes(config)
    existing = store.list_all()
    by_dsk = {item.dsk: item for item in existing if item.dsk}
    by_node_id = {item.zwave_node_id: item for item in existing if item.zwave_node_id}
    created = 0
    updated = 0
    skipped = 0
    errors: list[dict] = []
    for candidate in candidates:
        try:
            if selected_node_ids is not None and candidate.node_id not in selected_node_ids:
                skipped += 1
                continue
            try:
                normalized_dsk = normalize_dsk(candidate.dsk.replace(" ", "")) if candidate.dsk else None
            except ValueError:
                normalized_dsk = None
            current = by_dsk.get(normalized_dsk) if normalized_dsk else None
            if not current:
                current = by_node_id.get(candidate.node_id)
            if current:
                merged = merge_candidate(current, candidate)
                store.update(merged)
                updated += 1
            else:
                record = build_record_from_candidate(candidate)
                validate_uniqueness_or_raise(record, store.uniqueness_indexes())
                store.create(record)
                created += 1
        except ValueError as exc:
            skipped += 1
            errors.append({"node_id": candidate.node_id, "error": str(exc)})
    if created or updated:
        sync.mark_write()
    return {
        "ok": True,
        "sync_direction": "home_assistant_to_vault",
        "results": {"created": created, "updated": updated, "skipped": skipped, "errors": errors},
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
    status = sync.status()
    items = [
        {
            **item.model_dump(mode="json"),
            "sync": {
                "state": status["state"],
                "last_success_at": status["last_success_at"],
                "last_error": status["last_error"],
                "head_commit": status["head_commit"],
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

    update_data = payload.model_dump(exclude_unset=True)
    # Normalize DSK if provided and non-empty, allow clearing to None
    if "dsk" in update_data:
        raw_dsk = update_data["dsk"]
        if raw_dsk and raw_dsk.strip():
            try:
                update_data["dsk"] = normalize_dsk(raw_dsk)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        else:
            update_data["dsk"] = None
    update_data["updated_at"] = now_utc()
    updated = current.model_copy(update=update_data)
    store.update(updated)
    sync.mark_write()
    return updated


@app.delete("/api/v1/devices", status_code=200)
def delete_all_devices(request: Request) -> dict:
    _require_auth(request)
    all_devices = store.list_all()
    count = 0
    for device in all_devices:
        store.delete(device.id)
        count += 1
    if count:
        sync.mark_write()
    return {"ok": True, "deleted": count}


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
