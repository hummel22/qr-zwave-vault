from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from app.models.device import DeviceCreate, DeviceRecord, build_device_record, normalize_dsk, now_utc


@dataclass
class HomeAssistantNodeCandidate:
    node_id: str
    device_name: str
    dsk: str
    location: str | None = None
    description: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class HomeAssistantSyncConfig:
    mode: str = "ingress"
    ha_base_url: str | None = None
    ha_auth_token: str | None = None
    addon_slug: str = "zwavejs2mqtt"
    zwave_base_url: str | None = None
    zwave_api_token: str | None = None
    request_timeout_seconds: int = 10
    retry_count: int = 3
    verify_ssl: bool = True
    zwave_path: str = "/api/nodes"


class HomeAssistantSyncService:
    def _request_json(
        self,
        url: str,
        token: str | None,
        verify_ssl: bool,
        timeout_seconds: int,
    ) -> Any:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(url, headers=headers)
        context = None if verify_ssl else ssl._create_unverified_context()
        with urlopen(request, context=context, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def _request_json_with_retry(self, url: str, token: str | None, config: HomeAssistantSyncConfig) -> Any:
        attempts = config.retry_count + 1
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                return self._request_json(url, token, config.verify_ssl, config.request_timeout_seconds)
            except (URLError, TimeoutError) as exc:
                last_error = exc
        if last_error:
            raise last_error
        return {}

    def _extract_nodes_from_payload(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            return [item for item in payload["data"] if isinstance(item, dict)]
        if isinstance(payload, dict) and isinstance(payload.get("nodes"), list):
            return [item for item in payload["nodes"] if isinstance(item, dict)]
        if isinstance(payload, dict) and isinstance(payload.get("result"), list):
            return [item for item in payload["result"] if isinstance(item, dict)]
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def _to_candidates(self, mode: str, raw_nodes: list[dict[str, Any]]) -> list[HomeAssistantNodeCandidate]:
        candidates: list[HomeAssistantNodeCandidate] = []
        for node in raw_nodes:
            dsk = str(node.get("dsk") or node.get("securityCode") or "").strip()
            if not dsk:
                continue
            node_id = str(node.get("id") or node.get("nodeId") or "unknown")
            name = str(node.get("name") or node.get("device") or f"Node {node_id}").strip()
            location = str(node.get("loc") or node.get("location") or "").strip() or None
            manufacturer = str(node.get("manufacturer") or node.get("manufacturerName") or "").strip() or None
            model = str(node.get("productLabel") or node.get("model") or "").strip() or None
            description = str(node.get("description") or node.get("productDescription") or "").strip() or None
            candidates.append(
                HomeAssistantNodeCandidate(
                    node_id=node_id,
                    device_name=name,
                    dsk=dsk,
                    location=location,
                    description=description,
                    manufacturer=manufacturer,
                    model=model,
                    metadata={"source": "home-assistant", "mode": mode, "zwave_node": node},
                )
            )
        return candidates

    def _resolve_ingress_base(self, config: HomeAssistantSyncConfig) -> str:
        if not config.ha_base_url or not config.ha_auth_token:
            raise ValueError("missing_home_assistant_config")
        base = config.ha_base_url.rstrip("/")
        info_url = f"{base}/api/hassio/addons/{config.addon_slug}/info"
        payload = self._request_json_with_retry(info_url, config.ha_auth_token, config)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise ValueError("ingress_discovery_failed")
        if data.get("state") != "started":
            raise ValueError("addon_not_started")
        if data.get("ingress") is not True:
            raise ValueError("ingress_disabled_or_unavailable")
        ingress_url = str(data.get("ingress_url") or "").strip()
        ingress_entry = str(data.get("ingress_entry") or "").strip()
        ingress_base = ingress_url or ingress_entry
        if not ingress_base:
            raise ValueError("ingress_discovery_failed")
        return urljoin(f"{base}/", ingress_base.lstrip("/"))

    def fetch_nodes_normalized(self, config: HomeAssistantSyncConfig) -> dict[str, Any]:
        if config.mode == "ingress":
            base_url = self._resolve_ingress_base(config)
            auth_token = config.ha_auth_token
        elif config.mode == "direct":
            if not config.zwave_base_url:
                raise ValueError("missing_zwave_base_url")
            base_url = config.zwave_base_url.rstrip("/")
            auth_token = config.zwave_api_token
        else:
            raise ValueError("unsupported_mode")

        path = config.zwave_path if config.zwave_path.startswith("/") else f"/{config.zwave_path}"
        payload = self._request_json_with_retry(f"{base_url}{path}", auth_token, config)
        raw_nodes = self._extract_nodes_from_payload(payload)
        normalized_nodes = [
            {
                "id": node.get("id") or node.get("nodeId"),
                "name": node.get("name") or node.get("device"),
                "status": node.get("status") or node.get("state") or "unknown",
                "manufacturer": node.get("manufacturer") or node.get("manufacturerName"),
                "raw": node,
            }
            for node in raw_nodes
        ]
        return {
            "source": "zwave_js_ui",
            "mode": config.mode,
            "fetched_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "nodes": normalized_nodes,
        }

    def fetch_nodes(self, config: HomeAssistantSyncConfig) -> list[HomeAssistantNodeCandidate]:
        normalized = self.fetch_nodes_normalized(config)
        return self._to_candidates(config.mode, [item["raw"] for item in normalized["nodes"]])

    def test_config(self, config: HomeAssistantSyncConfig) -> tuple[bool, str, int]:
        try:
            normalized = self.fetch_nodes_normalized(config)
            return True, "ok", len(normalized["nodes"])
        except HTTPError as exc:
            if exc.code in (401, 403):
                reason = "auth_failure"
            elif exc.code == 404:
                reason = "api_not_found"
            else:
                reason = f"api_http_error:{exc.code}"
            return False, reason, 0
        except URLError as exc:
            return False, f"api_transport_error:{exc.reason}", 0
        except TimeoutError:
            return False, "api_transport_timeout", 0
        except ValueError as exc:
            return False, str(exc), 0
        except Exception as exc:  # pragma: no cover - defensive fallback
            return False, f"unexpected_error:{exc}", 0


def build_record_from_candidate(candidate: HomeAssistantNodeCandidate) -> DeviceRecord:
    normalized_dsk = normalize_dsk(candidate.dsk)
    synthetic_raw = f"ha://zwave-ui/node/{candidate.node_id}/dsk/{normalized_dsk.replace('-', '')}"
    payload = DeviceCreate(
        raw_value=synthetic_raw,
        device_name=candidate.device_name,
        location=candidate.location,
        description=candidate.description,
        manufacturer=candidate.manufacturer,
        model=candidate.model,
        metadata=candidate.metadata,
    )
    return build_device_record(payload, normalized_dsk)


def merge_candidate(existing: DeviceRecord, candidate: HomeAssistantNodeCandidate) -> DeviceRecord:
    current_metadata = existing.metadata if isinstance(existing.metadata, dict) else {}
    merged_metadata = {**current_metadata, **(candidate.metadata or {})}
    return existing.model_copy(
        update={
            "device_name": candidate.device_name or existing.device_name,
            "location": candidate.location or existing.location,
            "description": candidate.description or existing.description,
            "manufacturer": candidate.manufacturer or existing.manufacturer,
            "model": candidate.model or existing.model,
            "metadata": merged_metadata,
            "updated_at": now_utc(),
        }
    )
