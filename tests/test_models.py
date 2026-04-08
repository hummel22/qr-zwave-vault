from app.models.device import DeviceCreate, build_device_record, generate_device_id, normalize_dsk


def test_normalize_dsk_formats_groups() -> None:
    raw = "1234512345123451234512345123451234512345"
    assert normalize_dsk(raw) == "12345-12345-12345-12345-12345-12345-12345-12345"


def test_generate_device_id_is_deterministic() -> None:
    raw_value = "90013278200312345123451234512345123451234512345"
    dsk = "1234512345123451234512345123451234512345"
    assert generate_device_id(raw_value, dsk) == generate_device_id(raw_value, dsk)


def test_build_device_record_sets_identity() -> None:
    payload = DeviceCreate(device_name="Kitchen", raw_value="90013278200312345123451234512345123451234512345")
    record = build_device_record(payload, "1234512345123451234512345123451234512345")
    assert record.id.startswith("dev-")
    assert record.schema_version == "1"
