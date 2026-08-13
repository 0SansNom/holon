import { useMemo, useState } from "react";
import { Checkbox } from "@blueprintjs/core";
import { useActionTypes, useCreateActionType, useUpdateActionType, useObjectTypes, useInterfaces } from "../../api/hooks";
import type { ActionType } from "../../api/knowledge";
import { CardGrid, EmptyState } from "../common/ListPrimitives";
import { RegistryDialog } from "../common/RegistryDialog";
import { usePaletteCreateIntent } from "../../hooks/usePaletteCreateIntent";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { BranchesDialog } from "./BranchesDialog";
import { OntologyTabHeader } from "./OntologyTabLayout";
import { ActionTypeCard } from "./ActionTypeCard";
import { ActionTypeFormFields } from "./ActionTypeFormFields";
import {
  DEFAULT_ACTION_TYPE_FORM,
  actionTypeFormFromRecord,
  isActionTypeCreateValid,
  parseActionTypeJsonFields,
  type ActionTypeFormState,
} from "./actionTypeForm";
import { isEphemeralTestName } from "./ephemeralResources";
import { parseTypeClassesInput } from "./typeClassUtils";

export function ActionTypesTab() {
  const { data } = useActionTypes();
  const { data: objectTypes } = useObjectTypes();
  const { data: interfaces } = useInterfaces();
  const createActionType = useCreateActionType();
  const updateActionType = useUpdateActionType();

  const [creating, setCreating] = useState(false);
  const [createForm, setCreateForm] = useState<ActionTypeFormState>(DEFAULT_ACTION_TYPE_FORM);
  const [editing, setEditing] = useState<ActionType | null>(null);
  const [editForm, setEditForm] = useState<ActionTypeFormState>(DEFAULT_ACTION_TYPE_FORM);
  const [branching, setBranching] = useState<ActionType | null>(null);
  const [showEphemeral, setShowEphemeral] = useState(false);

  const ephemeralCount = useMemo(
    () => data.filter((at) => isEphemeralTestName(at.name)).length,
    [data],
  );
  const visibleActions = useMemo(
    () => (showEphemeral ? data : data.filter((at) => !isEphemeralTestName(at.name))),
    [data, showEphemeral],
  );

  usePaletteCreateIntent("create-action-type", setCreating);

  function resetCreate() {
    setCreateForm(DEFAULT_ACTION_TYPE_FORM);
  }

  function closeCreate() {
    setCreating(false);
    resetCreate();
  }

  function patchCreate(patch: Partial<ActionTypeFormState>) {
    setCreateForm((prev) => ({ ...prev, ...patch }));
  }

  function patchEdit(patch: Partial<ActionTypeFormState>) {
    setEditForm((prev) => ({ ...prev, ...patch }));
  }

  const {
    submit: submitCreate,
    error: createError,
    isPending: createPending,
  } = useAsyncAction(async () => {
    const parsed = parseActionTypeJsonFields(createForm);
    if (!parsed.ok) throw new Error(parsed.error);
    const target = createForm.targetKind === "object_type" ? createForm.targetObjectType : createForm.targetInterface;
    await createActionType.mutateAsync({
      name: `${target}.${createForm.localName}`,
      target_object_type: createForm.targetKind === "object_type" ? createForm.targetObjectType : undefined,
      target_interface: createForm.targetKind === "interface" ? createForm.targetInterface : undefined,
      required_permission: createForm.requiredPermission,
      risk_level: createForm.riskLevel as "low" | "high",
      description: createForm.description,
      parameters: parsed.parameters,
      edits: parsed.edits,
      submission_criteria: parsed.submission_criteria,
      edit_function: createForm.editsKind === "function" ? createForm.editFunctionName : undefined,
      sections: parsed.sections,
      function_side_effect: createForm.functionSideEffect?.trim() || undefined,
      writeback_dataset: createForm.writebackDataset?.trim() || undefined,
      notify_webhook: createForm.notifyWebhook?.trim() || undefined,
      type_classes: parseTypeClassesInput(createForm.typeClasses),
      lifecycle_status: createForm.lifecycleStatus,
      deprecation_reason: createForm.lifecycleStatus === "deprecated" ? createForm.deprecationReason : undefined,
      deprecation_deadline: createForm.lifecycleStatus === "deprecated" ? createForm.deprecationDeadline || undefined : undefined,
      replacement_urn: createForm.lifecycleStatus === "deprecated" ? createForm.replacementUrn || undefined : undefined,
    });
    closeCreate();
  }, { successMessage: `Action type "${createForm.targetKind === "object_type" ? createForm.targetObjectType : createForm.targetInterface}.${createForm.localName}" created` });

  function openEdit(at: ActionType) {
    setEditing(at);
    setEditForm(actionTypeFormFromRecord(at));
  }

  const {
    submit: submitEdit,
    error: editError,
    isPending: editPending,
  } = useAsyncAction(async () => {
    if (!editing) return;
    const parsed = parseActionTypeJsonFields(editForm);
    if (!parsed.ok) throw new Error(parsed.error);
    await updateActionType.mutateAsync({
      name: editing.name,
      body: {
        name: editing.name,
        target_object_type: editing.target_object_type ?? undefined,
        target_interface: editing.target_interface ?? undefined,
        required_permission: editForm.requiredPermission,
        risk_level: editForm.riskLevel as "low" | "high",
        description: editForm.description,
        parameters: parsed.parameters,
        edits: parsed.edits,
        submission_criteria: parsed.submission_criteria,
        function_side_effect: editForm.functionSideEffect?.trim() || undefined,
        writeback_dataset: editForm.writebackDataset?.trim() || undefined,
        notify_webhook: editForm.notifyWebhook?.trim() || undefined,
        edit_function: editForm.editsKind === "function" ? editForm.editFunctionName : undefined,
        sections: parsed.sections,
        type_classes: parseTypeClassesInput(editForm.typeClasses),
        lifecycle_status: editForm.lifecycleStatus,
        deprecation_reason: editForm.lifecycleStatus === "deprecated" ? editForm.deprecationReason : undefined,
        deprecation_deadline: editForm.lifecycleStatus === "deprecated" ? editForm.deprecationDeadline || undefined : undefined,
        replacement_urn: editForm.lifecycleStatus === "deprecated" ? editForm.replacementUrn || undefined : undefined,
      },
    });
    setEditing(null);
  }, { successMessage: `"${editing?.name ?? "Action type"}" saved` });

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            The no-code counterpart to writing a Python Action handler — named parameters, declarative edits, and
            submission criteria, no code to write or deploy. A <code>high</code> risk Action requires human approval
            before it applies.
          </>
        }
        createLabel="New action type"
        onCreate={() => setCreating(true)}
        trailing={
          ephemeralCount > 0 ? (
            <Checkbox
              checked={showEphemeral}
              label={`Show test leftovers (${ephemeralCount})`}
              onChange={(e) => setShowEphemeral(e.currentTarget.checked)}
              style={{ marginBottom: 0 }}
            />
          ) : undefined
        }
      />

      <CardGrid minWidth={260}>
        {visibleActions.map((at) => (
          <ActionTypeCard key={at.name} actionType={at} onEdit={() => openEdit(at)} onBranch={() => setBranching(at)} />
        ))}
        {visibleActions.length === 0 && (
          <EmptyState actionLabel="New action type" onAction={() => setCreating(true)}>
            {data.length === 0 ? "No action types yet." : "No durable action types — show test leftovers to browse pytest actions."}
          </EmptyState>
        )}
      </CardGrid>

      <RegistryDialog
        isOpen={creating}
        title="New action type"
        style={{ width: 560 }}
        onClose={closeCreate}
        error={createError}
        isPending={createPending}
        submitLabel="Create"
        submitDisabled={!isActionTypeCreateValid(createForm)}
        onSubmit={() => submitCreate(undefined)}
      >
        <ActionTypeFormFields
          mode="create"
          value={createForm}
          onChange={patchCreate}
          objectTypes={objectTypes}
          interfaces={interfaces}
        />
      </RegistryDialog>

      <RegistryDialog
        isOpen={editing !== null}
        title={`Edit ${editing?.name ?? ""}`}
        style={{ width: 560 }}
        onClose={() => setEditing(null)}
        error={editError}
        isPending={editPending}
        submitLabel="Save"
        onSubmit={() => submitEdit(undefined)}
      >
        <ActionTypeFormFields
          mode="edit"
          value={editForm}
          onChange={patchEdit}
          objectTypes={objectTypes}
          interfaces={interfaces}
          fixedName={editing?.name}
        />
      </RegistryDialog>

      {branching && (
        <BranchesDialog
          kind="action_type"
          resourceName={branching.name}
          currentDefinition={{
            target_object_type: branching.target_object_type,
            target_interface: branching.target_interface,
            required_permission: branching.required_permission,
            risk_level: branching.risk_level,
            description: branching.description,
            parameters: branching.parameters,
            edits: branching.edits,
            submission_criteria: branching.submission_criteria,
            function_side_effect: branching.function_side_effect,
            writeback_dataset: branching.writeback_dataset,
            edit_function: branching.edit_function,
            sections: branching.sections,
            type_classes: branching.type_classes ?? [],
            lifecycle_status: branching.lifecycle_status,
            deprecation_reason: branching.deprecation_reason,
            deprecation_deadline: branching.deprecation_deadline,
            replacement_urn: branching.replacement_urn,
          }}
          onClose={() => setBranching(null)}
        />
      )}
    </div>
  );
}
