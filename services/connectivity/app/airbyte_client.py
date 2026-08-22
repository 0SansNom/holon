"""Thin async client over Airbyte's Public API (ADR 027).

Connectivity never implements a connector itself for anything routed
through this client — it only creates/triggers Airbyte's own source,
destination, and connection objects and reads back their ids. Auth is a
client-id/client-secret exchange for a short-lived bearer token, the
same shape Airbyte's own Airflow/Prefect integrations use.
"""

from __future__ import annotations

from typing import Any

import httpx

_TOKEN_PATH = "/api/public/v1/applications/token"
_SOURCES_PATH = "/api/public/v1/sources"
_DESTINATIONS_PATH = "/api/public/v1/destinations"
_CONNECTIONS_PATH = "/api/public/v1/connections"
_JOBS_PATH = "/api/public/v1/jobs"


class AirbyteApiError(RuntimeError):
    """Raised on any non-2xx response from the Airbyte API — callers
    handle this the same way `_run_sync_for_dataset` already handles
    `httpx.HTTPStatusError`/`RequestError` for other source kinds.
    """


class AirbyteClient:
    def __init__(
        self,
        *,
        base_url: str,
        client_id: str,
        client_secret: str,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout
        self._transport = transport

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._transport)

    async def _token(self) -> str:
        async with self._http() as http:
            response = await http.post(
                f"{self._base_url}{_TOKEN_PATH}",
                json={"client_id": self._client_id, "client_secret": self._client_secret},
            )
        _raise_for_status(response)
        return response.json()["access_token"]

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        token = await self._token()
        async with self._http() as http:
            response = await http.post(
                f"{self._base_url}{path}",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
        _raise_for_status(response)
        return response.json()

    async def _get(self, path: str) -> dict[str, Any]:
        token = await self._token()
        async with self._http() as http:
            response = await http.get(
                f"{self._base_url}{path}",
                headers={"Authorization": f"Bearer {token}"},
            )
        _raise_for_status(response)
        return response.json()

    async def _delete(self, path: str) -> None:
        token = await self._token()
        async with self._http() as http:
            response = await http.delete(
                f"{self._base_url}{path}",
                headers={"Authorization": f"Bearer {token}"},
            )
        _raise_for_status(response)

    async def create_source(self, *, name: str, workspace_id: str, configuration: dict[str, Any]) -> str:
        body = {"name": name, "workspaceId": workspace_id, "configuration": configuration}
        result = await self._post(_SOURCES_PATH, body)
        return result["sourceId"]

    async def create_destination(self, *, name: str, workspace_id: str, configuration: dict[str, Any]) -> str:
        body = {"name": name, "workspaceId": workspace_id, "configuration": configuration}
        result = await self._post(_DESTINATIONS_PATH, body)
        return result["destinationId"]

    async def create_connection(
        self,
        *,
        name: str,
        source_id: str,
        destination_id: str,
        namespace: str | None = None,
    ) -> str:
        body: dict[str, Any] = {"name": name, "sourceId": source_id, "destinationId": destination_id}
        if namespace:
            body["namespaceDefinition"] = "custom_format"
            body["namespaceFormat"] = namespace
        result = await self._post(_CONNECTIONS_PATH, body)
        return result["connectionId"]

    async def trigger_sync(self, connection_id: str) -> str:
        result = await self._post(_JOBS_PATH, {"connectionId": connection_id, "jobType": "sync"})
        return str(result["jobId"])

    async def get_connection(self, connection_id: str) -> dict[str, Any]:
        return await self._get(f"{_CONNECTIONS_PATH}/{connection_id}")

    async def delete_connection(self, connection_id: str) -> None:
        await self._delete(f"{_CONNECTIONS_PATH}/{connection_id}")

    async def delete_source(self, source_id: str) -> None:
        await self._delete(f"{_SOURCES_PATH}/{source_id}")

    async def delete_destination(self, destination_id: str) -> None:
        await self._delete(f"{_DESTINATIONS_PATH}/{destination_id}")


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code >= 400:
        raise AirbyteApiError(f"Airbyte API {response.request.method} {response.request.url} -> {response.status_code}: {response.text[:500]}")
