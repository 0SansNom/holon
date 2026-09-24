import { useRef, useState } from "react";
import { Button, Callout, InputGroup, Spinner, Tag } from "@blueprintjs/core";
import { experienceApi, isAgentAppSurface, type Application } from "../../api/experience";
import { ApiError } from "../../api/client";
import { EmptyState } from "../common/ListPrimitives";
import { useBootstrapConfig } from "../../api/hooks";

type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
};

/**
 * Runtime for applications that declare an agentApp surface.
 * Opens one Intelligence session and reuses it across turns until the
 * user starts a new chat (or the session expires / is aborted).
 */
export function AgentAppView({ application }: { application: Application }) {
  const { data: bootstrap } = useBootstrapConfig();
  const intelligenceEnabled = bootstrap?.intelligence_enabled !== false;
  const surface = application.definition.surfaces.find(isAgentAppSurface);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionUrn, setSessionUrn] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const sessionUrnRef = useRef<string | null>(null);

  if (!surface) {
    return (
      <EmptyState>
        No Agent App surface on this application — enable it in the Builder tab (beta ontology-grounded
        runtime).
      </EmptyState>
    );
  }

  if (!intelligenceEnabled) {
    return (
      <Callout intent="warning" title="Intelligence disabled">
        This environment has Intelligence off (`HOLON_INTELLIGENCE_ENABLED=false`). Agent chat cannot run here.
      </Callout>
    );
  }

  function startNewChat() {
    sessionUrnRef.current = null;
    setSessionUrn(null);
    setMessages([]);
    setError(null);
    setDraft("");
  }

  async function ensureSession(): Promise<string> {
    if (sessionUrnRef.current) return sessionUrnRef.current;
    const session = await experienceApi.createAgentSession(application.name);
    sessionUrnRef.current = session.urn;
    setSessionUrn(session.urn);
    return session.urn;
  }

  async function send() {
    const message = draft.trim();
    if (!message || busy) return;
    setError(null);
    setBusy(true);
    setDraft("");
    const userMsg: ChatMessage = { id: `u-${Date.now()}`, role: "user", text: message };
    setMessages((prev) => [...prev, userMsg]);

    try {
      const urn = await ensureSession();
      const turn = await experienceApi.runAgentSessionTurn(application.name, urn, message);
      if (turn.sessionStatus && turn.sessionStatus !== "running") {
        sessionUrnRef.current = null;
        setSessionUrn(null);
      }
      setMessages((prev) => [
        ...prev,
        {
          id: `a-${Date.now()}`,
          role: "assistant",
          text: turn.text || "(empty response)",
        },
      ]);
      requestAnimationFrame(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }));
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : err instanceof Error ? err.message : "Agent turn failed";
      setError(detail);
      setMessages((prev) => [
        ...prev,
        { id: `s-${Date.now()}`, role: "system", text: detail },
      ]);
      // Next send opens a fresh session if this one was aborted/expired.
      if (/not running|expired|aborted/i.test(detail)) {
        sessionUrnRef.current = null;
        setSessionUrn(null);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="hl-flex-col hl-gap-sm" style={{ minHeight: 420 }}>
      <Callout intent="primary" icon="info-sign">
        Ontology-grounded agent runtime (beta). Tools are Knowledge Actions under the same authz as a human
        session. Messages stay in one session until you start a new chat.
      </Callout>

      <div className="hl-tag-row hl-flex-row hl-items-center hl-justify-between">
        <div className="hl-tag-row">
          <Tag minimal>tools: {surface.tools?.length ?? 0}</Tag>
          {surface.budget?.max_iterations != null && (
            <Tag minimal>max iterations: {surface.budget.max_iterations}</Tag>
          )}
          {sessionUrn && (
            <Tag minimal intent="success" title={sessionUrn}>
              session active
            </Tag>
          )}
        </div>
        <Button minimal small icon="clean" text="New chat" disabled={busy || (!sessionUrn && messages.length === 0)} onClick={startNewChat} />
      </div>

      <div
        className="hl-flex-col hl-gap-sm"
        style={{
          flex: 1,
          overflowY: "auto",
          maxHeight: 360,
          padding: "8px 0",
          borderTop: "1px solid var(--hl-border, #ddd)",
          borderBottom: "1px solid var(--hl-border, #ddd)",
        }}
      >
        {messages.length === 0 && (
          <p className="hl-text-muted">Ask the agent to use its allowed tools — e.g. look something up or propose an action.</p>
        )}
        {messages.map((m) => (
          <div
            key={m.id}
            className="hl-flex-col hl-gap-xs"
            style={{
              alignSelf: m.role === "user" ? "flex-end" : "flex-start",
              maxWidth: "85%",
            }}
          >
            <Tag minimal intent={m.role === "system" ? "danger" : m.role === "user" ? "primary" : "none"}>
              {m.role}
            </Tag>
            <div className={m.role === "system" ? "hl-text-danger" : undefined} style={{ whiteSpace: "pre-wrap" }}>
              {m.text}
            </div>
          </div>
        ))}
        {busy && (
          <div className="hl-flex-row hl-items-center hl-gap-sm">
            <Spinner size={16} />
            <span className="hl-text-muted">Running turn…</span>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {error && (
        <Callout intent="danger">
          <div className="hl-flex-row hl-items-center hl-justify-between hl-gap-sm">
            <span>{error}</span>
            <Button minimal small text="Dismiss" onClick={() => setError(null)} />
          </div>
        </Callout>
      )}

      <form
        className="hl-flex-row hl-gap-sm hl-items-center"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <InputGroup
          fill
          value={draft}
          disabled={busy}
          placeholder="Message the agent…"
          onChange={(e) => setDraft(e.target.value)}
        />
        <Button type="submit" intent="primary" disabled={busy || !draft.trim()} loading={busy}>
          Send
        </Button>
      </form>
    </div>
  );
}
