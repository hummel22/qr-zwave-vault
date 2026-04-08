from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _clean() -> None:
    root = Path("data/repo/devices")
    root.mkdir(parents=True, exist_ok=True)
    for f in root.glob("dev-*.json"):
        f.unlink()


def test_health_endpoint() -> None:
    _clean()
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_create_list_get_delete_device() -> None:
    _clean()
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


def test_import_endpoint_partial_success() -> None:
    _clean()
    payload = [
        {"device_name": "A", "raw_value": "90013278200312345123451234512345123451234512345"},
        {"device_name": "B", "raw_value": "123"},
    ]
    res = client.post("/api/v1/import", json=payload)
    assert res.status_code == 200
    assert res.json()["created"] == 1
    assert len(res.json()["errors"]) == 1
