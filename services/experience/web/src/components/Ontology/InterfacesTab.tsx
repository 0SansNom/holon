import { useMemo, useState } from "react";
import { Checkbox, FormGroup, HTMLSelect, InputGroup, Tag, TagInput } from "@blueprintjs/core";
import {
  useInterfaces,
  useCreateInterface,
  useUpdateInterface,
  useDeleteInterface,
  useObjectTypes,
  useValueTypes,
  useSharedPropertyTypes,
  useActionTypes,
} from "../../api/hooks";
import type { InterfaceType } from "../../api/knowledge";
import { CardGrid, EmptyState } from "../common/ListPrimitives";
import { RegistryDialog } from "../common/RegistryDialog";
import { usePaletteCreateIntent } from "../../hooks/usePaletteCreateIntent";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { BranchesDialog } from "./BranchesDialog";
import { InterfaceImplementationsDialog } from "./InterfaceImplementationsDialog";
import {
  formatPropertyTypeBinding,
  InterfacePropertyTypesFields,
  prunePropertyTypes,
  type InterfacePropertyTypes,
} from "./InterfacePropertyTypesFields";
import { InterfaceLinkConstraintsFields } from "./InterfaceLinkConstraintsFields";
import { OntologyTabHeader, RegistryCard } from "./OntologyTabLayout";
import type { InterfaceLinkConstraint } from "../../api/knowledge";
import { CheckboxNamePicker } from "./CheckboxNamePicker";
import { isEphemeralTestName } from "./ephemeralResources";

import { REGISTRY_LIFECYCLE_STATUSES } from "./lifecycleUtils";
import { effectiveInterfaceContract } from "./interfaceContractUtils";

const LIFECYCLE_STATUSES = REGISTRY_LIFECYCLE_STATUSES;

function actionLocalName(fullName: string): string {
  const dot = fullName.indexOf(".");
  return dot >= 0 ? fullName.slice(dot + 1) : fullName;
}

export function InterfacesTab() {
  const { data } = useInterfaces();
  const { data: objectTypes } = useObjectTypes();
  const { data: valueTypes } = useValueTypes();
  const { data: sharedPropertyTypes } = useSharedPropertyTypes();
  const { data: actionTypes = [] } = useActionTypes();
  const createInterface = useCreateInterface();
  const updateInterface = useUpdateInterface();
  const deleteInterface = useDeleteInterface();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [requiredProperties, setRequiredProperties] = useState<string[]>([]);
  const [requiredActions, setRequiredActions] = useState<string[]>([]);
  const [propertyTypes, setPropertyTypes] = useState<InterfacePropertyTypes>({});
  const [linkConstraints, setLinkConstraints] = useState<InterfaceLinkConstraint[]>([]);
  const [parentInterfaces, setParentInterfaces] = useState<string[]>([]);
  const [description, setDescription] = useState("");
  const [lifecycleStatus, setLifecycleStatus] = useState<string>("experimental");
  const [deprecationReason, setDeprecationReason] = useState("");
  const [deprecationDeadline, setDeprecationDeadline] = useState("");
  const [replacementUrn, setReplacementUrn] = useState("");
  const [editing, setEditing] = useState<InterfaceType | null>(null);
  const [editRequiredProperties, setEditRequiredProperties] = useState<string[]>([]);
  const [editRequiredActions, setEditRequiredActions] = useState<string[]>([]);
  const [editPropertyTypes, setEditPropertyTypes] = useState<InterfacePropertyTypes>({});
  const [editLinkConstraints, setEditLinkConstraints] = useState<InterfaceLinkConstraint[]>([]);
  const [editParentInterfaces, setEditParentInterfaces] = useState<string[]>([]);
  const [editDescription, setEditDescription] = useState("");
  const [editLifecycleStatus, setEditLifecycleStatus] = useState<string>("experimental");
  const [editDeprecationReason, setEditDeprecationReason] = useState("");
  const [editDeprecationDeadline, setEditDeprecationDeadline] = useState("");
  const [editReplacementUrn, setEditReplacementUrn] = useState("");
  const [branching, setBranching] = useState<InterfaceType | null>(null);
  const [viewing, setViewing] = useState<InterfaceType | null>(null);
  const [deleting, setDeleting] = useState<InterfaceType | null>(null);
  const [showEphemeral, setShowEphemeral] = useState(false);

  const ephemeralCount = useMemo(
    () => (data ?? []).filter((iface) => isEphemeralTestName(iface.name)).length,
    [data],
  );
  const visibleInterfaces = useMemo(
    () =>
      showEphemeral
        ? (data ?? [])
        : (data ?? []).filter((iface) => !isEphemeralTestName(iface.name)),
    [data, showEphemeral],
  );

  const valueTypeNames = useMemo(() => valueTypes.map((vt) => vt.name), [valueTypes]);
  const sharedPropertyTypeNames = useMemo(
    () => sharedPropertyTypes.map((spt) => spt.api_name),
    [sharedPropertyTypes],
  );
  const actionLocalNames = useMemo(() => {
    const names = new Set<string>();
    for (const action of actionTypes) {
      const local = actionLocalName(action.name);
      if (local) names.add(local);
    }
    return [...names].sort();
  }, [actionTypes]);
  const parentInterfaceOptions = useMemo(
    () => (data ?? []).map((iface) => iface.name).sort(),
    [data],
  );

  const interfacesByName = useMemo(
    () => new Map((data ?? []).map((iface) => [iface.name, iface])),
    [data],
  );

  const implementerCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const ot of objectTypes ?? []) {
      const seen = new Set<string>();
      for (const ifaceName of ot.implements ?? []) {
        const stack = [ifaceName];
        while (stack.length > 0) {
          const current = stack.pop()!;
          if (seen.has(current)) continue;
          seen.add(current);
          counts.set(current, (counts.get(current) ?? 0) + 1);
          for (const parent of interfacesByName.get(current)?.parent_interfaces ?? []) {
            stack.push(parent);
          }
        }
      }
    }
    return counts;
  }, [objectTypes, interfacesByName]);

  usePaletteCreateIntent("create-interface", setCreating);

  function resetCreate() {
    setName("");
    setRequiredProperties([]);
    setRequiredActions([]);
    setPropertyTypes({});
    setLinkConstraints([]);
    setParentInterfaces([]);
    setDescription("");
    setLifecycleStatus("experimental");
    setDeprecationReason("");
    setDeprecationDeadline("");
    setReplacementUrn("");
  }

  function closeCreate() {
    setCreating(false);
    resetCreate();
  }

  const {
    submit: submitCreate,
    error: createError,
    isPending: createPending,
  } = useAsyncAction(async () => {
    const types = prunePropertyTypes(requiredProperties, propertyTypes);
    await createInterface.mutateAsync({
      name,
      required_properties: requiredProperties,
      required_actions: requiredActions,
      property_types: Object.keys(types).length > 0 ? types : undefined,
      link_constraints: linkConstraints.length > 0 ? linkConstraints : undefined,
      parent_interfaces: parentInterfaces.length > 0 ? parentInterfaces : undefined,
      description: description || undefined,
      lifecycle_status: lifecycleStatus,
      deprecation_reason: lifecycleStatus === "deprecated" ? deprecationReason : undefined,
      deprecation_deadline: lifecycleStatus === "deprecated" ? deprecationDeadline || undefined : undefined,
      replacement_urn: lifecycleStatus === "deprecated" ? replacementUrn || undefined : undefined,
    });
    closeCreate();
  }, { successMessage: `Interface "${name}" created` });

  function openEdit(iface: InterfaceType) {
    setEditing(iface);
    setEditRequiredProperties(iface.required_properties);
    setEditRequiredActions(iface.required_actions);
    setEditPropertyTypes({ ...(iface.property_types ?? {}) });
    setEditLinkConstraints([...(iface.link_constraints ?? [])]);
    setEditParentInterfaces([...(iface.parent_interfaces ?? [])]);
    setEditDescription(iface.description ?? "");
    setEditLifecycleStatus(iface.lifecycle_status ?? "experimental");
    setEditDeprecationReason(iface.deprecation_reason ?? "");
    setEditDeprecationDeadline((iface.deprecation_deadline ?? "").toString().slice(0, 10));
    setEditReplacementUrn(iface.replacement_urn ?? "");
  }

  const {
    submit: submitEdit,
    error: editError,
    isPending: editPending,
  } = useAsyncAction(async () => {
    if (!editing) return;
    const types = prunePropertyTypes(editRequiredProperties, editPropertyTypes);
    await updateInterface.mutateAsync({
      name: editing.name,
      body: {
        required_properties: editRequiredProperties,
        required_actions: editRequiredActions,
        property_types: types,
        link_constraints: editLinkConstraints,
        parent_interfaces: editParentInterfaces,
        description: editDescription,
        lifecycle_status: editLifecycleStatus,
        deprecation_reason: editLifecycleStatus === "deprecated" ? editDeprecationReason : undefined,
        deprecation_deadline: editLifecycleStatus === "deprecated" ? editDeprecationDeadline || undefined : undefined,
        replacement_urn: editLifecycleStatus === "deprecated" ? editReplacementUrn || undefined : undefined,
      },
    });
    setEditing(null);
  }, { successMessage: `"${editing?.name ?? "Interface"}" saved` });

  const {
    submit: submitDelete,
    error: deleteError,
    isPending: deletePending,
  } = useAsyncAction(async () => {
    if (!deleting) return;
    const deletedName = deleting.name;
    await deleteInterface.mutateAsync(deletedName);
    setDeleting(null);
  }, { successMessage: `Deleted "${deleting?.name ?? "interface"}"` });

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            A named, checked contract — an ObjectType declaring <code>implements</code> must actually have every
            required property mapped and every required Action defined, checked at publish time, not just a label.
            Optional VT/SPT bindings tighten the type check. Use the layers button to browse implementations.
          </>
        }
        createLabel="New interface"
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

      <CardGrid>
        {visibleInterfaces.map((iface) => {
          const implementerCount = implementerCounts.get(iface.name) ?? 0;
          const effective = effectiveInterfaceContract(iface, interfacesByName);
          const types = effective.property_types;
          const localProps = new Set(iface.required_properties);
          const localActions = new Set(iface.required_actions);
          return (
            <RegistryCard
              key={iface.name}
              name={iface.name}
              onView={() => setViewing(iface)}
              onEdit={() => openEdit(iface)}
              onBranch={() => setBranching(iface)}
              onDelete={() => setDeleting(iface)}
            >
              <div className="hl-tag-row hl-mt-xs">
                <Tag minimal intent={iface.lifecycle_status === "deprecated" ? "warning" : "none"}>
                  {iface.lifecycle_status ?? "experimental"}
                </Tag>
                <Tag minimal icon="layers">
                  {implementerCount} implementer{implementerCount === 1 ? "" : "s"}
                </Tag>
                {(iface.parent_interfaces?.length ?? 0) > 0 && (
                  <Tag minimal icon="diagram-tree">
                    extends {iface.parent_interfaces!.join(", ")}
                  </Tag>
                )}
              </div>
              {effective.required_properties.length > 0 && (
                <div className="hl-mt-xs">
                  <div className="hl-text-muted-sm">Requires properties</div>
                  <div className="hl-tag-row hl-mt-xs">
                    {effective.required_properties.map((p) => {
                      const binding = formatPropertyTypeBinding(types[p]);
                      const inherited = !localProps.has(p);
                      return (
                        <Tag key={p} minimal intent={inherited ? "primary" : "none"}>
                          {p}
                          {binding ? ` · ${binding}` : ""}
                          {inherited ? " · inherited" : ""}
                        </Tag>
                      );
                    })}
                  </div>
                </div>
              )}
              {effective.required_actions.length > 0 && (
                <div className="hl-mt-xs">
                  <div className="hl-text-muted-sm">Requires actions</div>
                  <div className="hl-tag-row hl-mt-xs">
                    {effective.required_actions.map((a) => (
                      <Tag key={a} minimal icon="lightning" intent={localActions.has(a) ? "none" : "primary"}>
                        {a}
                        {localActions.has(a) ? "" : " · inherited"}
                      </Tag>
                    ))}
                  </div>
                </div>
              )}
              {effective.link_constraints.length > 0 && (
                <div className="hl-mt-xs">
                  <div className="hl-text-muted-sm">Link constraints</div>
                  <div className="hl-tag-row hl-mt-xs">
                    {effective.link_constraints.map((c) => (
                      <Tag key={c.api_name} minimal icon="graph">
                        {c.api_name} → {c.target} ({c.cardinality}
                        {c.required ? "" : ", optional"})
                      </Tag>
                    ))}
                  </div>
                </div>
              )}
              {iface.description && <p className="hl-card-desc">{iface.description}</p>}
            </RegistryCard>
          );
        })}
        {visibleInterfaces.length === 0 && (
          <EmptyState actionLabel="New interface" onAction={() => setCreating(true)}>
            {(data ?? []).length === 0
              ? "No interfaces yet."
              : "No durable interfaces — show test leftovers to browse pytest interfaces."}
          </EmptyState>
        )}
      </CardGrid>

      <RegistryDialog
        isOpen={creating}
        title="New interface"
        onClose={closeCreate}
        error={createError}
        isPending={createPending}
        submitLabel="Create"
        submitDisabled={!name}
        onSubmit={() => submitCreate(undefined)}
        style={{ width: 560 }}
      >
        <FormGroup label="Name">
          <InputGroup placeholder="Contactable" value={name} onChange={(e) => setName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Extends">
          <CheckboxNamePicker
            options={parentInterfaceOptions.filter((n) => n !== name)}
            values={parentInterfaces}
            onChange={setParentInterfaces}
            emptyHint="Create another interface first to extend it."
          />
        </FormGroup>
        <FormGroup
          label="Required properties"
          helperText="Free-form API names, or pick a Shared Property Type below to add its api_name."
        >
          <TagInput
            placeholder="type a property name, press Enter"
            values={requiredProperties}
            onChange={(values) => {
              const next = values as string[];
              setRequiredProperties(next);
              setPropertyTypes(prunePropertyTypes(next, propertyTypes));
            }}
          />
          {sharedPropertyTypeNames.length > 0 && (
            <HTMLSelect
              fill
              className="hl-mt-xs"
              value=""
              onChange={(e) => {
                const apiName = e.target.value;
                if (!apiName || requiredProperties.includes(apiName)) return;
                const next = [...requiredProperties, apiName];
                setRequiredProperties(next);
                setPropertyTypes({
                  ...prunePropertyTypes(next, propertyTypes),
                  [apiName]: { kind: "shared_property_type", shared_property_type: apiName },
                });
              }}
            >
              <option value="">Add Shared Property Type…</option>
              {sharedPropertyTypeNames.map((apiName) => (
                <option key={apiName} value={apiName} disabled={requiredProperties.includes(apiName)}>
                  {apiName}
                </option>
              ))}
            </HTMLSelect>
          )}
        </FormGroup>
        <FormGroup label="Typed bindings">
          <InterfacePropertyTypesFields
            requiredProperties={requiredProperties}
            propertyTypes={propertyTypes}
            onChange={setPropertyTypes}
            valueTypeNames={valueTypeNames}
            sharedPropertyTypeNames={sharedPropertyTypeNames}
          />
        </FormGroup>
        <FormGroup label="Link constraints">
          <InterfaceLinkConstraintsFields
            constraints={linkConstraints}
            onChange={setLinkConstraints}
            objectTypeNames={objectTypes.map((ot) => ot.name)}
            interfaceNames={(data ?? []).map((iface) => iface.name)}
          />
        </FormGroup>
        <FormGroup label="Required actions" helperText="Local ActionType names (suffix after the first dot).">
          <CheckboxNamePicker
            options={actionLocalNames}
            values={requiredActions}
            onChange={setRequiredActions}
            emptyHint="No ActionTypes registered yet — create one under Action Types."
          />
        </FormGroup>
        <FormGroup label="Description">
          <InputGroup placeholder="Anything with a reachable contact method" value={description} onChange={(e) => setDescription(e.target.value)} />
        </FormGroup>
        <FormGroup label="Status">
          <HTMLSelect fill value={lifecycleStatus} onChange={(e) => setLifecycleStatus(e.target.value)}>
            {LIFECYCLE_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        {lifecycleStatus === "deprecated" && (
          <>
            <FormGroup label="Deprecation reason">
              <InputGroup value={deprecationReason} onChange={(e) => setDeprecationReason(e.target.value)} />
            </FormGroup>
            <FormGroup label="Deprecation deadline">
              <InputGroup type="date" value={deprecationDeadline} onChange={(e) => setDeprecationDeadline(e.target.value)} />
            </FormGroup>
            <FormGroup label="Replacement URN (optional)">
              <InputGroup className="hl-mono" value={replacementUrn} onChange={(e) => setReplacementUrn(e.target.value)} />
            </FormGroup>
          </>
        )}
      </RegistryDialog>

      <RegistryDialog
        isOpen={editing !== null}
        title={`Edit ${editing?.name ?? ""}`}
        onClose={() => setEditing(null)}
        error={editError}
        isPending={editPending}
        submitLabel="Save"
        onSubmit={() => submitEdit(undefined)}
        style={{ width: 560 }}
      >
        <p style={{ fontSize: 12, color: "var(--hl-text-muted)" }}>
          Name isn't editable — it's the key referenced from every ObjectType's <code>implements</code> list.
          Tightening required properties, actions, typed bindings, link constraints, or parents is blocked if any
          published implementer would break.
        </p>
        <FormGroup label="Extends">
          <CheckboxNamePicker
            options={parentInterfaceOptions.filter((n) => n !== editing?.name)}
            values={editParentInterfaces}
            onChange={setEditParentInterfaces}
            emptyHint="No other interfaces to extend."
          />
        </FormGroup>
        <FormGroup
          label="Required properties"
          helperText="Free-form API names, or pick a Shared Property Type below to add its api_name."
        >
          <TagInput
            placeholder="type a property name, press Enter"
            values={editRequiredProperties}
            onChange={(values) => {
              const next = values as string[];
              setEditRequiredProperties(next);
              setEditPropertyTypes(prunePropertyTypes(next, editPropertyTypes));
            }}
          />
          {sharedPropertyTypeNames.length > 0 && (
            <HTMLSelect
              fill
              className="hl-mt-xs"
              value=""
              onChange={(e) => {
                const apiName = e.target.value;
                if (!apiName || editRequiredProperties.includes(apiName)) return;
                const next = [...editRequiredProperties, apiName];
                setEditRequiredProperties(next);
                setEditPropertyTypes({
                  ...prunePropertyTypes(next, editPropertyTypes),
                  [apiName]: { kind: "shared_property_type", shared_property_type: apiName },
                });
              }}
            >
              <option value="">Add Shared Property Type…</option>
              {sharedPropertyTypeNames.map((apiName) => (
                <option key={apiName} value={apiName} disabled={editRequiredProperties.includes(apiName)}>
                  {apiName}
                </option>
              ))}
            </HTMLSelect>
          )}
        </FormGroup>
        <FormGroup label="Typed bindings">
          <InterfacePropertyTypesFields
            requiredProperties={editRequiredProperties}
            propertyTypes={editPropertyTypes}
            onChange={setEditPropertyTypes}
            valueTypeNames={valueTypeNames}
            sharedPropertyTypeNames={sharedPropertyTypeNames}
          />
        </FormGroup>
        <FormGroup label="Link constraints">
          <InterfaceLinkConstraintsFields
            constraints={editLinkConstraints}
            onChange={setEditLinkConstraints}
            objectTypeNames={objectTypes.map((ot) => ot.name)}
            interfaceNames={(data ?? []).map((iface) => iface.name)}
          />
        </FormGroup>
        <FormGroup label="Required actions" helperText="Local ActionType names (suffix after the first dot).">
          <CheckboxNamePicker
            options={[...new Set([...actionLocalNames, ...editRequiredActions])].sort()}
            values={editRequiredActions}
            onChange={setEditRequiredActions}
            emptyHint="No ActionTypes registered yet — create one under Action Types."
          />
        </FormGroup>
        <FormGroup label="Description">
          <InputGroup value={editDescription} onChange={(e) => setEditDescription(e.target.value)} />
        </FormGroup>
        <FormGroup label="Status">
          <HTMLSelect fill value={editLifecycleStatus} onChange={(e) => setEditLifecycleStatus(e.target.value)}>
            {LIFECYCLE_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        {editLifecycleStatus === "deprecated" && (
          <>
            <FormGroup label="Deprecation reason">
              <InputGroup value={editDeprecationReason} onChange={(e) => setEditDeprecationReason(e.target.value)} />
            </FormGroup>
            <FormGroup label="Deprecation deadline">
              <InputGroup type="date" value={editDeprecationDeadline} onChange={(e) => setEditDeprecationDeadline(e.target.value)} />
            </FormGroup>
            <FormGroup label="Replacement URN (optional)">
              <InputGroup className="hl-mono" value={editReplacementUrn} onChange={(e) => setEditReplacementUrn(e.target.value)} />
            </FormGroup>
          </>
        )}
      </RegistryDialog>

      {viewing && <InterfaceImplementationsDialog iface={viewing} onClose={() => setViewing(null)} />}

      <RegistryDialog
        isOpen={!!deleting}
        title={`Delete ${deleting?.name ?? ""}`}
        onClose={() => setDeleting(null)}
        error={deleteError}
        isPending={deletePending}
        onSubmit={() => submitDelete(undefined)}
        submitLabel="Delete"
        intent="danger"
      >
        <p>
          Delete interface <Tag minimal className="hl-mono">{deleting?.name}</Tag>? ObjectTypes must stop
          implementing it (and children must stop extending it) first. Active interfaces must be deprecated
          before delete.
        </p>
        {(deleting?.lifecycle_status ?? "experimental") === "active" && (
          <p className="hl-text-muted-sm">This interface is still <code>active</code> — deprecate it first.</p>
        )}
      </RegistryDialog>

      {branching && (
        <BranchesDialog
          kind="interface_type"
          resourceName={branching.name}
          currentDefinition={{
            required_properties: branching.required_properties,
            required_actions: branching.required_actions,
            property_types: branching.property_types ?? {},
            link_constraints: branching.link_constraints ?? [],
            parent_interfaces: branching.parent_interfaces ?? [],
            description: branching.description,
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
