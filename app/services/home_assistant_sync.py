from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
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


class HomeAssistantSyncService:
    def _request_json(self, url: str, token: str, verify_ssl: bool) -> Any:
        request = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        context = None if verify_ssl else ssl._create_unverified_context()
        with urlopen(request, context=context, timeout=12) as response:
            return json.loads(response.read().decode("utf-8"))

    def _extract_candidates(self, payload: Any) -> list[HomeAssistantNodeCandidate]:
        raw_nodes: list[dict[str, Any]]
        if isinstance(payload, list):
            raw_nodes = [item for item in payload if isinstance(item, dict)]
        elif isinstance(payload, dict):
            if isinstance(payload.get("result"), list):
                raw_nodes = [item for item in payload["result"] if isinstance(item, dict)]
            elif isinstance(payload.get("nodes"), list):
                raw_nodes = [item for item in payload["nodes"] if isinstance(item, dict)]
            else:
                raw_nodes = []
        else:
            raw_nodes = []

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
                    metadata={"source": "home-assistant", "zwave_node": node},
                )
            )
        return candidates

    def fetch_nodes(self, base_url: str, token: str, zwave_path: str, verify_ssl: bool) -> list[HomeAssistantNodeCandidate]:
        normalized_base = base_url.rstrip("/")
        normalized_path = zwave_path if zwave_path.startswith("/") else f"/{zwave_path}"
        payload = self._request_json(f"{normalized_base}{normalized_path}", token, verify_ssl)
        return self._extract_candidates(payload)

    def test_config(self, base_url: str, token: str, zwave_path: str, verify_ssl: bool) -> tuple[bool, str, int]:
        try:
            nodes = self.fetch_nodes(base_url, token, zwave_path, verify_ssl)
            return True, "ok", len(nodes)
        except HTTPError as exc:
            return False, f"http_error:{exc.code}", 0
        except URLError as exc:
            return False, f"connection_error:{exc.reason}", 0
        except TimeoutError:
            return False, "timeout", 0
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
