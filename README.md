# QR Z-Wave Vault

QR Z-Wave Vault is a lightweight FastAPI service for storing, searching, and managing Z-Wave onboarding records (including DSKs and QR payloads) in a local, git-friendly data repository.

It includes:
- A browser UI at `/` for day-to-day use.
- A REST API under `/api/v1/*` for automation/integration.
- Local JSON-backed storage in `data/repo/devices` by default.
- Optional git sync hooks for commit/push workflows.

## Table of contents
- [Features](#features)
- [Architecture at a glance](#architecture-at-a-glance)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Running the app](#running-the-app)
- [Using the API](#using-the-api)
- [Project structure](#project-structure)
- [Development workflow](#development-workflow)
- [Testing](#testing)
- [Security assumptions](#security-assumptions)
- [Troubleshooting](#troubleshooting)
- [Roadmap docs](#roadmap-docs)
- [License](#license)

## Features

- **Device record lifecycle**: create, list/filter, update, delete device records.
- **DSK extraction/validation** from raw QR/onboarding value.
- **Pagination + sorting** on device listing endpoints.
- **QR image endpoint** to regenerate PNG from stored raw payload.
- **Import endpoint** for batch creation.
- **Health + sync status** endpoints.

## Architecture at a glance

- **API server**: FastAPI (`app/main.py`)
- **Domain models**: Pydantic models (`app/models`)
- **Persistence layer**: file-based store (`app/storage/device_store.py`)
- **Git sync service**: sync status and trigger wrapper (`app/services/git_sync.py`)
- **Frontend assets**: plain HTML/CSS/JS (`app/templates`, `app/static`)

## Requirements

- Python **3.12+**
- `pip` (or any PEP 517-compatible installer)
- Optional: `git` if you plan to use sync workflows

## Quick start

```bash
# 1) Clone
git clone <your-fork-or-repo-url>
cd qr-zwave-vault

# 2) Create a virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# 3) Install dependencies
pip install -e .

# Optional: install test dependencies
pip install -e .[test]

# 4) Start the service
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Then open:
- App UI: <http://localhost:8000>
- OpenAPI docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>

## Configuration

Environment variables:

- `DATA_DIR` (optional)
  - Default: `./data/repo/devices`
  - Controls where device records are stored.

Example:

```bash
export DATA_DIR=./data/repo/devices
uvicorn app.main:app --reload
```

## Running the app

### Local development

```bash
uvicorn app.main:app --reload
```

### Production-ish run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

> For production deployments, place the service behind a TLS-terminating reverse proxy and keep it inside trusted network boundaries.

## Using the API

### Create a device

```bash
curl -X POST http://localhost:8000/api/v1/devices \
  -H 'Content-Type: application/json' \
  -d '{
    "device_name": "Garage Lock",
    "raw_value": "90013278290000123456789012345678901234567890",
    "notes": "Installed near side door"
  }'
```

### List devices with search/sort/pagination

```bash
curl 'http://localhost:8000/api/v1/devices?q=garage&sort=updated_at&order=desc&page=1&per_page=25'
```

### Get QR PNG for a record

```bash
curl -o qr.png http://localhost:8000/api/v1/devices/<device_id>/qr.png
```

### Trigger sync

```bash
curl -X POST http://localhost:8000/api/v1/sync
```

For full API details, see `docs/api/v1.md` and `/docs` at runtime.

## Project structure

```text
app/
  main.py                  # FastAPI entrypoint + routes
  models/                  # Pydantic models and validation helpers
  services/                # Parser + sync service
  storage/                 # Device persistence implementation
  static/                  # CSS/JS assets
  templates/               # HTML templates
docs/
  api/v1.md                # API contract notes
  security/baseline.md     # Security baseline and assumptions
tests/                     # Unit/integration tests
README.md
pyproject.toml
```

## Development workflow

1. Create a branch.
2. Make changes.
3. Run tests (`pytest`).
4. Start local server and verify UI/API behavior.
5. Open a PR with a clear summary and testing notes.

## Testing

```bash
pytest
```

## Security assumptions

This project is designed for a trusted homelab environment with explicit trust boundaries:

- The internal LAN and host environment are assumed to be controlled by the operator.
- Untrusted clients and networks are outside the homelab trust boundary and must be treated as hostile.
- Administrative endpoints and secret-bearing workflows should remain reachable only from trusted segments.

### HTTPS requirement

All authenticated traffic must use HTTPS in transit.

- Do not deploy with plaintext HTTP for token-bearing or session-bearing requests.
- If TLS termination is handled by a reverse proxy, the proxy-to-app path must still be protected within the trusted network boundary.
- Cookies (if used) must be configured with `Secure` and aligned with the baseline in `docs/security/baseline.md`.

## Troubleshooting

- **`ModuleNotFoundError` on startup**: ensure virtualenv is active and reinstall with `pip install -e .`.
- **No records showing**: verify `DATA_DIR` points to the expected path.
- **Port already in use**: run with `--port <other-port>`.
- **Sync not progressing**: check sync status at `/api/v1/sync/status` and inspect git configuration/permissions.

## Roadmap docs

- Current plan: `docs/current-plan.md`
- Sync lifecycle: `docs/sync/git-sync-lifecycle.md`
- Data schema notes: `docs/schema/device-record-v1.md`

## License

Add a `LICENSE` file (for example MIT or Apache-2.0) before public open-source distribution.
