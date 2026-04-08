from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _clean() -> None:
    root = Path("data/repo/devices")
    root.mkdir(parents=True, exist_ok=True)
    for f in root.glob("dev-*.json"):
        f.unlink()

    settings = Path("data/settings/settings.json")
    if settings.exists():
        settings.unlink()


def _setup_and_login() -> None:
    setup_payload = {
        "username": "admin",
        "password": "supersecure",
        "github_repo": "https://github.com/example/repo",
        "github_token": "ghp_test_token_123456",
        "github_branch": "main",
    }
    setup = client.post("/api/v1/setup/bootstrap", json=setup_payload)
    assert setup.status_code == 200

    logged_in = client.post("/api/v1/auth/login", json={"username": "admin", "password": "supersecure"})
    assert logged_in.status_code == 200


def test_health_endpoint() -> None:
    _clean()
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_setup_login_and_crud_flow() -> None:
    _clean()
    _setup_and_login()

    payload = {
        "device_name": "Hall Sensor",
        "raw_value": "90013278200312345123451234512345123451234512345",
        "description": "Entry hall",
    }

    created = client.post("/api/v1/devices", json=payload)
    assert created.status_code == 201
    device_id = created.json()["id"]

    listed = client.get("/api/v1/devices")
    assert listed.status_code == 200
    assert listed.json()["pagination"]["total_items"] == 1

    loaded = client.get(f"/api/v1/devices/{device_id}")
    assert loaded.status_code == 200
    assert loaded.json()["device_name"] == "Hall Sensor"

    updated = client.put(f"/api/v1/devices/{device_id}", json={"location": "Hallway"})
    assert updated.status_code == 200
    assert updated.json()["location"] == "Hallway"

    deleted = client.delete(f"/api/v1/devices/{device_id}")
    assert deleted.status_code == 204


def test_protected_route_requires_login() -> None:
    _clean()
    fresh_client = TestClient(app)
    res = fresh_client.get("/api/v1/devices")
    assert res.status_code == 401


def test_import_endpoint_partial_success() -> None:
    _clean()
    _setup_and_login()
    payload = [
        {"device_name": "A", "raw_value": "90013278200312345123451234512345123451234512345"},
        {"device_name": "B", "raw_value": "123"},
    ]
    res = client.post("/api/v1/import", json=payload)
    assert res.status_code == 200
    assert res.json()["created"] == 1
    assert len(res.json()["errors"]) == 1
