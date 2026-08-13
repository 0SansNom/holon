import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { Checkbox, FormGroup, HTMLSelect, InputGroup, Tag, Button, Callout } from "@blueprintjs/core";
import {
  useObjectSets,
  useCreateObjectSet,
  useUpdateObjectSet,
  useEvaluateObjectSet,
  useObjectTypes,
} from "../../api/hooks";
import type { ObjectSet } from "../../api/knowledge";
import { CardGrid, EmptyState } from "../common/ListPrimitives";
import { RegistryDialog } from "../common/RegistryDialog";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { usePaletteIntentStore } from "../../store/paletteIntent";
import { objectSetBrowsePath, urnShortName } from "../ObjectExplorer/objectExplorerUtils";
import { isEphemeralTestName } from "./ephemeralResources";
import { OntologyTabHeader, RegistryCard } from "./OntologyTabLayout";
import { PredicateFilterRows } from "./PredicateFilterRows";
import {
  buildPredicateDefinition,
  expandFilterPropertyKeys,
  predicateValueToInput,
  type PredicateFormRow,
} from "./objectSetPredicates";
import { REGISTRY_LIFECYCLE_STATUSES } from "./lifecycleUtils";

const LIFECYCLES = REGISTRY_LIFECYCLE_STATUSES;
const VISIBILITIES = ["prominent", "normal", "hidden"] as const;

function EvaluatePanel({ name, objectType }: { name: string; objectType: string }) {
  const { data, isFetching, error, refetch } = useEvaluateObjectSet(name, true);
  return (
    <div className="hl-mt-sm">
      <div className="hl-flex-between hl-mb-xs">
        <span className="hl-text-muted-sm">Evaluation (PDP-gated)</span>
        <Button small minimal icon="refresh" loading={isFetching} onClick={() => void refetch()}>
          Refresh
        </Button>
      </div>
      {error && (
        <Callout intent="danger" className="hl-mb-xs">
          {(error as Error).message}
        </Callout>
      )}
      {data && (
        <p className="hl-text-muted-sm">
          {data.count} object{data.count === 1 ? "" : "s"} of type {data.object_type}
          {data.items.slice(0, 5).map((item) => (
            <Link
              key={String(item.id)}
              to="/objects/$type/$id"
              params={{ type: objectType, id: String(item.id) }}
              className="hl-ml-xs"
              onClick={(e) => e.stopPropagation()}
            >
              <Tag minimal interactive>
                {(item.title as string | undefined) ?? String(item.id)}
              </Tag>
            </Link>
          ))}
          {data.count > 5 && <Tag minimal className="hl-ml-xs">+{data.count - 5}</Tag>}
        </p>
      )}
    </div>
  );
}

function toFormPredicates(
  definition: ObjectSet["definition"] | undefined,
  fallbackProperty: string,
): PredicateFormRow[] {
  const preds = definition?.all ?? [];
  if (preds.length === 0) {
    return [{ property: fallbackProperty, op: "eq", value: "" }];
  }
  return preds.map((p) => ({
    property: p.property,
    op: p.op,
    value: predicateValueToInput(p.op, p.value),
  }));
}

function buildDefinition(formPreds: PredicateFormRow[]) {
  return buildPredicateDefinition(formPreds, { requireValue: false });
}

export function ObjectSetsTab() {
  const navigate = useNavigate();
  const { data } = useObjectSets();
  const { data: objectTypes } = useObjectTypes();
  const createObjectSet = useCreateObjectSet();
  const updateObjectSet = useUpdateObjectSet();
  const intent = usePaletteIntentStore((s) => s.intent);
  const consumeIntent = usePaletteIntentStore((s) => s.consume);

  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [objectType, setObjectType] = useState("");
  const [predicates, setPredicates] = useState<Array<{ property: string; op: string; value: string }>>([
    { property: "", op: "eq", value: "" },
  ]);
  const [lifecycleStatus, setLifecycleStatus] = useState<string>("experimental");
  const [visibility, setVisibility] = useState<string>("normal");
  const [editing, setEditing] = useState<ObjectSet | null>(null);
  const [editDisplayName, setEditDisplayName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editLifecycle, setEditLifecycle] = useState("experimental");
  const [editVisibility, setEditVisibility] = useState("normal");
  const [editPredicates, setEditPredicates] = useState<Array<{ property: string; op: string; value: string }>>([]);
  const [evaluating, setEvaluating] = useState<string | null>(null);
  const [showEphemeral, setShowEphemeral] = useState(false);

  const ephemeralCount = useMemo(
    () => (data ?? []).filter((os) => isEphemeralTestName(os.name)).length,
    [data],
  );
  const visibleSets = useMemo(
    () =>
      showEphemeral ? (data ?? []) : (data ?? []).filter((os) => !isEphemeralTestName(os.name)),
    [data, showEphemeral],
  );

  const selectedOt = (objectTypes ?? []).find((ot) => ot.name === objectType);
  const propertyKeys = expandFilterPropertyKeys(selectedOt?.property_mapping, selectedOt?.property_types);
  const editingOtName = editing ? urnShortName(editing.object_type_urn) : "";
  const editingOt = (objectTypes ?? []).find((ot) => ot.name === editingOtName);
  const editPropertyKeys = expandFilterPropertyKeys(editingOt?.property_mapping, editingOt?.property_types);

  useEffect(() => {
    if (intent === "create-object-set") {
      setCreating(true);
      consumeIntent();
    }
  }, [intent, consumeIntent]);

  function resetCreate() {
    setName("");
    setDisplayName("");
    setDescription("");
    setObjectType("");
    setPredicates([{ property: "", op: "eq", value: "" }]);
    setLifecycleStatus("experimental");
    setVisibility("normal");
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
    await createObjectSet.mutateAsync({
      name,
      object_type: objectType,
      display_name: displayName || undefined,
      description: description || undefined,
      lifecycle_status: lifecycleStatus,
      visibility,
      definition: buildDefinition(predicates),
    });
    closeCreate();
  }, { successMessage: `Object set "${name}" created` });

  function openEdit(os: ObjectSet) {
    setEditing(os);
    setEditDisplayName(os.display_name ?? "");
    setEditDescription(os.description ?? "");
    setEditLifecycle(os.lifecycle_status ?? "experimental");
    setEditVisibility(os.visibility ?? "normal");
    const otName = urnShortName(os.object_type_urn);
    const ot = (objectTypes ?? []).find((o) => o.name === otName);
    const keys = expandFilterPropertyKeys(ot?.property_mapping, ot?.property_types);
    setEditPredicates(toFormPredicates(os.definition, keys[0] ?? ""));
  }

  const {
    submit: submitEdit,
    error: editError,
    isPending: editPending,
  } = useAsyncAction(async () => {
    if (!editing) return;
    await updateObjectSet.mutateAsync({
      name: editing.name,
      body: {
        display_name: editDisplayName,
        description: editDescription,
        lifecycle_status: editLifecycle,
        visibility: editVisibility,
        definition: buildDefinition(editPredicates),
      },
    });
    setEditing(null);
  }, { successMessage: `"${editing?.name ?? "Object set"}" saved` });

  function browseSet(os: ObjectSet) {
    const typeName = urnShortName(os.object_type_urn);
    const path = objectSetBrowsePath(typeName, os.name);
    void navigate({ to: path.to, params: path.params, search: path.search });
  }

  const createReady = !!name && !!objectType && predicates.some((p) => p.property);

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            Filtered collections of object instances (Foundry Object Sets). Predicates run through the same PDP-gated
            resolve path as Object Explorer — markings and classification always apply.
          </>
        }
        createLabel="New object set"
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
        {visibleSets.map((os) => {
          const typeName = urnShortName(os.object_type_urn);
          return (
            <RegistryCard key={os.urn} name={os.display_name || os.name} onEdit={() => openEdit(os)}>
              <div className="hl-tag-row hl-mt-xs">
                <Tag minimal>{typeName}</Tag>
                <Tag minimal intent={os.lifecycle_status === "active" ? "success" : "none"}>
                  {os.lifecycle_status}
                </Tag>
                {os.visibility !== "normal" && <Tag minimal>{os.visibility}</Tag>}
              </div>
              {(os.definition?.all ?? []).length > 0 ? (
                <div className="hl-tag-row hl-mt-xs">
                  {os.definition.all.map((pred, i) => (
                    <Tag key={i} minimal className="hl-mono">
                      {pred.property} {pred.op}{" "}
                      {Array.isArray(pred.value) ? pred.value.join(",") : String(pred.value)}
                    </Tag>
                  ))}
                </div>
              ) : (
                <p className="hl-card-desc">All {typeName} instances</p>
              )}
              {os.description && <p className="hl-card-desc">{os.description}</p>}
              <div className="hl-flex-row hl-gap-xs hl-mt-xs">
                <Button small minimal icon="th" onClick={() => browseSet(os)}>
                  Browse
                </Button>
                <Button
                  small
                  minimal
                  icon="search"
                  onClick={() => setEvaluating(evaluating === os.name ? null : os.name)}
                >
                  {evaluating === os.name ? "Hide objects" : "Evaluate"}
                </Button>
              </div>
              {evaluating === os.name && <EvaluatePanel name={os.name} objectType={typeName} />}
            </RegistryCard>
          );
        })}
        {visibleSets.length === 0 && (
          <EmptyState actionLabel="New object set" onAction={() => setCreating(true)}>
            {(data ?? []).length === 0 ? "No object sets yet." : "No durable object sets — show test leftovers to browse pytest sets."}
          </EmptyState>
        )}
      </CardGrid>

      <RegistryDialog
        isOpen={creating}
        title="New object set"
        onClose={closeCreate}
        error={createError}
        isPending={createPending}
        submitLabel="Create"
        submitDisabled={!createReady}
        onSubmit={() => submitCreate(undefined)}
      >
        <FormGroup label="Name">
          <InputGroup value={name} onChange={(e) => setName(e.target.value)} placeholder="ActiveCustomers" />
        </FormGroup>
        <FormGroup label="Display name">
          <InputGroup value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Description">
          <InputGroup value={description} onChange={(e) => setDescription(e.target.value)} />
        </FormGroup>
        <FormGroup label="Object type">
          <HTMLSelect
            fill
            value={objectType}
            onChange={(e) => {
              const next = e.target.value;
              setObjectType(next);
              const ot = (objectTypes ?? []).find((o) => o.name === next);
              const keys = expandFilterPropertyKeys(ot?.property_mapping, ot?.property_types);
              setPredicates([{ property: keys[0] ?? "", op: "eq", value: "" }]);
            }}
          >
            <option value="">Select…</option>
            {(objectTypes ?? []).map((ot) => (
              <option key={ot.name} value={ot.name}>
                {ot.name}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        <FormGroup label="Predicates (AND)" helperText="All predicates must match. Empty list = all instances of the type.">
          <PredicateFilterRows predicates={predicates} propertyKeys={propertyKeys} onChange={setPredicates} />
        </FormGroup>
        <FormGroup label="Lifecycle">
          <HTMLSelect fill value={lifecycleStatus} onChange={(e) => setLifecycleStatus(e.target.value)}>
            {LIFECYCLES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        <FormGroup label="Visibility">
          <HTMLSelect fill value={visibility} onChange={(e) => setVisibility(e.target.value)}>
            {VISIBILITIES.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
      </RegistryDialog>

      <RegistryDialog
        isOpen={!!editing}
        title={`Edit ${editing?.name ?? ""}`}
        onClose={() => setEditing(null)}
        error={editError}
        isPending={editPending}
        submitLabel="Save"
        onSubmit={() => submitEdit(undefined)}
      >
        <FormGroup label="Display name">
          <InputGroup value={editDisplayName} onChange={(e) => setEditDisplayName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Description">
          <InputGroup value={editDescription} onChange={(e) => setEditDescription(e.target.value)} />
        </FormGroup>
        <FormGroup label="Predicates (AND)">
          <PredicateFilterRows predicates={editPredicates} propertyKeys={editPropertyKeys} onChange={setEditPredicates} />
        </FormGroup>
        <FormGroup label="Lifecycle">
          <HTMLSelect fill value={editLifecycle} onChange={(e) => setEditLifecycle(e.target.value)}>
            {LIFECYCLES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        <FormGroup label="Visibility">
          <HTMLSelect fill value={editVisibility} onChange={(e) => setEditVisibility(e.target.value)}>
            {VISIBILITIES.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
      </RegistryDialog>
    </div>
  );
}
