# Z-Wave QR Code Store — Implementation Plan (Current)

Created: 2026-04-07  
Status: Draft (implementation-ready)

## 1) Scope (v1)
Build a self-hosted Dockerized web app that stores Z-Wave SmartStart/S2 QR payloads as one JSON file per device in a Git-backed repository, with two-way sync and a lightweight UI.

### In scope
- FastAPI backend + vanilla JS frontend
- CRUD for QR entries
- Add via text input, camera scan, and bulk JSON import
- Per-device JSON file storage (`devices/{id}.json`)
- Git sync (clone/pull/push via `subprocess`)
- Server-side QR image generation for display
- Basic authentication (or token-based, to be finalized)
- Health endpoint with sync + record status
- Docker image and `docker-compose` for self-hosting

### Out of scope (for now)
- CI/CD pipeline specifics
- Local infrastructure automation
- Deep HA entity linking (unless later enabled)

---

## 2) Architecture

- **Backend:** Python 3.12 + FastAPI + Uvicorn
- **Frontend:** Server-served HTML/CSS/vanilla JS
- **Camera scanning:** `qr-scanner` (Nimiq) in browser via WebRTC
- **Storage model:** Git repo as database, file-per-record JSON
- **QR generation:** `python-qrcode`
- **Git operations:** shelling out to `git` with `subprocess.run`

Directory model:

- App runtime repo mount: `/data/repo`
- Data files: `/data/repo/devices/*.json`

---

## 3) Data Contract (v1)

Each device record is a single JSON document:

```json
{
  "schema_version": 1,
  "id": "deterministic-device-id",
  "raw_value": "<full numeric QR string>",
  "device_name": "Living Room Dimmer",
  "dsk": "12345-67890-12345-67890-12345-67890-12345-67890",
  "manufacturer": "0xXXXX",
  "product_id": "0xYYYY",
  "requested_keys": ["S2_AUTHENTICATED", "S2_UNAUTHENTICATED"],
  "date_added": "2026-04-07T00:00:00Z",
  "notes": "optional"
}
```

Conventions:
- File path: `devices/{id}.json`
- `id` is stable and independent from display name
- `dsk` stored formatted in 5-digit groups

---

## 4) Z-Wave Parsing Plan

Implement a Python port of the `zwave-js/qr` reference parser behavior.

Parser outputs (minimum):
- `raw_value`
- formatted `dsk`
- requested security keys
- manufacturer/product (if TLV present)
- structured parse errors for invalid payloads

Validation/parity plan:
- Build fixture set with valid + invalid QR payloads
- Compare parsed output against expected behavior from reference logic

---

## 5) Git Sync Lifecycle

### Startup
- If `/data/repo/.git` missing: clone target repo
- Else: `git pull --rebase`

### Write path (add/edit/delete)
1. `git pull --rebase`
2. Apply file changes under `devices/`
3. `git add`
4. `git commit -m "..."`
5. `git push`

### Background sync
- Interval pull every `SYNC_INTERVAL` seconds (default 300)

### Conflict/failure behavior
- On rebase failure: stash → pull/rebase → re-apply → retry push
- If unresolved, mark status as `conflicted` and surface in API/UI

---

## 6) API Surface (v1)

- `GET /health` — service health + sync status + count
- `GET /api/v1/devices` — list/search records
- `POST /api/v1/devices` — create record from raw QR string + metadata
- `GET /api/v1/devices/{id}` — device detail
- `PUT /api/v1/devices/{id}` — update metadata/raw value
- `DELETE /api/v1/devices/{id}` — remove record
- `POST /api/v1/import` — bulk import JSON array
- `GET /api/v1/devices/{id}/qr.png` — render QR image
- `POST /api/v1/sync` — manual sync trigger
- `GET /api/v1/sync/status` — sync diagnostics

---

## 7) UI Plan (v1)

Pages/components:
- Dashboard list/grid with search (name, DSK, notes)
- Add flow:
  - manual raw string input
  - camera scan using `qr-scanner`
  - bulk import upload
- Detail view:
  - rendered QR image
  - parsed DSK and metadata
  - copy-to-clipboard actions
- Settings:
  - repo URL/branch/token source
  - sync interval
- Sync status indicator + manual sync button

---

## 8) Security/Auth (v1)

- Initial mode: basic auth or static token auth (final selection pending)
- Require HTTPS when using camera scanning in browsers (except localhost)
- Mask secrets in logs and status responses
- Store secrets via environment variables

Planned env vars:
- `GITHUB_REPO`
- `GITHUB_TOKEN`
- `GITHUB_BRANCH` (default `main`)
- `SYNC_INTERVAL` (default `300`)
- `APP_PORT` (default `8000`)
- `AUTH_*` (finalized with auth mode)

---

## 9) Docker/Runtime Plan

- Base image: `python:3.12-slim`
- Multi-stage build (builder/runtime)
- Runtime includes `git`
- Run as non-root
- Persistent volume for `/data/repo`

Expected image size target: ~150–200 MB.

---

## 10) Open Decisions (to finalize)

1. Deployment target: mini host vs dedicated Proxmox LXC
2. GitHub token model: existing token vs fine-grained/service account
3. HA integration level: standalone vs optional import/linking
4. Auth mode: basic auth vs SSO/Authelia fronting
5. Mobile camera support requirement (recommended: yes)

---

## 11) Execution Backlog (ordered)

1. Scaffold FastAPI app structure + config loader
2. Define pydantic models and JSON schema validation
3. Implement git sync module (startup/pull/push/status)
4. Implement Z-Wave parser module + fixture tests
5. Implement CRUD/import/sync/health endpoints
6. Implement frontend dashboard/add/detail/settings pages
7. Integrate `qr-scanner` camera flow
8. Add QR render endpoint + UI display
9. Add authentication middleware
10. Add Dockerfile + docker-compose
11. End-to-end validation: add → commit/push → fresh pull instance
