import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearch, Link } from "@tanstack/react-router";
import { Button, Callout, Checkbox, HTMLSelect, Icon, InputGroup, Menu, MenuDivider, MenuItem, PopoverNext, Tag } from "@blueprintjs/core";
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
import { TablePageSkeleton } from "../common/Skeleton";
import { TENANT_ID, WORKSPACE_ID } from "../../api/config";
import { getErrorMessage } from "../../api/client";
import {
  actionsForObjectType,
  computeInlineEditableActions,
  fkFieldTargetsFromRelations,
  fkTargetForField,
  humanizeApiName,
  inferredFormatRule,
  explorerTypeBlurb,
  principalsDisplayByUrn,
  headlineOf,
} from "./objectExplorerUtils";
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
import { newViewId } from "./savedViews";
import { downloadCsv, rowsToCsv } from "./csvExport";
import { prefillActionParameters } from "./actionParameterPrefill";
import { useAuthStore } from "../../store/auth";
import { useObjectTableLayoutsStore } from "../../store/objectTableLayouts";
import { useObjectExplorerViewsStore } from "../../store/objectExplorerViews";
import {
  buildFormatsBySourceKey,
  resolveDisplayTypeRule,
  resolvePropertyTypeRule,
} from "../Ontology/propertyEditorUtils";
import {
  buildPredicateDefinition,
  matchesPredicates,
  type PredicateFormRow,
} from "../Ontology/objectSetPredicates";
import {
  BATCH_ACTION_CAP,
  FROZEN_DATA_COL_WIDTH,
  FROZEN_SELECT_WIDTH,
  bulkActionTargets,
  conditionalFormatsBySourceKey as mapConditionalFormatsBySourceKey,
  explorerAvailableColumnKeys,
  explorerPropertyKeys,
  formatBatchInvokeMessage,
  formatSingleInvokeMessage,
  frozenLeftOffset,
  nextFocusedRowId,
  resolveInvokeActionName,
  selectObjectTableBaseRows,
  shouldUseServerPaging,
  visibleObjectSetsForType,
} from "./objectTableModel";

type Row = Record<string, unknown>;

export function ObjectTablePage() {
  const { type } = useParams({ from: "/shell/objects/$type" });
  const { set: setName, exploration: explorationId, list: listId } = useSearch({
    from: "/shell/objects/$type",
  });
  const navigate = useNavigate();
  const principalUrn = useAuthStore((s) => s.session?.principal.urn);
  const { data: objectType, isPending: objectTypePending } = useObjectType(type);
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
  const [chartCollapsed, setChartCollapsed] = useState(true);
  const [activeActionName, setActiveActionName] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [actionParameters, setActionParameters] = useState<Record<string, unknown>>({});
  const [actionResult, setActionResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [serverPageSize, setServerPageSize] = useState(25);
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);
  const [stackIndex, setStackIndex] = useState(0);

  const activeFilterDefinition = useMemo(
    () => buildPredicateDefinition(filterPredicates),
    [filterPredicates],
  );

  const useServerPaging = shouldUseServerPaging({
    setName,
    listId,
    predicateCount: activeFilterDefinition.all.length,
  });
  const serverCursor = cursorStack[stackIndex] ?? null;

  const { data: allRows } = useObjects(useServerPaging ? "" : type);
  const { data: serverPage } = useObjectsPage(type, serverPageSize, serverCursor, useServerPaging);

  useEffect(() => {
    setCursorStack([null]);
    setStackIndex(0);
  }, [type, serverPageSize, useServerPaging]);

  useEffect(() => {
    if (useServerPaging) setGlobalFilter("");
  }, [useServerPaging]);

  const setsForType = useMemo(
    () => visibleObjectSetsForType(objectSets, type, setName),
    [objectSets, type, setName],
  );

  const activeSet = setName && !listId ? objectSets.find((os) => os.name === setName) : undefined;
  const setTypeMismatch = !!setName && !listId && !!evaluated && evaluated.object_type !== type;

  const baseRows = useMemo(
    () =>
      selectObjectTableBaseRows({
        activeList,
        useServerPaging,
        setName,
        setTypeMismatch,
        allRows,
        serverPageData: (serverPage?.data as Row[] | undefined) ?? [],
        evaluatedData: (evaluated?.data as Row[] | undefined) ?? [],
      }),
    [activeList, setName, allRows, evaluated, setTypeMismatch, useServerPaging, serverPage],
  );

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

  const propertyKeys = useMemo(
    () => explorerPropertyKeys(objectType, baseRows),
    [objectType, baseRows],
  );

  const rows = useMemo(() => {
    if (activeFilterDefinition.all.length === 0) return baseRows;
    const mapping = objectType?.property_mapping ?? {};
    return baseRows.filter((row) => matchesPredicates(row, activeFilterDefinition, mapping));
  }, [baseRows, activeFilterDefinition, objectType]);

  useEffect(() => {
    const next = nextFocusedRowId(rowSelection, focusedId);
    setFocusedId(next.focusedId);
    if (next.openPreview) setPreviewOpen(true);
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

  const conditionalFormatsBySourceKey = useMemo(
    () => mapConditionalFormatsBySourceKey(objectType?.conditional_formats),
    [objectType],
  );

  const principalsByUrn = useMemo(() => principalsDisplayByUrn(principals), [principals]);

  const fkFieldTargets = useMemo(
    () => fkFieldTargetsFromRelations(relationTypes, type),
    [relationTypes, type],
  );

  const relevantActions = useMemo(
    () => actionsForObjectType(allActions, type, objectType?.implements ?? []),
    [allActions, type, objectType],
  );

  const availableColumnKeys = useMemo(
    () => explorerAvailableColumnKeys(objectType, rows?.[0] ?? null, sharedPropertyTypes),
    [rows, objectType, sharedPropertyTypes],
  );

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
        header: humanizeApiName(key),
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
          const value = info.getValue();
          const fkTarget = fkTargetForField(fkFieldTargets, key);
          if (value != null && fkTarget && !isNavTitleCol) {
            return (
              <Link
                to="/objects/$type/$id"
                params={{ type: fkTarget, id: String(value) }}
                className="hl-link-accent"
                onClick={(e) => e.stopPropagation()}
              >
                {String(value)} → {fkTarget}
              </Link>
            );
          }
          if (inlineAction && !fkTarget) {
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
            const label = headlineOf(row, objectType).title || String(id);
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
              rule={inferredFormatRule(formatsBySourceKey.get(key) ?? formatsBySourceKey.get(camelToSnake(key)), value)}
              value={value}
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
    fkFieldTargets,
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
      // Omit pagination when client-side: passing `undefined` overwrites
      // initialState and TanStack then destructures pageSize from nothing.
      ...(useServerPaging
        ? { pagination: { pageIndex: 0, pageSize: Math.max(rows.length, 1) } }
        : {}),
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
  const { ids: bulkTargetIds, capWarning: bulkCapWarning } = useMemo(
    () => bulkActionTargets(selectedRows.map((r) => String(r.id)), focusedId),
    [selectedRows, focusedId],
  );

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
          message: formatSingleInvokeMessage(status),
        });
      } else {
        const response = await invokeActionBatch.mutateAsync({
          actionName,
          reason,
          instanceIds: bulkTargetIds,
          parameters: actionParameters,
        });
        setActionResult(formatBatchInvokeMessage(actionName, response));
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

  if (objectTypePending) {
    return <TablePageSkeleton />;
  }

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
            Saved set on {type}
            {activeSet.description ? ` — ${activeSet.description}` : null}
          </>
        ) : (
          explorerTypeBlurb(objectType?.description)
        )
      }
      actions={
        <ObjectTableToolbar
          type={type}
          setName={setName}
          setsForType={setsForType}
          selectionCount={selectionCount}
          rowCount={rows.length}
          previewOpen={previewOpen}
          focusedId={focusedId}
          columnLayoutCustom={
            freezeCount > 0 || columnLayout.hidden.length > 0 || columnLayout.order.length > 0
          }
          onSelectSet={selectSet}
          onClearSelection={() => {
            setRowSelection({});
            setFocusedId(null);
          }}
          onSelectMatching={selectMatchingRows}
          onExportCsv={() => exportCsv(selectionCount > 0 ? "selected" : "visible")}
          onCompare={() => setCompareOpen(true)}
          onShowPreview={() => setPreviewOpen(true)}
          onOpenColumns={() => setColumnsDialogOpen(true)}
          onViewLineage={() =>
            void navigate({
              to: "/lineage/$urn",
              params: { urn: `hl:${TENANT_ID}:${WORKSPACE_ID}:object-type:${type}` },
            })
          }
        />
      }
    >
      <ObjectTableStatus
        type={type}
        setName={setName}
        listId={listId}
        explorationMissing={!!explorationId && !activeExploration && !listId}
        evaluating={evaluating}
        evaluateError={evaluateError}
        setTypeMismatch={setTypeMismatch}
        evaluatedObjectType={evaluated?.object_type}
        activeList={activeList}
        activeSet={activeSet}
        baseRowCount={baseRows.length}
        onClearSavedView={clearSavedView}
        onClearSet={() => selectSet("")}
      />

      <div className="hl-oe-query-bar">
        {!useServerPaging && (
          <InputGroup
            leftIcon="search"
            placeholder="Search these rows…"
            value={globalFilter}
            onChange={(e) => setGlobalFilter(e.target.value)}
            className="hl-oe-query-search"
          />
        )}
        <TableFilterBar
          propertyKeys={propertyKeys}
          predicates={filterPredicates}
          onChange={setFilterPredicates}
          onClear={() => setFilterPredicates([])}
        />
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
        <Button
          small
          minimal
          icon="timeline-area-chart"
          active={!chartCollapsed}
          onClick={() => setChartCollapsed((c) => !c)}
        >
          Chart
        </Button>
      </div>

      {!chartCollapsed && (
        <ExplorationChartPanel
          rows={baseRows}
          propertyKeys={propertyKeys}
          propertyMapping={objectType?.property_mapping}
          collapsed={false}
          onToggleCollapsed={() => setChartCollapsed(true)}
          onDrillDown={(bucket, property) => {
            setFilterPredicates((prev) => mergeDrillDownFilters(prev, property, bucket));
          }}
        />
      )}

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
              disabled={!serverPage?.nextPageToken}
              onClick={() => {
                const next = serverPage?.nextPageToken;
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

function ObjectTableToolbar({
  type,
  setName,
  setsForType,
  selectionCount,
  rowCount,
  previewOpen,
  focusedId,
  columnLayoutCustom,
  onSelectSet,
  onClearSelection,
  onSelectMatching,
  onExportCsv,
  onCompare,
  onShowPreview,
  onOpenColumns,
  onViewLineage,
}: {
  type: string;
  setName?: string;
  setsForType: Array<{ name: string; display_name?: string }>;
  selectionCount: number;
  rowCount: number;
  previewOpen: boolean;
  focusedId: string | null;
  columnLayoutCustom: boolean;
  onSelectSet: (next: string) => void;
  onClearSelection: () => void;
  onSelectMatching: () => void;
  onExportCsv: () => void;
  onCompare: () => void;
  onShowPreview: () => void;
  onOpenColumns: () => void;
  onViewLineage: () => void;
}) {
  return (
    <div className="hl-flex-row hl-items-center hl-gap-sm">
      {setsForType.length > 0 && (
        <HTMLSelect value={setName ?? ""} onChange={(e) => onSelectSet(e.target.value)}>
          <option value="">All {type}</option>
          {setsForType.map((os) => (
            <option key={os.name} value={os.name}>
              {os.display_name || os.name}
            </option>
          ))}
        </HTMLSelect>
      )}
      {selectionCount > 0 && (
        <Tag minimal intent="primary" icon="selection" onRemove={onClearSelection}>
          {selectionCount} selected
        </Tag>
      )}
      <Button minimal icon="th" onClick={onOpenColumns}>
        Columns
        {columnLayoutCustom ? (
          <Tag minimal className="hl-ml-xs">
            custom
          </Tag>
        ) : null}
      </Button>
      <PopoverNext
        placement="bottom-end"
        content={
          <Menu>
            <MenuItem
              icon="multi-select"
              text="Select matching"
              onClick={onSelectMatching}
            />
            <MenuItem
              icon="export"
              text={selectionCount > 0 ? `Export ${selectionCount} selected` : "Export CSV"}
              disabled={rowCount === 0}
              onClick={onExportCsv}
            />
            <MenuItem
              icon="exchange"
              text="Compare"
              disabled={selectionCount < 2}
              onClick={onCompare}
            />
            {!previewOpen && focusedId && (
              <MenuItem icon="panel-stats" text="Show preview" onClick={onShowPreview} />
            )}
            <MenuDivider />
            <MenuItem icon="diagram-tree" text="Lineage" onClick={onViewLineage} />
          </Menu>
        }
      >
        <Button icon="more" aria-label="More actions" />
      </PopoverNext>
    </div>
  );
}

function ObjectTableStatus({
  type,
  setName,
  listId,
  explorationMissing,
  evaluating,
  evaluateError,
  setTypeMismatch,
  evaluatedObjectType,
  activeList,
  activeSet,
  baseRowCount,
  onClearSavedView,
  onClearSet,
}: {
  type: string;
  setName?: string;
  listId?: string;
  explorationMissing: boolean;
  evaluating: boolean;
  evaluateError: unknown;
  setTypeMismatch: boolean;
  evaluatedObjectType?: string;
  activeList?: { name: string; instanceIds: string[] };
  activeSet?: {
    lifecycle_status?: string;
    definition?: { all?: Array<{ property: string; op: string; value: unknown }> };
  };
  baseRowCount: number;
  onClearSavedView: () => void;
  onClearSet: () => void;
}) {
  return (
    <>
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
          Object Set “{setName}” targets {evaluatedObjectType}, not {type}.
        </Callout>
      )}
      {listId && !activeList && (
        <Callout intent="warning" className="hl-mb-md">
          Saved list not found (deleted or other browser).{" "}
          <Button minimal small onClick={onClearSavedView}>
            Clear
          </Button>
        </Callout>
      )}
      {explorationMissing && (
        <Callout intent="warning" className="hl-mb-md">
          Saved exploration not found.{" "}
          <Button minimal small onClick={onClearSavedView}>
            Clear
          </Button>
        </Callout>
      )}
      {activeList && (
        <Callout intent="primary" className="hl-mb-md" icon="properties">
          Static list “{activeList.name}” — {activeList.instanceIds.length} IDs
          {baseRowCount < activeList.instanceIds.length
            ? ` (${activeList.instanceIds.length - baseRowCount} missing from current data)`
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
          <Button minimal small icon="cross" onClick={onClearSet}>
            Clear set
          </Button>
        </div>
      )}
    </>
  );
}
