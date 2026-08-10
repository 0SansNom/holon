import type { ActionType } from "../../api/knowledge";

export const RISK_LEVELS = ["low", "high"] as const;
export const DEFAULT_EDITS = '[\n  { "property": "reviewStatus", "source": "literal", "value": "reviewed" }\n]';
export const DEFAULT_PARAMETERS = "[]";
export const DEFAULT_CRITERIA = "[]";
export const DEFAULT_SECTIONS = "[]";

export interface ActionTypeFormState {
  targetKind: "object_type" | "interface";
  targetObjectType: string;
  targetInterface: string;
  localName: string;
  requiredPermission: string;
  riskLevel: string;
  description: string;
  parametersJson: string;
  editsKind: "declarative" | "function";
  editsJson: string;
  editFunctionName: string;
  criteriaJson: string;
  sectionsJson: string;
  functionSideEffect?: string;
  writebackDataset?: string;
}

export const DEFAULT_ACTION_TYPE_FORM: ActionTypeFormState = {
  targetKind: "object_type",
  targetObjectType: "",
  targetInterface: "",
  localName: "",
  requiredPermission: "write",
  riskLevel: "low",
  description: "",
  parametersJson: DEFAULT_PARAMETERS,
  editsKind: "declarative",
  editsJson: DEFAULT_EDITS,
  editFunctionName: "",
  criteriaJson: DEFAULT_CRITERIA,
  sectionsJson: DEFAULT_SECTIONS,
};

export function actionTypeFormFromRecord(at: ActionType): ActionTypeFormState {
  return {
    targetKind: at.target_interface ? "interface" : "object_type",
    targetObjectType: at.target_object_type ?? "",
    targetInterface: at.target_interface ?? "",
    localName: at.name.includes(".") ? at.name.split(".").slice(1).join(".") : at.name,
    requiredPermission: at.required_permission,
    riskLevel: at.risk_level,
    description: at.description ?? "",
    parametersJson: JSON.stringify(at.parameters ?? [], null, 2),
    editsKind: at.edit_function ? "function" : "declarative",
    editsJson: JSON.stringify(at.edits ?? [], null, 2),
    editFunctionName: at.edit_function ?? "",
    criteriaJson: JSON.stringify(at.submission_criteria ?? [], null, 2),
    sectionsJson: JSON.stringify(at.sections ?? [], null, 2),
    functionSideEffect: at.function_side_effect ?? undefined,
    writebackDataset: at.writeback_dataset ?? undefined,
  };
}

export function parseActionTypeJsonFields(state: Pick<
  ActionTypeFormState,
  "parametersJson" | "editsKind" | "editsJson" | "criteriaJson" | "sectionsJson"
>) {
  try {
    const parameters = JSON.parse(state.parametersJson);
    const edits = state.editsKind === "declarative" ? JSON.parse(state.editsJson) : [];
    const submission_criteria = JSON.parse(state.criteriaJson);
    const sections = JSON.parse(state.sectionsJson);
    return { ok: true as const, parameters, edits, submission_criteria, sections };
  } catch {
    return { ok: false as const, error: "Parameters/Edits/Submission criteria/Sections must each be valid JSON." };
  }
}

export function isActionTypeCreateValid(state: ActionTypeFormState): boolean {
  const hasTarget = state.targetKind === "object_type" ? !!state.targetObjectType : !!state.targetInterface;
  return !!(
    state.localName &&
    state.description &&
    hasTarget &&
    (state.editsKind !== "function" || state.editFunctionName)
  );
}
