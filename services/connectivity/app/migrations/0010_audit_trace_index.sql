-- Audit records of one action share a trace id (holon_common.correlation).

CREATE INDEX IF NOT EXISTS audit_event_tenant_trace_idx
    ON audit_event (tenant_id, trace_id, occurred_at);
