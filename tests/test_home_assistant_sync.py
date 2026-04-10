from app.services.home_assistant_sync import HomeAssistantSyncConfig, HomeAssistantSyncService


def test_fetch_nodes_normalized_ingress_mode() -> None:
    service = HomeAssistantSyncService()

    def fake_request(url: str, token: str | None, config: HomeAssistantSyncConfig):
        if url.endswith("/api/hassio/addons/zwavejs2mqtt/info"):
            return {
                "data": {
                    "state": "started",
                    "ingress": True,
                    "ingress_url": "/api/hassio_ingress/abc123",
                }
            }
        if url.endswith("/api/hassio_ingress/abc123/api/nodes"):
            return {"success": True, "data": [{"id": 4, "name": "Kitchen Dimmer", "dsk": "12345-12345-12345-12345-12345-12345-12345-12345"}]}
        raise AssertionError(f"unexpected URL: {url}")

    service._request_json_with_retry = fake_request  # type: ignore[method-assign]
    config = HomeAssistantSyncConfig(mode="ingress", ha_base_url="https://ha.local", ha_auth_token="token")

    normalized = service.fetch_nodes_normalized(config)
    assert normalized["source"] == "zwave_js_ui"
    assert normalized["mode"] == "ingress"
    assert normalized["nodes"][0]["id"] == 4
    assert normalized["nodes"][0]["raw"]["name"] == "Kitchen Dimmer"


def test_fetch_nodes_normalized_direct_mode() -> None:
    service = HomeAssistantSyncService()

    def fake_request(url: str, token: str | None, config: HomeAssistantSyncConfig):
        assert token == "zwave-token"
        assert url == "http://zwave.local:8091/api/nodes"
        return {"data": [{"id": 7, "name": "Hall Sensor", "dsk": "54321-54321-54321-54321-54321-54321-54321-54321"}]}

    service._request_json_with_retry = fake_request  # type: ignore[method-assign]
    config = HomeAssistantSyncConfig(mode="direct", zwave_base_url="http://zwave.local:8091", zwave_api_token="zwave-token")
    normalized = service.fetch_nodes_normalized(config)
    assert normalized["mode"] == "direct"
    assert normalized["nodes"][0]["id"] == 7


def test_ingress_test_config_returns_discovery_error() -> None:
    service = HomeAssistantSyncService()
    ok, reason, count = service.test_config(HomeAssistantSyncConfig(mode="ingress"))
    assert ok is False
    assert reason == "missing_home_assistant_config"
    assert count == 0
