"""Reference catalog of platform event payload schemas.

This module is imported by `holon_common` at package import time, so every
service gets the full registry just by depending on the shared library.
Adding an event to the bus = adding a schema here; `EventProducer.publish`
refuses anything unregistered.

`extra="forbid"` on every schema: a producer that adds a field without
bumping the registration fails at publish time — that drift is exactly
what the registry exists to catch.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from .registry import register


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


@register("connectivity.sync.completed", version=1)
class ConnectivitySyncCompletedV1(_Payload):
    connector_urn: str
    dataset_name: str
    dataset_urn: str
    dataset_version_urn: str
    iceberg_namespace: str
    iceberg_table: str
    snapshot_id: int
    row_count: int
    location: str


@register("knowledge.action.requested", version=1)
class KnowledgeActionRequestedV1(_Payload):
    action_name: str
    instance_urn: str
    reason: Optional[str] = None


@register("knowledge.action.invoked", version=1)
class KnowledgeActionInvokedV1(_Payload):
    action_name: str
    instance_urn: str
    reason: Optional[str] = None
    approval_id: Optional[int] = None  # present when the action went through approval


@register("knowledge.action.compensated", version=1)
class KnowledgeActionCompensatedV1(_Payload):
    action_name: str
    instance_urn: str
    approval_id: int
    error: str


@register("knowledge.action.rejected", version=1)
class KnowledgeActionRejectedV1(_Payload):
    action_name: str
    instance_urn: str
    note: Optional[str] = None  # ApprovalDecisionRequest.note is optional — a rejection needs no reason


@register("knowledge.action.approval_expired", version=1)
class KnowledgeActionApprovalExpiredV1(_Payload):
    action_name: str
    instance_urn: str
    approval_id: int


@register("identity.permission.revoked", version=1)
class IdentityPermissionRevokedV1(_Payload):
    principal_urn: str
    resource_type: str
    resource_urn: str
    relation: str


@register("automation.workflow.completed", version=1)
class AutomationWorkflowCompletedV1(_Payload):
    workflow_name: str
    approval_id: int
    instance_urn: str


@register("intelligence.agent.session_completed", version=1)
class IntelligenceAgentSessionCompletedV1(_Payload):
    """Fired when an agent session completes.
    `causation_depth`/`chain_trigger`/`max_chain_depth` exist specifically
    so an event can spawn a new agent session while staying provably bounded.
    """

    session_urn: str
    agent_urn: str
    on_behalf_of: Optional[str] = None
    status: str
    tool_calls: int
    causation_depth: int = 0
    chain_trigger: bool = False
    max_chain_depth: int = 10


@register("knowledge.objecttype.published", version=1)
class KnowledgeObjecttypePublishedV1(_Payload):
    """Ontology lifecycle. Fired when a proposed draft version becomes the live,
    published version other platforms actually read.
    """

    object_type_urn: str
    name: str
    version: int
    previous_version: Optional[int] = None


@register("platform.dlq.message_quarantined", version=1)
class PlatformDlqMessageQuarantinedV1(_Payload):
    """Dead Letter Queue — a poison message (fails registry
    validation) is quarantined here instead of being silently dropped.
    """

    original_topic: str
    original_event_type: str
    error: str
    raw_payload: dict[str, Any]
