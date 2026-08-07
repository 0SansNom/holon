import { useState } from "react";
import { Button, Callout, Card, Dialog, DialogBody, DialogFooter, HTMLSelect, InputGroup, Spinner, Tag } from "@blueprintjs/core";
import Editor from "@monaco-editor/react";
import { useActionTypes, useCreateActionType, useObjectTypes } from "../../api/hooks";
import { ApiError } from "../../api/client";

const RISK_LEVELS = ["low", "high"] as const;
const DEFAULT_EDITS = '[\n  { "property": "reviewStatus", "source": "literal", "value": "reviewed" }\n]';
const DEFAULT_PARAMETERS = "[]";
const DEFAULT_CRITERIA = "[]";

export function ActionTypesTab() {
  const { data, isLoading } = useActionTypes();
  const { data: objectTypes = [] } = useObjectTypes();
  const createActionType = useCreateActionType();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [targetObjectType, setTargetObjectType] = useState("");
  const [requiredPermission, setRequiredPermission] = useState("write");
  const [riskLevel, setRiskLevel] = useState<string>("low");
  const [description, setDescription] = useState("");
  const [parametersJson, setParametersJson] = useState(DEFAULT_PARAMETERS);
  const [editsJson, setEditsJson] = useState(DEFAULT_EDITS);
  const [criteriaJson, setCriteriaJson] = useState(DEFAULT_CRITERIA);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setName("");
    setTargetObjectType("");
    setRequiredPermission("write");
    setRiskLevel("low");
    setDescription("");
    setParametersJson(DEFAULT_PARAMETERS);
    setEditsJson(DEFAULT_EDITS);
    setCriteriaJson(DEFAULT_CRITERIA);
    setError(null);
  }

  async function create() {
    setError(null);
    let parameters, edits, submission_criteria;
    try {
      parameters = JSON.parse(parametersJson);
      edits = JSON.parse(editsJson);
      submission_criteria = JSON.parse(criteriaJson);
    } catch {
      setError("Parameters/Edits/Submission criteria must each be valid JSON.");
      return;
    }
    try {
      await createActionType.mutateAsync({
        name: `${targetObjectType}.${name}`,
        target_object_type: targetObjectType,
        required_permission: requiredPermission,
        risk_level: riskLevel as "low" | "high",
        description,
        parameters,
        edits,
        submission_criteria,
      });
      setCreating(false);
      reset();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Create failed");
    }
  }

  if (isLoading) return <Spinner />;

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <p style={{ fontSize: 12, color: "var(--hl-text-muted)", margin: 0, maxWidth: 560 }}>
          The no-code counterpart to writing a Python Action handler — named parameters, declarative edits, and
          submission criteria, no code to write or deploy. A <code>high</code> risk Action requires human approval
          before it applies.
        </p>
        <Button intent="primary" icon="add" onClick={() => setCreating(true)}>
          New action type
        </Button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12 }}>
        {data?.map((at) => (
          <Card key={at.name}>
            <strong
              style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
              title={at.name}
            >
              {at.name}
            </strong>
            <div style={{ marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap" }}>
              <Tag minimal>{at.target_object_type}</Tag>
              <Tag minimal intent={at.risk_level === "high" ? "warning" : "none"}>
                {at.risk_level} risk
              </Tag>
              {at.writeback_dataset && (
                <Tag minimal icon="cloud-upload">
                  writes back
                </Tag>
              )}
            </div>
            {at.description && (
              <p style={{ fontSize: 12, color: "var(--hl-text-muted)", marginTop: 8, marginBottom: 0 }}>{at.description}</p>
            )}
          </Card>
        ))}
        {data?.length === 0 && <p style={{ color: "var(--hl-text-muted)" }}>No action types yet.</p>}
      </div>

      <Dialog
        isOpen={creating}
        onClose={() => {
          setCreating(false);
          reset();
        }}
        title="New action type"
        style={{ width: 560 }}
      >
        <DialogBody>
          <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
            <div style={{ flex: 1 }}>
              <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Target ObjectType</label>
              <HTMLSelect fill value={targetObjectType} onChange={(e) => setTargetObjectType(e.target.value)}>
                <option value="">Select…</option>
                {objectTypes.map((ot) => (
                  <option key={ot.name} value={ot.name}>
                    {ot.name}
                  </option>
                ))}
              </HTMLSelect>
            </div>
            <div style={{ flex: 1 }}>
              <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Local name</label>
              <InputGroup placeholder="setPriority" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
            <div style={{ flex: 1 }}>
              <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Required permission</label>
              <InputGroup value={requiredPermission} onChange={(e) => setRequiredPermission(e.target.value)} />
            </div>
            <div style={{ flex: 1 }}>
              <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Risk level</label>
              <HTMLSelect fill value={riskLevel} onChange={(e) => setRiskLevel(e.target.value)} options={[...RISK_LEVELS]} />
            </div>
          </div>
          <div style={{ marginBottom: 12 }}>
            <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Description</label>
            <InputGroup placeholder="What invoking this Action does" value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>

          <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>
            Parameters <span className="hl-mono" style={{ color: "var(--hl-text-muted)" }}>[{"{"}name, value_type, required{"}"}, ...]</span>
          </label>
          <div className="hl-panel" style={{ padding: 0, overflow: "hidden", marginBottom: 12 }}>
            <Editor height="90px" defaultLanguage="json" theme="vs-dark" value={parametersJson} onChange={(v) => setParametersJson(v ?? "")} options={{ minimap: { enabled: false }, fontSize: 12 }} />
          </div>
          <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>
            Edits <span className="hl-mono" style={{ color: "var(--hl-text-muted)" }}>[{"{"}property, source, value|parameter_name{"}"}, ...]</span>
          </label>
          <div className="hl-panel" style={{ padding: 0, overflow: "hidden", marginBottom: 12 }}>
            <Editor height="90px" defaultLanguage="json" theme="vs-dark" value={editsJson} onChange={(v) => setEditsJson(v ?? "")} options={{ minimap: { enabled: false }, fontSize: 12 }} />
          </div>
          <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>
            Submission criteria <span className="hl-mono" style={{ color: "var(--hl-text-muted)" }}>[{"{"}property, operator, value{"}"}, ...]</span>
          </label>
          <div className="hl-panel" style={{ padding: 0, overflow: "hidden" }}>
            <Editor height="70px" defaultLanguage="json" theme="vs-dark" value={criteriaJson} onChange={(v) => setCriteriaJson(v ?? "")} options={{ minimap: { enabled: false }, fontSize: 12 }} />
          </div>

          {error && (
            <Callout intent="danger" style={{ marginTop: 12 }}>
              {error}
            </Callout>
          )}
        </DialogBody>
        <DialogFooter
          actions={
            <Button
              intent="primary"
              disabled={!name || !targetObjectType || !description}
              loading={createActionType.isPending}
              onClick={() => void create()}
            >
              Create
            </Button>
          }
        />
      </Dialog>
    </div>
  );
}
