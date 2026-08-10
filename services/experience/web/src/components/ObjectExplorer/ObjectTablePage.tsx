import { useMemo, useState } from "react";
import { useParams, useNavigate } from "@tanstack/react-router";
import { Button, HTMLSelect, Icon, InputGroup, Tag } from "@blueprintjs/core";
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
import { useObjectType, useObjects, usePrincipals, useActions, useValueTypes, useInvokeAction } from "../../api/hooks";
import { FormattedValue } from "../common/PropertyFormat";
import { applyConditionalStyle, camelToSnake } from "../common/propertyFormatUtils";
import { DetailPage } from "../common/PageLayout";
import type { PropertyFormatRule, ConditionalFormatRule } from "../../api/knowledge";
import { TENANT_ID, WORKSPACE_ID } from "../../api/config";
import { OBJECT_METADATA_KEYS, computeInlineEditableActions } from "./objectExplorerUtils";
import { InlineEditableCell } from "./InlineEditableCell";

type Row = Record<string, unknown>;

export function ObjectTablePage() {
  const { type } = useParams({ from: "/shell/objects/$type" });
  const navigate = useNavigate();
  const { data: objectType } = useObjectType(type);
  const { data: rows } = useObjects(type);
  const { data: principals } = usePrincipals();
  const { data: allActions = [] } = useActions();
  const { data: valueTypes = [] } = useValueTypes();
  const invokeAction = useInvokeAction(type);

  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState("");

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
    const keys = first ? Object.keys(first).filter((k) => !OBJECT_METADATA_KEYS.has(k)) : [];
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
  }, [rows, formatsBySourceKey, principalsByUrn, inlineEditableBySourceKey, valueTypes, invokeAction]);

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

  return (
    <DetailPage
      breadcrumbs={[{ label: "Objects", to: "/objects" }, { label: type }]}
      title={type}
      description={objectType?.description}
      actions={
        <Button
          icon="diagram-tree"
          onClick={() => void navigate({ to: "/lineage/$urn", params: { urn: `hl:${TENANT_ID}:${WORKSPACE_ID}:object-type:${type}` } })}
        >
          View lineage
        </Button>
      }
    >
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
