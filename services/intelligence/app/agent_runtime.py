"""Agent Runtime — Agent session management and execution.

A session is a real Resource: URN, tenant, owning agent
principal, mandant (`on_behalf_of`), budget, consumed counters, an
append-only transcript, and a status lifecycle.

- **Effective rights**: tool calls hit Knowledge's PDP-gated endpoint with the
  session's own bearer token, computing the agent and mandant rights intersection.
- **Tool Registry**: compiles Knowledge's `GET /actions` definitions into tool schemas.
- **Human-in-the-loop**: high-risk Actions invoked as tools create pending approval rows.
- **Loop detection**: terminal sessions publish `intelligence.agent.session_completed`.
  Automation can spawn follow-up sessions up to `max_chain_depth`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import asyncpg
import httpx

from holon_common import EventActor, EventEnvelope, build_urn, outbox

from . import tool_plugin_registry
from .llm_gateway import LLMClient

logger = logging.getLogger("intelligence.agent_runtime")

# Default budget values.
DEFAULT_BUDGET = {"max_iterations": 10, "max_tool_calls": 25, "max_tokens": 50_000}
DEFAULT_TTL_SECONDS = 15 * 60  # interactive session

DDL = """
CREATE TABLE IF NOT EXISTS agent_session (
    urn TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    agent_urn TEXT NOT NULL,
    on_behalf_of TEXT,
    budget JSONB NOT NULL,
    consumed JSONB NOT NULL DEFAULT '{"iterations": 0, "tool_calls": 0, "tokens": 0}',
    status TEXT NOT NULL DEFAULT 'running',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_turn (
    id BIGSERIAL PRIMARY KEY,
    session_urn TEXT NOT NULL REFERENCES agent_session(urn),
    role TEXT NOT NULL,
    content JSONB NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# Additive migration: loop-detection fields, added after `agent_session` already
# shipped without them.
_MIGRATIONS = """
ALTER TABLE agent_session ADD COLUMN IF NOT EXISTS causation_id TEXT;
ALTER TABLE agent_session ADD COLUMN IF NOT EXISTS causation_depth INT NOT NULL DEFAULT 0;
ALTER TABLE agent_session ADD COLUMN IF NOT EXISTS chain_trigger BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE agent_session ADD COLUMN IF NOT EXISTS max_chain_depth INT NOT NULL DEFAULT 10;
"""

_SYSTEM_PROMPT = (
    "You are Holon's autonomous agent. Use the provided tools to "
    "carry out the user's request. Only use a tool when the request "
    "genuinely calls for a mutation; otherwise answer directly. Be "
    "concise about what you did and why."
)


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)
    await conn.execute(_MIGRATIONS)


async def create_session(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    agent_urn: str,
    on_behalf_of: Optional[str],
    budget: Optional[dict] = None,
    ttl_seconds: Optional[int] = None,
    causation_id: Optional[str] = None,
    causation_depth: int = 0,
    chain_trigger: bool = False,
    max_chain_depth: int = 10,
) -> dict:
    if chain_trigger and causation_depth > max_chain_depth:
        # The authoritative circuit breaker — holds even if a caller
        # (or a bug in Automation's own pre-check) tries to push one hop
        # past the declared ceiling. `max_chain_depth` is inclusive: a
        # session AT that depth is the last one allowed to exist.
        raise ValueError(
            f"refusing to start a chained agent session at causation_depth={causation_depth} "
            f"(max_chain_depth={max_chain_depth}) — loop guard"
        )
    session_urn = build_urn(tenant_id, "global", "agent-session", uuid.uuid4().hex)
    full_budget = {**DEFAULT_BUDGET, **(budget or {})}
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds or DEFAULT_TTL_SECONDS)
    await pool.execute(
        """
        INSERT INTO agent_session
            (urn, tenant_id, agent_urn, on_behalf_of, budget, expires_at,
             causation_id, causation_depth, chain_trigger, max_chain_depth)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10)
        """,
        session_urn, tenant_id, agent_urn, on_behalf_of, json.dumps(full_budget), expires_at,
        causation_id, causation_depth, chain_trigger, max_chain_depth,
    )
    return await get_session(pool, session_urn)


async def get_session(pool: asyncpg.Pool, session_urn: str) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM agent_session WHERE urn = $1", session_urn)
    if row is None:
        return None
    result = dict(row)
    for field in ("budget", "consumed"):
        if isinstance(result[field], str):
            result[field] = json.loads(result[field])
    return result


async def get_transcript(pool: asyncpg.Pool, session_urn: str) -> list[dict]:
    rows = await pool.fetch(
        "SELECT role, content, recorded_at FROM agent_turn WHERE session_urn = $1 ORDER BY id", session_urn
    )
    # asyncpg returns JSONB columns as raw text, not parsed dicts (unlike
    # some other frameworks) — every other JSONB reader in this codebase
    # (e.g. serving_store.get_instance) already does this same parse.
    return [{"role": row["role"], "content": json.loads(row["content"]), "recorded_at": row["recorded_at"]} for row in rows]


async def _record_turn(pool: asyncpg.Pool, session_urn: str, role: str, content: Any) -> None:
    await pool.execute(
        "INSERT INTO agent_turn (session_urn, role, content) VALUES ($1, $2, $3::jsonb)",
        session_urn,
        role,
        json.dumps(content, default=str),
    )


async def _list_tools(
    pool: asyncpg.Pool, http: httpx.AsyncClient, knowledge_url: str, headers: dict
) -> tuple[list[dict], dict[str, dict]]:
    """Compiles two sources into one tool list: Knowledge's real,
    audited ontology Actions, and any active **agent tool plugin**
    — a synthetic capability with no ontology
    backing at all (see `tool_plugin_registry.py`'s module docstring).
    Recomputed fresh every turn.
    """
    response = await http.get(f"{knowledge_url}/actions", headers=headers)
    response.raise_for_status()
    tools, by_tool_name = [], {}
    for action in response.json():
        tool_name = action["name"].replace(".", "_")
        by_tool_name[tool_name] = {"kind": "knowledge_action", **action}
        tools.append(
            {
                "name": tool_name,
                "description": action["description"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "instance_id": {"type": "integer", "description": f"the {action['target_object_type']} id"},
                        "reason": {"type": "string", "description": "why this action is being invoked"},
                    },
                    "required": ["instance_id", "reason"],
                },
            }
        )

    for registration in await tool_plugin_registry.list_active_tool_plugins(pool):
        manifest = registration["manifest"]
        tool_name = manifest["tool_name"]
        by_tool_name[tool_name] = {"kind": "agent_tool_plugin", "manifest": manifest}
        tools.append(
            {"name": tool_name, "description": manifest["tool_description"], "input_schema": manifest["input_schema"]}
        )

    return tools, by_tool_name


async def _invoke_tool(http: httpx.AsyncClient, knowledge_url: str, headers: dict, entry: dict, tool_input: dict) -> dict:
    """Dispatches on `entry["kind"]` — a real Knowledge Action re-hits the
    real Action endpoint (a high-risk Action still only ever produces a
    `pending` approval here, exactly as it would for a human caller);
    an agent-tool plugin is loaded dynamically and invoked directly.
    """
    if entry["kind"] == "agent_tool_plugin":
        plugin = tool_plugin_registry.load_tool_plugin(entry["manifest"])
        body = await plugin.invoke(tool_input)
        return {"status_code": 200, "body": body}

    action = entry
    target_object_type = action["target_object_type"]
    local_name = action["name"].split(".", 1)[1]
    instance_id = tool_input["instance_id"]
    response = await http.post(
        f"{knowledge_url}/objects/{target_object_type}/{instance_id}/actions/{local_name}",
        headers=headers,
        json={"reason": tool_input.get("reason", "")},
    )
    try:
        body = response.json()
    except ValueError:
        body = {"detail": response.text}
    return {"status_code": response.status_code, "body": body}


def _session_completed_event(session: dict, *, status: str, consumed: dict) -> EventEnvelope:
    event_id = uuid.uuid4().hex
    return EventEnvelope(
        event_id=event_id,
        event_type="intelligence.agent.session_completed",
        tenant_id=session["tenant_id"],
        aggregate_type="AgentSession",
        aggregate_id=session["urn"],
        correlation_id=event_id,
        causation_id=session.get("causation_id"),
        partition_key=f"{session['tenant_id']}/{session['urn']}",
        producer="intelligence-platform@0.1.0",
        actor=EventActor(type="agent", urn=session["agent_urn"], on_behalf_of=session.get("on_behalf_of")),
        payload={
            "session_urn": session["urn"],
            "agent_urn": session["agent_urn"],
            "on_behalf_of": session.get("on_behalf_of"),
            "status": status,
            "tool_calls": consumed["tool_calls"],
            "causation_depth": session.get("causation_depth", 0),
            "chain_trigger": session.get("chain_trigger", False),
            "max_chain_depth": session.get("max_chain_depth", 10),
        },
    )


async def _finish_session(pool: asyncpg.Pool, session: dict, status: str, consumed: dict) -> None:
    """Transactional outbox — every terminal session (completed or
    aborted) publishes `intelligence.agent.session_completed` in the same
    transaction as the status write.
    """
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE agent_session SET status = $1, consumed = $2::jsonb, updated_at = now() WHERE urn = $3",
            status,
            json.dumps(consumed),
            session["urn"],
        )
        await outbox.enqueue(conn, _session_completed_event(session, status=status, consumed=consumed))


async def run_turn(
    pool: asyncpg.Pool, *, session_urn: str, user_message: str, knowledge_url: str, authorization: str, llm: LLMClient
) -> dict:
    """Runs the agent to completion for one user message — one or more
    LLM round-trips, executing any tool calls in between. Budgets
    are checked every iteration, not just once at session start.
    """
    session = await get_session(pool, session_urn)
    if session is None:
        raise ValueError(f"no agent_session found for {session_urn!r}")
    if session["status"] != "running":
        raise ValueError(f"session {session_urn} is {session['status']}, not running")
    if session["expires_at"] < datetime.now(timezone.utc):
        await _finish_session(pool, session, "aborted", session["consumed"])
        raise ValueError(f"session {session_urn} has expired (TTL)")

    budget = session["budget"]
    consumed = session["consumed"]

    await _record_turn(pool, session_urn, "user", {"text": user_message})
    messages = [{"role": "user", "content": user_message}]

    headers = {"Authorization": authorization}
    final_text = ""
    async with httpx.AsyncClient(timeout=15.0) as http:
        tools, by_tool_name = await _list_tools(pool, http, knowledge_url, headers)

        while True:
            if consumed["iterations"] >= budget["max_iterations"]:
                await _finish_session(pool, session, "aborted", consumed)
                raise ValueError(f"session {session_urn} exceeded max_iterations ({budget['max_iterations']})")

            response = await llm.complete(system=_SYSTEM_PROMPT, messages=messages, max_tokens=1024, tools=tools)
            consumed["iterations"] += 1
            consumed["tokens"] += response.input_tokens + response.output_tokens
            if consumed["tokens"] > budget["max_tokens"]:
                await _finish_session(pool, session, "aborted", consumed)
                raise ValueError(f"session {session_urn} exceeded max_tokens ({budget['max_tokens']})")

            await _record_turn(pool, session_urn, "assistant", {"content_blocks": response.content_blocks})
            messages.append({"role": "assistant", "content": response.content_blocks})

            tool_use_blocks = [b for b in response.content_blocks if b.get("type") == "tool_use"]
            if not tool_use_blocks:
                final_text = response.text
                break

            tool_results = []
            for block in tool_use_blocks:
                if consumed["tool_calls"] >= budget["max_tool_calls"]:
                    await _finish_session(pool, session, "aborted", consumed)
                    raise ValueError(f"session {session_urn} exceeded max_tool_calls ({budget['max_tool_calls']})")
                action = by_tool_name.get(block["name"])
                result = (
                    {"error": f"unknown tool {block['name']!r}"}
                    if action is None
                    else await _invoke_tool(http, knowledge_url, headers, action, block["input"])
                )
                consumed["tool_calls"] += 1
                await _record_turn(pool, session_urn, "tool", {"tool_use_id": block["id"], "result": result})
                tool_results.append({"type": "tool_result", "tool_use_id": block["id"], "content": json.dumps(result)})

            messages.append({"role": "user", "content": tool_results})
            await pool.execute(
                "UPDATE agent_session SET consumed = $1::jsonb, updated_at = now() WHERE urn = $2",
                json.dumps(consumed),
                session_urn,
            )

    await _finish_session(pool, session, "completed", consumed)
    return {"sessionUrn": session_urn, "status": "completed", "text": final_text, "consumed": consumed}


async def replay_session(pool: asyncpg.Pool, *, session_urn: str, llm: LLMClient) -> dict:
    transcript = await get_transcript(pool, session_urn)
    user_turns = [t for t in transcript if t["role"] == "user" and "text" in t["content"]]
    assistant_turns = [t for t in transcript if t["role"] == "assistant"]
    if not user_turns or not assistant_turns:
        raise ValueError(f"session {session_urn} has no complete turn to replay")

    original_text = ""
    for block in assistant_turns[-1]["content"].get("content_blocks", []):
        if block.get("type") == "text":
            original_text += block["text"]

    response = await llm.complete(
        system=_SYSTEM_PROMPT, messages=[{"role": "user", "content": user_turns[0]["content"]["text"]}], max_tokens=1024
    )
    return {
        "sessionUrn": session_urn,
        "originalText": original_text,
        "replayedText": response.text,
        "note": "LLM output is not guaranteed byte-identical — "
        "this proves the pinned context/prompt was faithfully reconstructed, not text equality.",
    }


async def sweep_expired_sessions(pool: asyncpg.Pool) -> int:
    rows = await pool.fetch(
        "UPDATE agent_session SET status = 'aborted', updated_at = now() "
        "WHERE status = 'running' AND expires_at < now() RETURNING urn"
    )
    return len(rows)


async def sweep_expired_sessions_forever(pool: asyncpg.Pool, poll_interval: float = 5.0) -> None:
    """Same shape as `actions.sweep_expired_approvals_forever` — a lone
    background task per process, TTL enforcement for sessions nobody's
    actively driving to completion.
    """
    while True:
        try:
            expired_count = await sweep_expired_sessions(pool)
            if expired_count:
                logger.info("aborted %d expired agent session(s)", expired_count)
        except Exception:
            logger.exception("agent session expiry sweep error, retrying in %ss", poll_interval)
        await asyncio.sleep(poll_interval)
