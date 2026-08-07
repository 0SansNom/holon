import { Card, Checkbox, InputGroup, NumericInput, TextArea } from "@blueprintjs/core";
import type { ToolDefinition } from "../../../api/intelligence";

export interface AgentAppValue {
  enabled: boolean;
  route: string;
  systemPrompt: string;
  tools: string[];
  maxIterations: number;
  maxToolCalls: number;
  maxTokens: number;
}

export function AgentAppSection({
  value,
  tools,
  onChange,
}: {
  value: AgentAppValue;
  tools: ToolDefinition[];
  onChange: (value: AgentAppValue) => void;
}) {
  function toggleTool(name: string, checked: boolean) {
    onChange({ ...value, tools: checked ? [...value.tools, name] : value.tools.filter((t) => t !== name) });
  }

  return (
    <Card>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: value.enabled ? 12 : 0 }}>
        <Checkbox
          checked={value.enabled}
          label="Agent App"
          onChange={(e) => onChange({ ...value, enabled: e.target.checked })}
          style={{ marginBottom: 0, fontWeight: 600 }}
        />
        {value.enabled && (
          <InputGroup
            small
            placeholder="/apps/name/agent"
            value={value.route}
            onChange={(e) => onChange({ ...value, route: e.target.value })}
            style={{ maxWidth: 260 }}
          />
        )}
      </div>

      {value.enabled && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <label style={{ fontSize: 12, color: "var(--hl-text-muted)" }}>
            System prompt
            <TextArea
              fill
              autoResize
              value={value.systemPrompt}
              onChange={(e) => onChange({ ...value, systemPrompt: e.target.value })}
              style={{ marginTop: 4 }}
              placeholder="You are a narrow, bounded agent for…"
            />
          </label>

          <div>
            <div style={{ fontSize: 12, color: "var(--hl-text-muted)", marginBottom: 6 }}>
              Tools this agent may use — the same live catalog `GET /tools` computes, real Actions and agent-tool
              plugins alike.
            </div>
            {tools.length === 0 && <p style={{ fontSize: 12, color: "var(--hl-text-muted)" }}>No tools available.</p>}
            {tools.map((tool) => (
              <Checkbox
                key={tool.name}
                checked={value.tools.includes(tool.name)}
                label={tool.name}
                onChange={(e) => toggleTool(tool.name, e.target.checked)}
              />
            ))}
          </div>

          <div style={{ display: "flex", gap: 16 }}>
            <label style={{ fontSize: 12, color: "var(--hl-text-muted)" }}>
              Max iterations
              <NumericInput
                min={1}
                value={value.maxIterations}
                onValueChange={(n) => onChange({ ...value, maxIterations: n })}
                style={{ marginTop: 4, width: 100 }}
              />
            </label>
            <label style={{ fontSize: 12, color: "var(--hl-text-muted)" }}>
              Max tool calls
              <NumericInput
                min={1}
                value={value.maxToolCalls}
                onValueChange={(n) => onChange({ ...value, maxToolCalls: n })}
                style={{ marginTop: 4, width: 100 }}
              />
            </label>
            <label style={{ fontSize: 12, color: "var(--hl-text-muted)" }}>
              Max tokens
              <NumericInput
                min={1000}
                stepSize={1000}
                value={value.maxTokens}
                onValueChange={(n) => onChange({ ...value, maxTokens: n })}
                style={{ marginTop: 4, width: 120 }}
              />
            </label>
          </div>
        </div>
      )}
    </Card>
  );
}
