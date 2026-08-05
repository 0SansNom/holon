import { useMemo } from "react";
import { useParams, Link } from "@tanstack/react-router";
import { H3, Spinner, Tag } from "@blueprintjs/core";
import { useReactTable, getCoreRowModel, flexRender, createColumnHelper } from "@tanstack/react-table";
import { useObjectType, useObjects } from "../../api/hooks";

const METADATA_KEYS = new Set(["materializedAt", "sourceLagSeconds", "degraded", "_maskedFields"]);

type Row = Record<string, unknown>;

export function ObjectTablePage() {
  const { type } = useParams({ from: "/shell/objects/$type" });
  const { data: objectType } = useObjectType(type);
  const { data: rows, isLoading, error } = useObjects(type);

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
          const value = info.getValue();
          return <span className="hl-mono">{value === null || value === undefined ? "—" : String(value)}</span>;
        },
      }),
    );
  }, [rows]);

  const table = useReactTable({ data: rows ?? [], columns, getCoreRowModel: getCoreRowModel() });

  if (isLoading) return <Spinner />;
  if (error) return <p style={{ color: "var(--hl-danger)" }}>{(error as Error).message}</p>;

  return (
    <div>
      <H3>{type}</H3>
      {objectType && <p style={{ color: "var(--hl-text-muted)", marginBottom: 16 }}>{objectType.description}</p>}
      <div className="hl-panel" style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((header) => (
                  <th
                    key={header.id}
                    style={{
                      textAlign: "left",
                      padding: "8px 12px",
                      borderBottom: "1px solid var(--hl-border)",
                      color: "var(--hl-text-muted)",
                      fontWeight: 500,
                      fontSize: 11,
                      textTransform: "uppercase",
                      letterSpacing: "0.03em",
                    }}
                  >
                    {flexRender(header.column.columnDef.header, header.getContext())}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => {
              const id = (row.original as Row).id as string | number;
              return (
                <tr key={row.id} style={{ borderBottom: "1px solid var(--hl-border)" }}>
                  {row.getVisibleCells().map((cell, idx) => (
                    <td key={cell.id} style={{ padding: "8px 12px" }}>
                      {idx === 0 ? (
                        <Link to="/objects/$type/$id" params={{ type, id: String(id) }} style={{ color: "#8abbff" }}>
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </Link>
                      ) : (
                        flexRender(cell.column.columnDef.cell, cell.getContext())
                      )}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
        {rows?.length === 0 && <p style={{ color: "var(--hl-text-muted)", padding: 12 }}>No instances found.</p>}
      </div>
      <div style={{ marginTop: 12 }}>
        <Tag minimal>{rows?.length ?? 0} rows</Tag>
      </div>
    </div>
  );
}
