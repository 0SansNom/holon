import { useMemo, useState } from "react";
import { useParams, useNavigate } from "@tanstack/react-router";
import { Button, H3, HTMLSelect, Icon, InputGroup, Spinner, Tag } from "@blueprintjs/core";
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
import { useObjectType, useObjects } from "../../api/hooks";
import { camelToSnake, FormattedValue } from "../common/PropertyFormat";
import { PageBreadcrumbs } from "../common/PageBreadcrumbs";
import type { PropertyFormatRule } from "../../api/knowledge";

const METADATA_KEYS = new Set(["materializedAt", "sourceLagSeconds", "degraded", "_maskedFields"]);

type Row = Record<string, unknown>;

export function ObjectTablePage() {
  const { type } = useParams({ from: "/shell/objects/$type" });
  const navigate = useNavigate();
  const { data: objectType } = useObjectType(type);
  const { data: rows, isLoading, error } = useObjects(type);

  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState("");

  // property_formats is keyed by ontology camelCase property names;
  // row data uses raw source column names — index by the converted key
  // once so each cell render doesn't redo the lookup.
  const formatsBySourceKey = useMemo(() => {
    const map = new Map<string, PropertyFormatRule>();
    Object.entries(objectType?.property_formats ?? {}).forEach(([property, rule]) => {
      map.set(camelToSnake(property), rule);
    });
    return map;
  }, [objectType]);

  const columns = useMemo(() => {
    const helper = createColumnHelper<Row>();
    const first = rows?.[0];
    const keys = first ? Object.keys(first).filter((k) => !METADATA_KEYS.has(k)) : [];
    return keys.map((key) =>
      helper.accessor((row) => row[key], {
        id: key,
        header: key,
        cell: (info) => {
          const row = info.row.original;
          const masked = Array.isArray(row._maskedFields) && (row._maskedFields as string[]).includes(key);
          if (masked) return <span className="hl-masked-field">forbidden — masked</span>;
          return <FormattedValue rule={formatsBySourceKey.get(key)} value={info.getValue()} />;
        },
      }),
    );
  }, [rows, formatsBySourceKey]);

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

  if (isLoading) return <Spinner />;
  if (error) return <p style={{ color: "var(--hl-danger)" }}>{(error as Error).message}</p>;

  return (
    <div>
      <PageBreadcrumbs items={[{ label: "Objects", to: "/objects" }, { label: type }]} />
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 6 }}>
        <H3 style={{ margin: 0 }}>{type}</H3>
        <Button
          icon="diagram-tree"
          onClick={() => void navigate({ to: "/lineage/$urn", params: { urn: `hl:acme:demo:object-type:${type}` } })}
        >
          View lineage
        </Button>
      </div>
      {objectType && <p style={{ color: "var(--hl-text-muted)", marginTop: 8, marginBottom: 16 }}>{objectType.description}</p>}
      <InputGroup
        leftIcon="filter"
        placeholder="Filter rows..."
        value={globalFilter}
        onChange={(e) => table.setGlobalFilter(e.target.value)}
        style={{ marginBottom: 12, maxWidth: 320 }}
      />
      <style>{`.hl-object-row:hover { background: var(--hl-accent-soft); }`}</style>
      <div className="hl-panel" style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((header) => {
                  const sortDirection = header.column.getIsSorted();
                  return (
                    <th
                      key={header.id}
                      onClick={header.column.getToggleSortingHandler()}
                      style={{
                        textAlign: "left",
                        padding: "8px 12px",
                        borderBottom: "1px solid var(--hl-border)",
                        color: "var(--hl-text-muted)",
                        fontWeight: 500,
                        fontSize: 11,
                        textTransform: "uppercase",
                        letterSpacing: "0.03em",
                        cursor: "pointer",
                        userSelect: "none",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      {sortDirection && (
                        <Icon
                          icon={sortDirection === "asc" ? "caret-up" : "caret-down"}
                          size={12}
                          style={{ marginLeft: 4, verticalAlign: "text-bottom" }}
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
                  className="hl-object-row"
                  onClick={() => void navigate({ to: "/objects/$type/$id", params: { type, id: String(id) } })}
                  style={{ borderBottom: "1px solid var(--hl-border)", cursor: "pointer" }}
                >
                  {row.getVisibleCells().map((cell, idx) => (
                    <td key={cell.id} style={{ padding: "8px 12px", color: idx === 0 ? "var(--hl-accent)" : undefined }}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
        {rows?.length === 0 && <p style={{ color: "var(--hl-text-muted)", padding: 12 }}>No instances found.</p>}
      </div>
      <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 12 }}>
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
            <span style={{ fontSize: 12, color: "var(--hl-text-muted)" }}>
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
    </div>
  );
}
