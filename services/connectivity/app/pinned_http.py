"""HTTP client that connects to the IP already checked for SSRF.

httpx would resolve the hostname again. A name that flips from a public
address to loopback between the check and the connect is refused, and a
stable name is dialed at the pinned address with the original host kept
for SNI and the Host header.
"""

from __future__ import annotations

import ipaddress

import httpcore
import httpx

from holon_common.connector_safety import ConnectorSafetyError, pin_connector_host


class _PinningBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, inner: httpcore.AsyncNetworkBackend) -> None:
        self._inner = inner

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        pinned = pin_connector_host(host)
        stream = await self._inner.connect_tcp(
            pinned,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )
        peer = stream.get_extra_info("server_addr")
        if peer:
            try:
                peer_ip = ipaddress.ip_address(peer[0])
            except ValueError as exc:
                await stream.aclose()
                raise ConnectorSafetyError(f"host {host!r} connected to an unreadable peer") from exc
            if peer_ip != ipaddress.ip_address(pinned):
                await stream.aclose()
                raise ConnectorSafetyError(
                    f"host {host!r} connected to {peer[0]}, not the checked address {pinned}"
                )
        return stream

    async def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options=None):
        raise ConnectorSafetyError("unix sockets are not allowed for connectors")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def pinned_transport() -> httpx.AsyncHTTPTransport:
    transport = httpx.AsyncHTTPTransport()
    pool = getattr(transport, "_pool", None)
    inner = getattr(pool, "_network_backend", None)
    if inner is None:
        raise ConnectorSafetyError("HTTP transport cannot pin a resolved address")
    pool._network_backend = _PinningBackend(inner)
    return transport
