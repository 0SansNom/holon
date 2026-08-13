import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { Button, Callout, Checkbox, HTMLSelect, Icon, InputGroup, Tag } from "@blueprintjs/core";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  flexRender,
  createColumnHelper,
  type RowSelectionState,
  type SortingState,
} from "@tanstack/react-table";
import {
  useActions,
  useEvaluateObjectSet,
  useInvokeAction,
  useInvokeActionBatch,
  useObjectSets,
  useObjectType,
  useObjects,
  useObjectsPage,
  usePrincipals,
  useRelationTypes,
  useValueTypes,
  useSharedPropertyTypes,
} from "../../api/hooks";
import { FormattedValue } from "../common/PropertyFormat";
import { applyConditionalStyle, camelToSnake } from "../common/propertyFormatUtils";
import { DetailPage } from "../common/PageLayout";
import type { ConditionalFormatRule } from "../../api/knowledge";
import { TENANT_ID, WORKSPACE_ID } from "../../api/config";
import { getErrorMessage } from "../../api/client";
import {
  OBJECT_METADATA_KEYS,
  buildExplorerColumnKeys,
  computeInlineEditableActions,
  preferTitleColumnFirst,
  resolveInstanceColumnKey,
  titleOf,
  urnShortName,
} from "./objectExplorerUtils";
import { isEphemeralTestName } from "../Ontology/ephemeralResources";
import { InlineEditableCell } from "./InlineEditableCell";
import { SelectionPreviewPanel } from "./SelectionPreviewPanel";
import { ActionInvokeDialog } from "./ActionInvokeDialog";
import { TableFilterBar } from "./TableFilterBar";
import { ColumnLayoutDialog } from "./ColumnLayoutDialog";
import { ExplorationChartPanel } from "./ExplorationChartPanel";
import { SavedViewsBar } from "./SavedViewsBar";
import { CompareObjectsDialog } from "./CompareObjectsDialog";
import { resolveVisibleColumnOrder, normalizeColumnLayout } from "./columnLayout";
import { mergeDrillDownFilters } from "./explorationCharts";
import { filterRowsByListIds, newViewId } from "./savedViews";
import { downloadCsv, rowsToCsv } from "./csvExport";
import { prefillActionParameters } from "./actionParameterPrefill";
import { useAuthStore } from "../../store/auth";
import { useObjectTableLayoutsStore } from "../../store/objectTableLayouts";
import { useObjectExplorerViewsStore } from "../../store/objectExplorerViews";
import {
  buildFormatsBySourceKey,
  isPropertyHidden,
  resolveDisplayTypeRule,
  resolvePropertyTypeRule,
  sortPropertiesByVisibility,
} from "../Ontology/propertyEditorUtils";
import {
  buildPredicateDefinition,
  expandFilterPropertyKeys,
  matchesPredicates,
  type PredicateFormRow,
} from "../Ontology/objectSetPredicates";

type Row = Record<string, unknown>;

const BATCH_ACTION_CAP = 50;
const FROZEN_SELECT_WIDTH = 36;
const FROZEN_DATA_COL_WIDTH = 140;

function resolveInvokeActionName(action: { name: string; parameters?: unknown } | undefined, activeActionName: string) {
  return action?.parameters !== undefined ? activeActionName : activeActionName.split(".")[1] ?? activeActionName;
}

function frozenLeftOffset(dataIndex: number): number {
  return FROZEN_SELECT_WIDTH + dataIndex * FROZEN_DATA_COL_WIDTH;
}

export function ObjectTablePage() {
  const { type } = useParams({ from: "/shell/objects/$type" });
  const { set: setName, exploration: explorationId, list: listId } = useSearch({
    from: "/shell/objects/$type",
  });
  const navigate = useNavigate();
  const principalUrn = useAuthStore((s) => s.session?.principal.urn);
  const { data: objectType } = useObjectType(type);
  const { data: objectSets = [] } = useObjectSets();
  const { data: evaluated, isFetching: evaluating, error: evaluateError } = useEvaluateObjectSet(
    setName ?? "",
    !!setName && !listId,
  );
  const { data: principals } = usePrincipals();
  const { data: allActions = [] } = useActions();
  const { data: relationTypes = [] } = useRelationTypes();
  const { data: valueTypes = [] } = useValueTypes();
  const { data: sharedPropertyTypes = [] } = useSharedPropertyTypes();
  const invokeAction = useInvokeAction(type);
  const invokeActionBatch = useInvokeActionBatch(type);
  const rawColumnLayout = useObjectTableLayoutsStore((s) => s.byType[type]);
  const setColumnLayout = useObjectTableLayoutsStore((s) => s.setLayout);
  const resetColumnLayout = useObjectTableLayoutsStore((s) => s.resetLayout);
  const columnLayout = useMemo(() => normalizeColumnLayout(rawColumnLayout), [rawColumnLayout]);

  const explorations = useObjectExplorerViewsStore((s) => s.explorations);
  const lists = useObjectExplorerViewsStore((s) => s.lists);
  const upsertExploration = useObjectExplorerViewsStore((s) => s.upsertExploration);
  const deleteExploration = useObjectExplorerViewsStore((s) => s.deleteExploration);
  const upsertList = useObjectExplorerViewsStore((s) => s.upsertList);
  const deleteList = useObjectExplorerViewsStore((s) => s.deleteList);

  const explorationsForType = useMemo(
    () => explorations.filter((e) => e.objectType === type),
    [explorations, type],
  );
  const listsForType = useMemo(() => lists.filter((l) => l.objectType === type), [lists, type]);
  const activeExploration = explorationsForType.find((e) => e.id === explorationId);
  const activeList = listsForType.find((l) => l.id === listId);

  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState("");
  const [filterPredicates, setFilterPredicates] = useState<PredicateFormRow[]>([]);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [previewOpen, setPreviewOpen] = useState(true);
  const [columnsDialogOpen, setColumnsDialogOpen] = useState(false);
  const [compareOpen, setCompareOpen] = useState(false);
  const [chartCollapsed, setChartCollapsed] = useState(false);
  const [activeActionName, setActiveActionName] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [actionParameters, setActionParameters] = useState<Record<string, unknown>>({});
  const [actionResult, setActionResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [serverPageSize, setServerPageSize] = useState(25);
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);
  const [stackIndex, setStackIndex] = useState(0);

  const useServerPaging =
    !setName && !listId && filterPredicates.length === 0 && globalFilter.trim() === "";
  const serverCursor = cursorStack[stackIndex] ?? null;

  const { data: allRows } = useObjects(useServerPaging ? "" : type);
  const { data: serverPage } = useObjectsPage(type, serverPageSize, serverCursor, useServerPaging);

  useEffect(() => {
    setCursorStack([null]);
    setStackIndex(0);
  }, [type, serverPageSize, useServerPaging]);

  const setsForType = useMemo(
    () =>
      objectSets.filter((os) => {
        if (urnShortName(os.object_type_urn) !== type || os.visibility === "hidden") return false;
        // Keep the active set visible even if ephemeral (deep link / saved exploration).
        if (setName && os.name === setName) return true;
        return !isEphemeralTestName(os.name);
      }),
    [objectSets, type, setName],
  );

  const activeSet = setName && !listId ? objectSets.find((os) => os.name === setName) : undefined;
  const setTypeMismatch = !!setName && !listId && !!evaluated && evaluated.object_type !== type;

  const baseRows = useMemo(() => {
    if (activeList) {
      return filterRowsByListIds(allRows ?? [], activeList.instanceIds);
    }
    if (useServerPaging) return (serverPage?.items as Row[] | undefined) ?? [];
    if (!setName) return allRows ?? [];
    if (setTypeMismatch) return [];
    return evaluated?.items ?? [];
  }, [activeList, setName, allRows, evaluated, setTypeMismatch, useServerPaging, serverPage]);

  // Apply saved exploration snapshot when URL exploration id changes.
  useEffect(() => {
    if (!explorationId || listId) return;
    const exp = explorations.find((e) => e.id === explorationId && e.objectType === type);
    if (!exp) return;
    setFilterPredicates(exp.filters);
    setColumnLayout(type, exp.columnLayout);
    setChartCollapsed(Boolean(exp.chartCollapsed));
    setGlobalFilter(exp.globalFilter ?? "");
    const desiredSet = exp.objectSet ?? "";
    const currentSet = setName ?? "";
    if (desiredSet !== currentSet) {
      void navigate({
        to: "/objects/$type",
        params: { type },
        search: {
          ...(desiredSet ? { set: desiredSet } : {}),
          exploration: explorationId,
        },
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only re-apply when exploration id / type changes
  }, [explorationId, type, listId]);

  useEffect(() => {
    setRowSelection({});
    setFocusedId(null);
    setActionResult(null);
    if (!explorationId) {
      setFilterPredicates([]);
      setGlobalFilter("");
    }
  }, [type, setName, listId, explorationId]);

  const propertyKeys = useMemo(() => {
    const mapping = objectType?.property_mapping ?? {};
    const fromMapping = expandFilterPropertyKeys(mapping, objectType?.property_types);
    if (fromMapping.length > 0) return fromMapping;
    const first = baseRows[0];
    if (!first) return [];
    return Object.keys(first).filter((k) => !OBJECT_METADATA_KEYS.has(k));
  }, [objectType, baseRows]);

  const activeFilterDefinition = useMemo(
    () => buildPredicateDefinition(filterPredicates),
    [filterPredicates],
  );

  const rows = useMemo(() => {
    if (activeFilterDefinition.all.length === 0) return baseRows;
    const mapping = objectType?.property_mapping ?? {};
    return baseRows.filter((row) => matchesPredicates(row, activeFilterDefinition, mapping));
  }, [baseRows, activeFilterDefinition, objectType]);

  useEffect(() => {
    const selectedIds = Object.keys(rowSelection).filter((id) => rowSelection[id]);
    if (focusedId && !rowSelection[focusedId]) {
      setFocusedId(selectedIds[0] ?? null);
      return;
    }
    if (!focusedId && selectedIds.length > 0) {
      setFocusedId(selectedIds[0]);
      setPreviewOpen(true);
    }
  }, [rowSelection, focusedId]);

  const inlineEditableBySourceKey = useMemo(
    () => computeInlineEditableActions(type, objectType?.implements ?? [], allActions),
    [type, objectType, allActions],
  );

  const formatsBySourceKey = useMemo(() => {
    return buildFormatsBySourceKey(
      objectType?.property_formats,
      objectType?.property_types,
      objectType?.property_mapping,
      sharedPropertyTypes,
    );
  }, [objectType, sharedPropertyTypes]);

  const conditionalFormatsBySourceKey = useMemo(() => {
    const map = new Map<string, ConditionalFormatRule[]>();
    Object.entries(objectType?.conditional_formats ?? {}).forEach(([property, rules]) => {
      map.set(camelToSnake(property), rules);
    });
    return map;
  }, [objectType]);

  const principalsByUrn = useMemo(() => {
    const map = new Map<string, string>();
    (principals ?? []).forEach((p) => map.set(p.urn, p.display_name));
    return map;
  }, [principals]);

  const fkFieldTargets = useMemo(() => {
    const map = new Map<string, string>();
    relationTypes
      .filter((r) => urnShortName(r.source_object_type_urn) === type)
      .forEach((r) => map.set(camelToSnake(r.source_property), urnShortName(r.target_object_type_urn)));
    return map;
  }, [relationTypes, type]);

  const relevantActions = useMemo(
    () =>
      allActions.filter(
        (a) =>
          a.target_object_type === type ||
          (a.target_interface && (objectType?.implements ?? []).includes(a.target_interface)),
      ),
    [allActions, type, objectType],
  );

  const availableColumnKeys = useMemo(() => {
    const first = rows?.[0] ?? null;
    const ordered = buildExplorerColumnKeys(objectType, first);
    const mapping = objectType?.property_mapping ?? {};
    const rowKeys = first ? new Set(Object.keys(first)) : null;
    const ontology = new Set(
      [...Object.keys(mapping), ...Object.keys(objectType?.derived_properties ?? {})].map((api) =>
        resolveInstanceColumnKey(api, mapping, rowKeys),
      ),
    );
    const visible = (keys: string[]) =>
      sortPropertiesByVisibility(
        keys,
        objectType?.property_types,
        objectType?.property_mapping,
        sharedPropertyTypes,
      ).filter(
        (k) =>
          !isPropertyHidden(k, objectType?.property_types, objectType?.property_mapping, sharedPropertyTypes),
      );
    if (ontology.size === 0) {
      return preferTitleColumnFirst(visible(ordered), objectType);
    }
    const ontologyKeys = preferTitleColumnFirst(
      visible(ordered.filter((k) => ontology.has(k))),
      objectType,
    );
    const extraKeys = visible(ordered.filter((k) => !ontology.has(k)));
    return [...ontologyKeys, ...extraKeys];
  }, [rows, objectType, sharedPropertyTypes]);

  const { visibleOrder: visibleColumnKeys, freezeCount } = useMemo(
    () => resolveVisibleColumnOrder(availableColumnKeys, columnLayout),
    [availableColumnKeys, columnLayout],
  );

  const columns = useMemo(() => {
    const helper = createColumnHelper<Row>();
    const freezeSelect = freezeCount > 0;

    const selectCol = helper.display({
      id: "_select",
      header: ({ table: tbl }) => (
        <Checkbox
          checked={tbl.getIsAllPageRowsSelected()}
          indeterminate={tbl.getIsSomePageRowsSelected() && !tbl.getIsAllPageRowsSelected()}
          onChange={tbl.getToggleAllPageRowsSelectedHandler()}
          onClick={(e) => e.stopPropagation()}
          aria-label="Select all on page"
        />
      ),
      cell: ({ row }) => (
        <Checkbox
          checked={row.getIsSelected()}
          disabled={!row.getCanSelect()}
          onChange={row.getToggleSelectedHandler()}
          onClick={(e) => e.stopPropagation()}
          aria-label={`Select ${String(row.original.id)}`}
        />
      ),
      size: FROZEN_SELECT_WIDTH,
      meta: {
        frozen: freezeSelect,
        frozenLeft: 0,
      },
    });

    const dataCols = visibleColumnKeys.map((key, keyIndex) => {
      const inlineAction = inlineEditableBySourceKey.get(key);
      const inlineParameterBaseType = inlineAction
        ? valueTypes.find((vt) => vt.name === inlineAction.parameters?.[0]?.value_type)?.base_type
        : undefined;
      const isNavTitleCol = keyIndex === 0;
      const frozen = keyIndex < freezeCount;

      return helper.accessor((row) => row[key], {
        id: key,
        header: key,
        size: FROZEN_DATA_COL_WIDTH,
        meta: {
          frozen,
          frozenLeft: frozen ? frozenLeftOffset(keyIndex) : undefined,
        },
        cell: (info) => {
          const row = info.row.original;
          const id = String(row.id);
          const masked =
            Array.isArray(row._maskedFields) &&
            ((row._maskedFields as string[]).includes(key) ||
              (row._maskedFields as string[]).includes(camelToSnake(key)));
          if (masked) return <span className="hl-masked-field">forbidden — masked</span>;
          if (inlineAction) {
            return (
              <InlineEditableCell
                value={info.getValue()}
                action={inlineAction}
                baseType={inlineParameterBaseType}
                onSubmit={(value) => {
                  const parameterName = inlineAction.parameters?.[0]?.name;
                  if (!parameterName) return;
                  void invokeAction.mutateAsync({
                    id: row.id as string | number,
                    actionName: inlineAction.name,
                    reason: "Inline edit",
                    parameters: { [parameterName]: value },
                  });
                }}
              />
            );
          }
          if (isNavTitleCol) {
            const label = titleOf(row, objectType) || String(id);
            return (
              <a
                href={`/objects/${encodeURIComponent(type)}/${encodeURIComponent(id)}`}
                className="hl-link-accent"
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  void navigate({ to: "/objects/$type/$id", params: { type, id } });
                }}
              >
                {label}
              </a>
            );
          }
          return (
            <FormattedValue
              rule={formatsBySourceKey.get(key)}
              value={info.getValue()}
              principalsByUrn={principalsByUrn}
              typeRule={resolveDisplayTypeRule(
                resolvePropertyTypeRule(key, objectType?.property_types, objectType?.property_mapping),
                sharedPropertyTypes,
              )}
              compact
            />
          );
        },
      });
    });

    return [selectCol, ...dataCols];
  }, [
    visibleColumnKeys,
    freezeCount,
    formatsBySourceKey,
    principalsByUrn,
    inlineEditableBySourceKey,
    valueTypes,
    invokeAction,
    objectType,
    sharedPropertyTypes,
    type,
    navigate,
  ]);

  const table = useReactTable({
    data: rows ?? [],
    columns,
    state: {
      sorting,
      globalFilter: useServerPaging ? "" : globalFilter,
      rowSelection,
      pagination: useServerPaging
        ? { pageIndex: 0, pageSize: Math.max(rows.length, 1) }
        : undefined,
    },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    onRowSelectionChange: setRowSelection,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    getRowId: (row) => String(row.id),
    enableRowSelection: true,
    manualPagination: useServerPaging,
    initialState: { pagination: { pageSize: 25 } },
  });

  const selectedRows = useMemo(() => {
    const byId = new Map((rows ?? []).map((r) => [String(r.id), r]));
    return Object.keys(rowSelection)
      .filter((id) => rowSelection[id])
      .map((id) => byId.get(id))
      .filter((r): r is Row => r != null);
  }, [rows, rowSelection]);

  const focusedRow = useMemo(() => {
    if (!focusedId) return null;
    return (rows ?? []).find((r) => String(r.id) === focusedId) ?? null;
  }, [rows, focusedId]);

  const activeAction = relevantActions.find((a) => a.name === activeActionName);
  const bulkTargetIds = useMemo(() => {
    if (selectedRows.length > 0) {
      return selectedRows.map((r) => String(r.id)).slice(0, BATCH_ACTION_CAP);
    }
    if (focusedId) return [focusedId];
    return [];
  }, [selectedRows, focusedId]);
  const bulkCapWarning =
    selectedRows.length > BATCH_ACTION_CAP
      ? `Selection has ${selectedRows.length} objects; only the first ${BATCH_ACTION_CAP} will be included (API cap).`
      : null;

  function selectSet(next: string) {
    void navigate({
      to: "/objects/$type",
      params: { type },
      search: next ? { set: next } : {},
    });
  }

  function clearSavedView() {
    void navigate({
      to: "/objects/$type",
      params: { type },
      search: setName ? { set: setName } : {},
    });
    setFilterPredicates([]);
    setGlobalFilter("");
  }

  function loadExploration(id: string) {
    const exp = explorations.find((e) => e.id === id);
    void navigate({
      to: "/objects/$type",
      params: { type },
      search: {
        ...(exp?.objectSet ? { set: exp.objectSet } : {}),
        exploration: id,
      },
    });
  }

  function loadList(id: string) {
    void navigate({
      to: "/objects/$type",
      params: { type },
      search: { list: id },
    });
  }

  function saveCurrentExploration(name: string) {
    const id = newViewId();
    upsertExploration({
      id,
      name,
      objectType: type,
      objectSet: setName,
      filters: filterPredicates,
      columnLayout,
      chartCollapsed,
      globalFilter: globalFilter || undefined,
      updatedAt: new Date().toISOString(),
    });
    void navigate({
      to: "/objects/$type",
      params: { type },
      search: {
        ...(setName ? { set: setName } : {}),
        exploration: id,
      },
    });
  }

  function saveSelectionAsList(name: string) {
    const ids = selectedRows.map((r) => String(r.id));
    if (ids.length === 0) return;
    const id = newViewId();
    upsertList({
      id,
      name,
      objectType: type,
      instanceIds: ids,
      updatedAt: new Date().toISOString(),
    });
    void navigate({
      to: "/objects/$type",
      params: { type },
      search: { list: id },
    });
  }

  function focusRow(id: string) {
    setFocusedId(id);
    setPreviewOpen(true);
    setRowSelection((prev) => (prev[id] ? prev : { ...prev, [id]: true }));
  }

  function selectMatchingRows() {
    const next: RowSelectionState = {};
    const ids = table
      .getFilteredRowModel()
      .rows.map((r) => String((r.original as Row).id))
      .slice(0, BATCH_ACTION_CAP);
    for (const id of ids) next[id] = true;
    setRowSelection(next);
    if (ids[0]) {
      setFocusedId(ids[0]);
      setPreviewOpen(true);
    }
  }

  async function submitAction() {
    if (!activeActionName || bulkTargetIds.length === 0) return;
    const action = relevantActions.find((a) => a.name === activeActionName);
    const actionName = resolveInvokeActionName(action, activeActionName);
    try {
      if (bulkTargetIds.length === 1) {
        const response = await invokeAction.mutateAsync({
          id: bulkTargetIds[0],
          actionName,
          reason,
          parameters: actionParameters,
        });
        const status = (response as { status?: string }).status;
        setActionResult({
          ok: true,
          message:
            status === "pending_approval"
              ? "Submitted for approval (high-risk Action)."
              : "Applied immediately.",
        });
      } else {
        const response = await invokeActionBatch.mutateAsync({
          actionName,
          reason,
          instanceIds: bulkTargetIds,
          parameters: actionParameters,
        });
        const pending = response.results.filter(
          (r) => r.ok && (r.result as { status?: string } | undefined)?.status === "pending_approval",
        ).length;
        const parts = [
          `${response.succeeded} succeeded`,
          response.failed > 0 ? `${response.failed} failed` : null,
          pending > 0 ? `${pending} pending approval` : null,
        ].filter(Boolean);
        setActionResult({
          ok: response.failed === 0,
          message: `Bulk ${actionName}: ${parts.join(", ")} (of ${response.count}).`,
        });
      }
    } catch (err) {
      setActionResult({ ok: false, message: getErrorMessage(err) });
    } finally {
      setActiveActionName(null);
      setReason("");
      setActionParameters({});
    }
  }

  const selectionCount = selectedRows.length;
  const showPreview = previewOpen && (focusedId != null || selectionCount > 0);
  const actionBusy = invokeAction.isPending || invokeActionBatch.isPending;
  const comparePair =
    selectedRows.length >= 2 ? [selectedRows[0], selectedRows[1]] as const : null;

  function exportCsv(scope: "visible" | "selected") {
    const exportRows =
      scope === "selected" && selectedRows.length > 0
        ? selectedRows
        : table.getFilteredRowModel().rows.map((r) => r.original as Row);
    const cols = visibleColumnKeys.length > 0 ? visibleColumnKeys : undefined;
    const csv = rowsToCsv(exportRows, cols);
    const stamp = new Date().toISOString().slice(0, 10);
    downloadCsv(`${type}-${scope}-${stamp}.csv`, csv);
  }

  return (
    <DetailPage
      breadcrumbs={[
        { label: "Objects", to: "/objects" },
        { label: type, ...(setName ? { to: "/objects/$type", params: { type } } : {}) },
        ...(activeSet ? [{ label: activeSet.display_name || activeSet.name }] : []),
      ]}
      title={activeSet ? activeSet.display_name || activeSet.name : type}
      description={
        activeSet ? (
          <>
            Object Set on <span className="hl-mono">{type}</span>
            {activeSet.description ? ` — ${activeSet.description}` : null}
          </>
        ) : (
          objectType?.description
        )
      }
      actions={
        <div className="hl-flex-row hl-items-center hl-gap-sm">
          {setsForType.length > 0 && (
            <HTMLSelect value={setName ?? ""} onChange={(e) => selectSet(e.target.value)}>
              <option value="">All {type}</option>
              {setsForType.map((os) => (
                <option key={os.name} value={os.name}>
                  {os.display_name || os.name}
                </option>
              ))}
            </HTMLSelect>
          )}
          {selectionCount > 0 && (
            <Tag minimal intent="primary" icon="selection" onRemove={() => {
              setRowSelection({});
              setFocusedId(null);
            }}>
              {selectionCount} selected
            </Tag>
          )}
          <Button
            minimal
            icon="multi-select"
            onClick={selectMatchingRows}
            title={`Select up to ${BATCH_ACTION_CAP} matching rows (filters + search)`}
          >
            Select matching
          </Button>
          <Button
            minimal
            icon="export"
            disabled={rows.length === 0}
            onClick={() => exportCsv(selectionCount > 0 ? "selected" : "visible")}
            title={
              selectionCount > 0
                ? `Export ${selectionCount} selected rows as CSV`
                : "Export visible (filtered) rows as CSV"
            }
          >
            Export CSV
          </Button>
          <Button
            minimal
            icon="exchange"
            disabled={selectionCount < 2}
            onClick={() => setCompareOpen(true)}
            title="Compare the first two selected objects"
          >
            Compare
          </Button>
          {!previewOpen && focusedId && (
            <Button minimal icon="panel-stats" onClick={() => setPreviewOpen(true)}>
              Show preview
            </Button>
          )}
          <Button minimal icon="th" onClick={() => setColumnsDialogOpen(true)}>
            Columns
            {freezeCount > 0 || columnLayout.hidden.length > 0 || columnLayout.order.length > 0 ? (
              <Tag minimal className="hl-ml-xs">
                custom
              </Tag>
            ) : null}
          </Button>
          <Button
            icon="diagram-tree"
            onClick={() =>
              void navigate({
                to: "/lineage/$urn",
                params: { urn: `hl:${TENANT_ID}:${WORKSPACE_ID}:object-type:${type}` },
              })
            }
          >
            View lineage
          </Button>
        </div>
      }
    >
      {setName && evaluating && (
        <Callout className="hl-mb-md" icon="refresh">
          Evaluating Object Set…
        </Callout>
      )}
      {evaluateError && (
        <Callout intent="danger" className="hl-mb-md">
          {(evaluateError as Error).message}
        </Callout>
      )}
      {setTypeMismatch && (
        <Callout intent="warning" className="hl-mb-md">
          Object Set “{setName}” targets {evaluated?.object_type}, not {type}.
        </Callout>
      )}
      {listId && !activeList && (
        <Callout intent="warning" className="hl-mb-md">
          Saved list not found (deleted or other browser).{" "}
          <Button minimal small onClick={clearSavedView}>
            Clear
          </Button>
        </Callout>
      )}
      {explorationId && !activeExploration && !listId && (
        <Callout intent="warning" className="hl-mb-md">
          Saved exploration not found.{" "}
          <Button minimal small onClick={clearSavedView}>
            Clear
          </Button>
        </Callout>
      )}
      {activeList && (
        <Callout intent="primary" className="hl-mb-md" icon="properties">
          Static list “{activeList.name}” — {activeList.instanceIds.length} IDs
          {baseRows.length < activeList.instanceIds.length
            ? ` (${activeList.instanceIds.length - baseRows.length} missing from current data)`
            : ""}
          . Object Sets are ignored while a list is active.
        </Callout>
      )}
      {activeSet && (
        <div className="hl-tag-row hl-mb-md">
          <Tag minimal intent="primary" icon="filter">
            Object Set
          </Tag>
          <Tag minimal>{activeSet.lifecycle_status}</Tag>
          {(activeSet.definition?.all ?? []).map((pred, i) => (
            <Tag key={i} minimal className="hl-mono">
              {pred.property} {pred.op} {Array.isArray(pred.value) ? pred.value.join(",") : String(pred.value)}
            </Tag>
          ))}
          {(activeSet.definition?.all ?? []).length === 0 && <Tag minimal>all instances</Tag>}
          <Button minimal small icon="cross" onClick={() => selectSet("")}>
            Clear set
          </Button>
        </div>
      )}

      <SavedViewsBar
        explorations={explorationsForType}
        lists={listsForType}
        activeExplorationId={activeExploration?.id}
        activeListId={activeList?.id}
        selectionCount={selectionCount}
        onLoadExploration={loadExploration}
        onLoadList={loadList}
        onClearView={clearSavedView}
        onSaveExploration={saveCurrentExploration}
        onSaveList={saveSelectionAsList}
        onDeleteExploration={(id) => {
          deleteExploration(id);
          if (explorationId === id) clearSavedView();
        }}
        onDeleteList={(id) => {
          deleteList(id);
          if (listId === id) clearSavedView();
        }}
      />

      <TableFilterBar
        propertyKeys={propertyKeys}
        predicates={filterPredicates}
        onChange={setFilterPredicates}
        onClear={() => setFilterPredicates([])}
      />

      <ExplorationChartPanel
        rows={baseRows}
        propertyKeys={propertyKeys}
        propertyMapping={objectType?.property_mapping}
        collapsed={chartCollapsed}
        onToggleCollapsed={() => setChartCollapsed((c) => !c)}
        onDrillDown={(bucket, property) => {
          setFilterPredicates((prev) => mergeDrillDownFilters(prev, property, bucket));
        }}
      />

      <InputGroup
        leftIcon="search"
        placeholder="Search visible rows..."
        value={globalFilter}
        onChange={(e) => table.setGlobalFilter(e.target.value)}
        className="hl-mb-md hl-filter-input"
      />

      <div className={`hl-oe-explore-layout${showPreview ? "" : " hl-oe-explore-layout--full"}`}>
        <div className="hl-panel hl-table-scroll hl-oe-explore-table">
          <table className="hl-data-table">
            <thead>
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id}>
                  {hg.headers.map((header) => {
                    const sortDirection = header.column.getIsSorted();
                    const isSelect = header.column.id === "_select";
                    const meta = header.column.columnDef.meta as
                      | { frozen?: boolean; frozenLeft?: number }
                      | undefined;
                    const frozenStyle =
                      meta?.frozen
                        ? ({
                            position: "sticky",
                            left: meta.frozenLeft ?? 0,
                            zIndex: 2,
                            width: header.getSize(),
                            minWidth: header.getSize(),
                          } as const)
                        : isSelect
                          ? { width: FROZEN_SELECT_WIDTH }
                          : undefined;
                    return (
                      <th
                        key={header.id}
                        className={meta?.frozen ? "hl-oe-col-frozen" : undefined}
                        onClick={isSelect ? undefined : header.column.getToggleSortingHandler()}
                        style={frozenStyle}
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {!isSelect && sortDirection && (
                          <Icon
                            icon={sortDirection === "asc" ? "caret-up" : "caret-down"}
                            size={12}
                            className="hl-sort-icon"
                          />
                        )}
                      </th>
                    );
                  })}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((row) => {
                const id = String((row.original as Row).id);
                const isFocused = focusedId === id;
                return (
                  <tr
                    key={row.id}
                    className="hl-data-table-row hl-object-row"
                    data-selected={row.getIsSelected() || isFocused ? "true" : undefined}
                    onClick={() => focusRow(id)}
                  >
                    {row.getVisibleCells().map((cell) => {
                      const meta = cell.column.columnDef.meta as
                        | { frozen?: boolean; frozenLeft?: number }
                        | undefined;
                      const conditional =
                        cell.column.id === "_select"
                          ? undefined
                          : applyConditionalStyle(
                              conditionalFormatsBySourceKey.get(cell.column.id),
                              row.original,
                              cell.getValue(),
                            );
                      const frozenStyle = meta?.frozen
                        ? {
                            position: "sticky" as const,
                            left: meta.frozenLeft ?? 0,
                            zIndex: 1,
                            width: cell.column.getSize(),
                            minWidth: cell.column.getSize(),
                          }
                        : undefined;
                      return (
                        <td
                          key={cell.id}
                          className={meta?.frozen ? "hl-oe-col-frozen" : undefined}
                          style={{ ...conditional, ...frozenStyle }}
                        >
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
          {rows.length === 0 && (
            <p className="hl-data-table-empty">
              {baseRows.length > 0 && activeFilterDefinition.all.length > 0
                ? "No instances match the current filters."
                : "No instances found."}
            </p>
          )}
        </div>

        {showPreview && (
          <SelectionPreviewPanel
            objectTypeName={type}
            objectType={objectType}
            focused={focusedRow}
            selectedRows={selectedRows}
            onFocus={focusRow}
            onClose={() => {
              setPreviewOpen(false);
            }}
            onSelectAction={(actionName) => {
              const action = relevantActions.find((a) => a.name === actionName);
              setActiveActionName(actionName);
              setActionParameters(
                prefillActionParameters(action?.parameters, {
                  principalUrn,
                  currentObjectId: focusedId,
                  currentObject: focusedRow,
                }),
              );
              setActionResult(null);
            }}
            formatsBySourceKey={formatsBySourceKey}
            conditionalFormatsBySourceKey={conditionalFormatsBySourceKey}
            principalsByUrn={principalsByUrn}
            sharedPropertyTypes={sharedPropertyTypes}
            fkFieldTargets={fkFieldTargets}
            relevantActions={relevantActions}
            actionResult={actionResult}
          />
        )}
      </div>

      <div className="hl-pagination-bar">
        <Tag minimal>
          {useServerPaging
            ? `${rows.length} on this page`
            : `${table.getFilteredRowModel().rows.length} of ${rows.length} rows`}
          {activeFilterDefinition.all.length > 0 ? ` (from ${baseRows.length})` : ""}
          {selectionCount > 0 ? ` · ${selectionCount} selected` : ""}
          {useServerPaging ? " · server cursor" : ""}
        </Tag>
        {useServerPaging ? (
          <>
            <Button
              minimal
              small
              icon="chevron-left"
              disabled={stackIndex <= 0}
              onClick={() => setStackIndex((i) => Math.max(0, i - 1))}
            />
            <span className="hl-text-muted">Page {stackIndex + 1}</span>
            <Button
              minimal
              small
              icon="chevron-right"
              disabled={!serverPage?.next_cursor}
              onClick={() => {
                const next = serverPage?.next_cursor;
                if (!next) return;
                setCursorStack((stack) => {
                  const trimmed = stack.slice(0, stackIndex + 1);
                  return [...trimmed, next];
                });
                setStackIndex((i) => i + 1);
              }}
            />
            <HTMLSelect
              minimal
              value={serverPageSize}
              onChange={(e) => setServerPageSize(Number(e.target.value))}
            >
              {[25, 50, 100].map((size) => (
                <option key={size} value={size}>
                  {size} / page
                </option>
              ))}
            </HTMLSelect>
          </>
        ) : (
          table.getPageCount() > 1 && (
            <>
              <Button
                minimal
                small
                icon="chevron-left"
                disabled={!table.getCanPreviousPage()}
                onClick={() => table.previousPage()}
              />
              <span className="hl-text-muted">
                Page {table.getState().pagination.pageIndex + 1} of {table.getPageCount()}
              </span>
              <Button
                minimal
                small
                icon="chevron-right"
                disabled={!table.getCanNextPage()}
                onClick={() => table.nextPage()}
              />
              <HTMLSelect
                minimal
                value={table.getState().pagination.pageSize}
                onChange={(e) => table.setPageSize(Number(e.target.value))}
              >
                {[25, 50, 100].map((size) => (
                  <option key={size} value={size}>
                    {size} / page
                  </option>
                ))}
              </HTMLSelect>
            </>
          )
        )}
      </div>

      <ActionInvokeDialog
        action={activeAction}
        reason={reason}
        parameters={actionParameters}
        loading={actionBusy}
        currentObjectId={focusedId}
        currentObject={focusedRow}
        bulkCount={bulkTargetIds.length}
        bulkCapWarning={bulkCapWarning}
        onReasonChange={setReason}
        onParametersChange={setActionParameters}
        onClose={() => {
          setActiveActionName(null);
          setReason("");
          setActionParameters({});
        }}
        onSubmit={() => void submitAction()}
      />

      <ColumnLayoutDialog
        isOpen={columnsDialogOpen}
        objectTypeName={type}
        availableKeys={availableColumnKeys}
        layout={columnLayout}
        onClose={() => setColumnsDialogOpen(false)}
        onSave={(next) => setColumnLayout(type, next)}
        onReset={() => resetColumnLayout(type)}
      />

      <CompareObjectsDialog
        isOpen={compareOpen}
        onClose={() => setCompareOpen(false)}
        objectTypeName={type}
        objectType={objectType}
        left={comparePair?.[0] ?? null}
        right={comparePair?.[1] ?? null}
      />
    </DetailPage>
  );
}
