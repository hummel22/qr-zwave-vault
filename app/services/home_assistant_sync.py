from __future__ import annotations

import base64
import json
import logging
import os
import socket
import ssl
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from app.models.device import DeviceCreate, DeviceRecord, build_device_record, normalize_dsk, now_utc

logger = logging.getLogger(__name__)


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
            body = response.read().decode("utf-8")
            if not body.strip():
                raise ValueError("empty response from server")
            return json.loads(body)

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

    def _ws_connect(self, host: str, port: int, path: str, timeout: int) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        s.sendall(handshake.encode())
        resp = s.recv(4096)
        if b"101" not in resp:
            s.close()
            raise ValueError("websocket_handshake_failed")
        return s

    def _ws_recv(self, s: socket.socket, timeout: int = 10) -> str | None:
        s.settimeout(timeout)
        hdr = s.recv(2)
        if len(hdr) < 2:
            return None
        length = hdr[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", s.recv(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", s.recv(8))[0]
        payload = b""
        while len(payload) < length:
            chunk = s.recv(min(length - len(payload), 65536))
            if not chunk:
                break
            payload += chunk
        return payload.decode("utf-8", errors="replace")

    def _ws_send(self, s: socket.socket, obj: dict) -> None:
        data = json.dumps(obj).encode("utf-8")
        frame = bytearray([0x81])
        mask = os.urandom(4)
        if len(data) < 126:
            frame.append(0x80 | len(data))
        elif len(data) < 65536:
            frame.append(0x80 | 126)
            frame.extend(struct.pack(">H", len(data)))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack(">Q", len(data)))
        frame.extend(mask)
        frame.extend(bytearray(b ^ mask[i % 4] for i, b in enumerate(data)))
        s.sendall(frame)

    def _fetch_nodes_via_websocket(self, config: HomeAssistantSyncConfig) -> list[dict[str, Any]]:
        url = config.zwave_base_url or config.ha_base_url or ""
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme in ("wss", "https") else 3000)
        path = parsed.path or "/"

        timeout = config.request_timeout_seconds
        s = self._ws_connect(host, port, path, timeout)
        try:
            # Read version message
            raw = self._ws_recv(s, timeout)
            if not raw:
                raise ValueError("zwave_ws_no_version")
            version_msg = json.loads(raw)
            if version_msg.get("type") != "version":
                raise ValueError("zwave_ws_unexpected_message")
            max_schema = version_msg.get("maxSchemaVersion", 0)
            logger.info(
                "Z-Wave JS Server v%s (driver %s, schema %d-%d)",
                version_msg.get("serverVersion"),
                version_msg.get("driverVersion"),
                version_msg.get("minSchemaVersion", 0),
                max_schema,
            )

            # Set API schema
            schema_ver = min(max_schema, 35)
            self._ws_send(s, {"messageId": "schema", "command": "set_api_schema", "schemaVersion": schema_ver})
            schema_resp = self._ws_recv(s, timeout)
            if not schema_resp:
                raise ValueError("zwave_ws_schema_timeout")

            # Request full state
            self._ws_send(s, {"messageId": "state", "command": "start_listening"})
            state_raw = self._ws_recv(s, timeout)
            if not state_raw:
                raise ValueError("zwave_ws_state_timeout")
            state_msg = json.loads(state_raw)
            state = state_msg.get("result", {}).get("state", {})
            home_id = state.get("controller", {}).get("homeId") or version_msg.get("homeId") or 0
            nodes = state.get("nodes", [])

            # Normalize node data to match the format expected by _to_candidates
            normalized: list[dict[str, Any]] = []
            for node in nodes:
                device_config = node.get("deviceConfig") or {}
                node_id = node.get("nodeId", 0)
                # Build a synthetic DSK from homeId + nodeId for tracking (8 groups of 5 digits = 40 digits)
                home_id_truncated = home_id % 100000  # fit into 5 digits
                synthetic_dsk = f"{home_id_truncated:05d}-{node_id:05d}-00000-00000-00000-00000-00000-00000"
                normalized.append({
                    "nodeId": node_id,
                    "name": node.get("name") or node.get("label") or f"Node {node_id}",
                    "location": node.get("location") or "",
                    "manufacturer": device_config.get("manufacturer") or "",
                    "productLabel": device_config.get("label") or node.get("label") or "",
                    "description": device_config.get("description") or "",
                    "dsk": node.get("dsk") or synthetic_dsk,
                    "status": node.get("status"),
                    "firmwareVersion": node.get("firmwareVersion"),
                    "isControllerNode": node.get("isControllerNode", False),
                    "_source_node": node,
                })
            logger.info("Fetched %d nodes via WebSocket (homeId=%s)", len(normalized), home_id)
            return normalized
        finally:
            s.close()

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

    def _is_websocket_target(self, config: HomeAssistantSyncConfig) -> bool:
        url = config.zwave_base_url or ""
        return url.startswith("ws://") or url.startswith("wss://")

    def fetch_nodes_normalized(self, config: HomeAssistantSyncConfig) -> dict[str, Any]:
        if config.mode == "direct" and config.zwave_base_url:
            # Try WebSocket first (Z-Wave JS Server protocol), fall back to HTTP
            try:
                raw_nodes = self._fetch_nodes_via_websocket(config)
            except (socket.timeout, TimeoutError, OSError, ValueError) as ws_err:
                if self._is_websocket_target(config):
                    raise
                logger.info("WebSocket fetch failed (%s), falling back to HTTP", ws_err)
                raw_nodes = self._fetch_nodes_via_http(config)
        elif config.mode == "ingress":
            raw_nodes = self._fetch_nodes_via_http(config)
        elif config.mode == "direct":
            raise ValueError("missing_zwave_base_url")
        else:
            raise ValueError("unsupported_mode")

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

    def _fetch_nodes_via_http(self, config: HomeAssistantSyncConfig) -> list[dict[str, Any]]:
        if config.mode == "ingress":
            base_url = self._resolve_ingress_base(config)
            auth_token = config.ha_auth_token
        else:
            if not config.zwave_base_url:
                raise ValueError("missing_zwave_base_url")
            base_url = config.zwave_base_url.rstrip("/")
            auth_token = config.zwave_api_token

        path = config.zwave_path if config.zwave_path.startswith("/") else f"/{config.zwave_path}"
        payload = self._request_json_with_retry(f"{base_url}{path}", auth_token, config)
        return self._extract_nodes_from_payload(payload)

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
        except (TimeoutError, socket.timeout):
            return False, "api_transport_timeout", 0
        except ConnectionRefusedError:
            return False, "connection_refused", 0
        except OSError as exc:
            return False, f"network_error:{exc}", 0
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
