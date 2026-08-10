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
      <div className={`hl-builder-section-header${value.enabled ? " hl-builder-section-header--expanded" : ""}`}>
        <Checkbox
          checked={value.enabled}
          label="Agent App"
          onChange={(e) => onChange({ ...value, enabled: e.target.checked })}
          className="hl-builder-checkbox"
        />
        {value.enabled && (
          <InputGroup
            small
            placeholder="/apps/name/agent"
            value={value.route}
            onChange={(e) => onChange({ ...value, route: e.target.value })}
            className="hl-builder-route-input"
          />
        )}
      </div>

      {value.enabled && (
        <div className="hl-builder-fields">
          <label className="hl-text-muted">
            System prompt
            <TextArea
              fill
              autoResize
              value={value.systemPrompt}
              onChange={(e) => onChange({ ...value, systemPrompt: e.target.value })}
              className="hl-builder-field-mt"
              placeholder="You are a narrow, bounded agent for…"
            />
          </label>

          <div>
            <div className="hl-section-title hl-mb-sm">
              Tools this agent may use — the same live catalog `GET /tools` computes, real Actions and agent-tool
              plugins alike.
            </div>
            {tools.length === 0 && <p className="hl-text-muted">No tools available.</p>}
            {tools.map((tool) => (
              <Checkbox
                key={tool.name}
                checked={value.tools.includes(tool.name)}
                label={tool.name}
                onChange={(e) => toggleTool(tool.name, e.target.checked)}
              />
            ))}
          </div>

          <div className="hl-builder-numeric-row">
            <label className="hl-text-muted">
              Max iterations
              <NumericInput
                min={1}
                value={value.maxIterations}
                onValueChange={(n) => onChange({ ...value, maxIterations: n })}
                className="hl-builder-numeric-input"
              />
            </label>
            <label className="hl-text-muted">
              Max tool calls
              <NumericInput
                min={1}
                value={value.maxToolCalls}
                onValueChange={(n) => onChange({ ...value, maxToolCalls: n })}
                className="hl-builder-numeric-input"
              />
            </label>
            <label className="hl-text-muted">
              Max tokens
              <NumericInput
                min={1000}
                stepSize={1000}
                value={value.maxTokens}
                onValueChange={(n) => onChange({ ...value, maxTokens: n })}
                className="hl-builder-numeric-input hl-builder-numeric-input--wide"
              />
            </label>
          </div>
        </div>
      )}
    </Card>
  );
}
