import { useMemo, useState } from "react";
import { useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { Button, Callout, HTMLSelect, Icon, InputGroup, Tag } from "@blueprintjs/core";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  flexRender,
  createColumnHelper,
  type SortingState,
} from "@tanstack/react-table";
import {
  useActions,
  useEvaluateObjectSet,
  useInvokeAction,
  useObjectSets,
  useObjectType,
  useObjects,
  usePrincipals,
  useValueTypes,
} from "../../api/hooks";
import { FormattedValue } from "../common/PropertyFormat";
import { applyConditionalStyle, camelToSnake } from "../common/propertyFormatUtils";
import { DetailPage } from "../common/PageLayout";
import type { PropertyFormatRule, ConditionalFormatRule } from "../../api/knowledge";
import { TENANT_ID, WORKSPACE_ID } from "../../api/config";
import { OBJECT_METADATA_KEYS, computeInlineEditableActions, urnShortName } from "./objectExplorerUtils";
import { InlineEditableCell } from "./InlineEditableCell";
import { isPropertyHidden, sortPropertiesByVisibility } from "../Ontology/propertyEditorUtils";

type Row = Record<string, unknown>;

export function ObjectTablePage() {
  const { type } = useParams({ from: "/shell/objects/$type" });
  const { set: setName } = useSearch({ from: "/shell/objects/$type" });
  const navigate = useNavigate();
  const { data: objectType } = useObjectType(type);
  const { data: allRows } = useObjects(type);
  const { data: objectSets = [] } = useObjectSets();
  const { data: evaluated, isFetching: evaluating, error: evaluateError } = useEvaluateObjectSet(
    setName ?? "",
    !!setName,
  );
  const { data: principals } = usePrincipals();
  const { data: allActions = [] } = useActions();
  const { data: valueTypes = [] } = useValueTypes();
  const invokeAction = useInvokeAction(type);

  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState("");

  const setsForType = useMemo(
    () =>
      objectSets.filter(
        (os) => urnShortName(os.object_type_urn) === type && os.visibility !== "hidden",
      ),
    [objectSets, type],
  );

  const activeSet = setName ? objectSets.find((os) => os.name === setName) : undefined;
  const setTypeMismatch =
    !!setName && !!evaluated && evaluated.object_type !== type;

  const rows = useMemo(() => {
    if (!setName) return allRows ?? [];
    if (setTypeMismatch) return [];
    return evaluated?.items ?? [];
  }, [setName, allRows, evaluated, setTypeMismatch]);

  const inlineEditableBySourceKey = useMemo(
    () => computeInlineEditableActions(type, objectType?.implements ?? [], allActions),
    [type, objectType, allActions],
  );

  const formatsBySourceKey = useMemo(() => {
    const map = new Map<string, PropertyFormatRule>();
    Object.entries(objectType?.property_formats ?? {}).forEach(([property, rule]) => {
      map.set(camelToSnake(property), rule);
    });
    return map;
  }, [objectType]);

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

  const columns = useMemo(() => {
    const helper = createColumnHelper<Row>();
    const first = rows?.[0];
    const keys = first
      ? sortPropertiesByVisibility(
          Object.keys(first).filter((k) => !OBJECT_METADATA_KEYS.has(k)),
          objectType?.property_types,
          objectType?.property_mapping,
        ).filter((k) => !isPropertyHidden(k, objectType?.property_types, objectType?.property_mapping))
      : [];
    return keys.map((key) => {
      const inlineAction = inlineEditableBySourceKey.get(key);
      const inlineParameterBaseType = inlineAction
        ? valueTypes.find((vt) => vt.name === inlineAction.parameters?.[0]?.value_type)?.base_type
        : undefined;
      return helper.accessor((row) => row[key], {
        id: key,
        header: key,
        cell: (info) => {
          const row = info.row.original;
          const masked = Array.isArray(row._maskedFields) && (row._maskedFields as string[]).includes(key);
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
          return <FormattedValue rule={formatsBySourceKey.get(key)} value={info.getValue()} principalsByUrn={principalsByUrn} />;
        },
      });
    });
  }, [rows, formatsBySourceKey, principalsByUrn, inlineEditableBySourceKey, valueTypes, invokeAction, objectType]);

  const table = useReactTable({
    data: rows ?? [],
    columns,
    state: { sorting, globalFilter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: 25 } },
  });

  function selectSet(next: string) {
    void navigate({
      to: "/objects/$type",
      params: { type },
      search: next ? { set: next } : {},
    });
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
          {(activeSet.definition?.all ?? []).length === 0 && (
            <Tag minimal>all instances</Tag>
          )}
          <Button minimal small icon="cross" onClick={() => selectSet("")}>
            Clear set
          </Button>
        </div>
      )}
      <InputGroup
        leftIcon="filter"
        placeholder="Filter rows..."
        value={globalFilter}
        onChange={(e) => table.setGlobalFilter(e.target.value)}
        className="hl-mb-md hl-filter-input"
      />
      <div className="hl-panel hl-table-scroll">
        <table className="hl-data-table">
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((header) => {
                  const sortDirection = header.column.getIsSorted();
                  return (
                    <th key={header.id} onClick={header.column.getToggleSortingHandler()}>
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      {sortDirection && (
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
              const id = (row.original as Row).id as string | number;
              return (
                <tr
                  key={row.id}
                  className="hl-data-table-row hl-object-row"
                  onClick={() => void navigate({ to: "/objects/$type/$id", params: { type, id: String(id) } })}
                >
                  {row.getVisibleCells().map((cell, idx) => (
                    <td
                      key={cell.id}
                      className={idx === 0 ? "hl-link-accent" : undefined}
                      style={applyConditionalStyle(conditionalFormatsBySourceKey.get(cell.column.id), row.original, cell.getValue())}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
        {rows?.length === 0 && <p className="hl-data-table-empty">No instances found.</p>}
      </div>
      <div className="hl-pagination-bar">
        <Tag minimal>
          {table.getFilteredRowModel().rows.length} of {rows?.length ?? 0} rows
        </Tag>
        {table.getPageCount() > 1 && (
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
        )}
      </div>
    </DetailPage>
  );
}
