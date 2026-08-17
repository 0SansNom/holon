"""Authorization PDP.

Two engines behind one interface: SpiceDB is the sole authority on
relational grants (ReBAC); OPA can only narrow what SpiceDB already
granted, never widen it (evaluation order is ReBAC then ABAC,
always). Every decision carries its reason.

Delegation: when the acting principal declares `on_behalf_of`,
`authorize` grants only the **intersection** of the agent's own ReBAC
grant and its mandant's — an agent can never exceed its mandant. ABAC
still evaluates the acting principal's own attributes, not the mandant's.

`authorize()` caches its own result for `decision_cache_ttl_seconds`, keyed
on everything the decision actually depends on (principal, mandant,
country, resource, permission, resource attributes). A cache hit is a
plain in-process dict lookup; a miss pays SpiceDB+OPA's real latency once,
then serves repeat checks from memory until the TTL lapses or an
explicit invalidation fires.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from prometheus_client import Counter

from .auth import Principal
from .audit import emit_audit
from .observability import CircuitBreaker

logger = logging.getLogger("holon_common.authz")

_TIMEOUT_SECONDS = 5.0
_DEFAULT_DECISION_CACHE_TTL_SECONDS = 5.0

# Real observability for the decision cache, exposed at whichever
# service's own `/metrics`.
_DECISION_CACHE_HITS = Counter("holon_authz_decision_cache_hits_total", "Decision cache hits")
_DECISION_CACHE_MISSES = Counter("holon_authz_decision_cache_misses_total", "Decision cache misses (real SpiceDB/OPA round trip)")


@dataclass
class Decision:
    allowed: bool
    reason: str


class PermissionClient:
    def __init__(
        self,
        spicedb_url: str,
        spicedb_preshared_key: str,
        opa_url: str,
        *,
        decision_cache_ttl_seconds: float = _DEFAULT_DECISION_CACHE_TTL_SECONDS,
    ):
        self._spicedb_url = spicedb_url.rstrip("/")
        self._spicedb_headers = {"Authorization": f"Bearer {spicedb_preshared_key}"}
        self._opa_url = opa_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=_TIMEOUT_SECONDS,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        self._rebac_breaker = CircuitBreaker(name="spicedb-check", failure_threshold=5, cooldown_seconds=30.0)
        self._abac_breaker = CircuitBreaker(name="opa-check", failure_threshold=5, cooldown_seconds=30.0)

        # Decision cache.
        self._decision_cache_ttl = decision_cache_ttl_seconds
        self._decision_cache: dict[tuple, tuple[Decision, float]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    def _cache_key(
        self, principal: Principal, resource_type: str, resource_urn: str, permission: str, resource_attributes: Optional[dict]
    ) -> tuple:
        return (
            principal.urn,
            principal.on_behalf_of,
            principal.country,
            resource_type,
            resource_urn,
            permission,
            tuple(sorted((resource_attributes or {}).items())),
        )

    def invalidate_principal(self, principal_urn: str) -> int:
        """Event-driven invalidation. Call this when
        `identity.permission.revoked` fires for `principal_urn`. Purges
        every cached decision where that URN is the acting principal *or*
        a mandant. Returns the number of entries purged.
        """
        stale_keys = [key for key in self._decision_cache if principal_urn in (key[0], key[1])]
        for key in stale_keys:
            self._decision_cache.pop(key, None)
        return len(stale_keys)

    def clear_cache(self) -> None:
        self._decision_cache.clear()

    async def write_schema(self, schema: str) -> None:
        response = await self._client.post(
            f"{self._spicedb_url}/v1/schema/write", headers=self._spicedb_headers, json={"schema": schema}
        )
        response.raise_for_status()

    async def write_relationship(
        self, *, resource_type: str, resource_urn: str, relation: str, subject_urn: str, subject_type: str = "principal"
    ) -> None:
        body = {
            "updates": [
                {
                    "operation": "OPERATION_TOUCH",
                    "relationship": {
                        "resource": {"objectType": resource_type, "objectId": _object_id(resource_urn)},
                        "relation": relation,
                        "subject": {"object": {"objectType": subject_type, "objectId": _object_id(subject_urn)}},
                    },
                }
            ]
        }
        response = await self._client.post(
            f"{self._spicedb_url}/v1/relationships/write", headers=self._spicedb_headers, json=body
        )
        response.raise_for_status()

    async def delete_relationship(
        self, *, resource_type: str, resource_urn: str, relation: str, subject_urn: str, subject_type: str = "principal"
    ) -> None:
        """Revocation (`identity.permission.revoked`). Same
        `WriteRelationships` call shape as `write_relationship`, just
        `OPERATION_DELETE` instead of `OPERATION_TOUCH`.
        """
        body = {
            "updates": [
                {
                    "operation": "OPERATION_DELETE",
                    "relationship": {
                        "resource": {"objectType": resource_type, "objectId": _object_id(resource_urn)},
                        "relation": relation,
                        "subject": {"object": {"objectType": subject_type, "objectId": _object_id(subject_urn)}},
                    },
                }
            ]
        }
        response = await self._client.post(
            f"{self._spicedb_url}/v1/relationships/write", headers=self._spicedb_headers, json=body
        )
        response.raise_for_status()

    async def read_relationships(
        self, *, resource_type: str, resource_urn: str, relation: Optional[str] = None
    ) -> list[dict]:
        """Enumerates existing relationships for a resource — used by
        project re-scoping (`_link_object_type_to_project`) to find and
        delete a stale `parent_project` edge before writing the current
        one: relationships are additive (`OPERATION_TOUCH`), so nothing
        prunes an old edge on its own when a single-valued Postgres
        column like `object_type.project_urn` moves on to a new value.
        The gateway streams one JSON object per line rather than a JSON
        array. Fully consistent on purpose: the default minimize-latency
        consistency serves from a quantized revision window that lags
        writes by seconds, which made a grant/revoke briefly invisible
        to the governance listings built on this — unacceptable for an
        "is the revocation effective?" view, and `check_rebac` already
        pays the same fully-consistent cost for every decision.
        """
        relationship_filter: dict[str, Any] = {
            "resourceType": resource_type, "optionalResourceId": _object_id(resource_urn)
        }
        if relation is not None:
            relationship_filter["optionalRelation"] = relation
        response = await self._client.post(
            f"{self._spicedb_url}/v1/relationships/read",
            headers=self._spicedb_headers,
            json={"relationshipFilter": relationship_filter, "consistency": {"fullyConsistent": True}},
        )
        response.raise_for_status()
        relationships = []
        for line in response.text.splitlines():
            line = line.strip()
            if not line:
                continue
            relationships.append(json.loads(line)["result"]["relationship"])
        return relationships

    async def check_rebac(self, principal_urn: str, resource_type: str, resource_urn: str, permission: str) -> bool:
        body = {
            "resource": {"objectType": resource_type, "objectId": _object_id(resource_urn)},
            "permission": permission,
            "subject": {"object": {"objectType": "principal", "objectId": _object_id(principal_urn)}},
            "consistency": {"fullyConsistent": True},
        }

        async def _do() -> httpx.Response:
            response = await self._client.post(
                f"{self._spicedb_url}/v1/permissions/check", headers=self._spicedb_headers, json=body
            )
            response.raise_for_status()
            return response

        response = await self._rebac_breaker.call(_do)
        return response.json().get("permissionship") == "PERMISSIONSHIP_HAS_PERMISSION"

    async def check_abac(self, principal: Principal, resource: dict) -> bool:
        async def _do() -> httpx.Response:
            response = await self._client.post(
                f"{self._opa_url}/v1/data/holon/authz/allow",
                json={"input": {"principal": {"country": principal.country}, "resource": resource}},
            )
            response.raise_for_status()
            return response

        response = await self._abac_breaker.call(_do)
        return response.json().get("result", False)

    async def get_policy_data(self, path: str) -> Any:
        """Reads an arbitrary rego rule's value straight from OPA's own
        data API (`GET /v1/data/{path}`) — for policy *data* a caller
        needs to mirror elsewhere (e.g. `search.py`'s entitlement tokens,
        which need `holon.authz.allowed_countries` to build per-document
        tokens, not to evaluate a per-request `allow` decision). Exists
        so that mirroring reads the live policy instead of a hand-copied
        Python literal that can silently drift from `docker/opa/holon.rego`
        — a real, previously-flagged two-sources-of-truth gap.
        """
        response = await self._client.get(f"{self._opa_url}/v1/data/{path}")
        response.raise_for_status()
        return response.json()["result"]

    async def authorize(
        self,
        principal: Principal,
        *,
        resource_type: str,
        resource_urn: str,
        permission: str,
        resource_attributes: Optional[dict] = None,
    ) -> Decision:
        """Cache wrapper around `_authorize_uncached`. A cache hit never
        touches the network; a miss falls through to the real ReBAC+ABAC
        evaluation and caches the result.
        """
        cache_key = self._cache_key(principal, resource_type, resource_urn, permission, resource_attributes)
        cached = self._decision_cache.get(cache_key)
        now = time.monotonic()
        if cached is not None:
            decision, expires_at = cached
            if now < expires_at:
                _DECISION_CACHE_HITS.inc()
                # Denials stay audible even on cache hits (security signal).
                if not decision.allowed:
                    emit_audit(
                        category="authz",
                        action="authz.decide",
                        outcome="deny",
                        tenant_id=principal.tenant_id,
                        actor_urn=principal.urn,
                        actor_type=principal.type,
                        resource_type=resource_type,
                        resource_urn=resource_urn,
                        permission=permission,
                        reason=decision.reason,
                        extra={"cacheHit": True},
                    )
                return decision
            del self._decision_cache[cache_key]

        _DECISION_CACHE_MISSES.inc()
        decision = await self._authorize_uncached(
            principal,
            resource_type=resource_type,
            resource_urn=resource_urn,
            permission=permission,
            resource_attributes=resource_attributes,
        )
        self._decision_cache[cache_key] = (decision, now + self._decision_cache_ttl)
        return decision

    async def _authorize_uncached(
        self,
        principal: Principal,
        *,
        resource_type: str,
        resource_urn: str,
        permission: str,
        resource_attributes: Optional[dict] = None,
    ) -> Decision:
        """ReBAC grants, then ABAC restricts — never the other order.
        Under delegation (`on_behalf_of`), the mandant's grant is
        checked too and only the intersection is granted."""
        granted = await self.check_rebac(principal.urn, resource_type, resource_urn, permission)
        if not granted:
            decision = Decision(False, f"rebac_denied: {principal.urn} has no '{permission}' on {resource_urn}")
            logger.info("authz: %s", decision.reason)
            emit_audit(
                category="authz",
                action="authz.decide",
                outcome="deny" if not decision.allowed else "allow",
                tenant_id=principal.tenant_id,
                actor_urn=principal.urn,
                actor_type=principal.type,
                resource_type=resource_type,
                resource_urn=resource_urn,
                permission=permission,
                reason=decision.reason,
            )
            return decision

        if principal.on_behalf_of is not None:
            mandant_granted = await self.check_rebac(
                principal.on_behalf_of, resource_type, resource_urn, permission
            )
            if not mandant_granted:
                decision = Decision(
                    False,
                    f"rebac_denied: {principal.urn} acts on behalf of {principal.on_behalf_of}, "
                    f"which has no '{permission}' on {resource_urn}",
                )
                logger.info("authz: %s", decision.reason)
                emit_audit(
                    category="authz",
                    action="authz.decide",
                    outcome="deny",
                    tenant_id=principal.tenant_id,
                    actor_urn=principal.urn,
                    actor_type=principal.type,
                    resource_type=resource_type,
                    resource_urn=resource_urn,
                    permission=permission,
                    reason=decision.reason,
                )
                return decision

        allowed_by_policy = await self.check_abac(principal, resource_attributes or {})
        if not allowed_by_policy:
            decision = Decision(False, f"abac_denied: policy restricted '{permission}' on {resource_urn}")
            logger.info("authz: %s", decision.reason)
            emit_audit(
                category="authz",
                action="authz.decide",
                outcome="deny",
                tenant_id=principal.tenant_id,
                actor_urn=principal.urn,
                actor_type=principal.type,
                resource_type=resource_type,
                resource_urn=resource_urn,
                permission=permission,
                reason=decision.reason,
            )
            return decision

        decision = Decision(True, f"granted: {principal.urn} -> {permission} on {resource_urn}")
        logger.info("authz: %s", decision.reason)
        emit_audit(
            category="authz",
            action="authz.decide",
            outcome="allow",
            tenant_id=principal.tenant_id,
            actor_urn=principal.urn,
            actor_type=principal.type,
            resource_type=resource_type,
            resource_urn=resource_urn,
            permission=permission,
            reason=decision.reason,
        )
        return decision


def _object_id(urn: str) -> str:
    """SpiceDB object IDs disallow ':' and '.' — URNs use both freely
    (e.g. RelationType `Order.customer`). A plain, deterministic swap
    keeps the mapping obvious without adding a lookup table.
    """
    return urn.replace(":", "_").replace(".", "_")
