import { OBJECT_METADATA_KEYS } from "./objectExplorerUtils";

/** Escape a CSV field (RFC-style quotes). */
export function csvEscape(value: unknown): string {
  if (value == null) return "";
  const s = typeof value === "string" ? value : Array.isArray(value) || typeof value === "object" ? JSON.stringify(value) : String(value);
  if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

export function rowsToCsv(
  rows: Record<string, unknown>[],
  columns?: string[],
): string {
  if (rows.length === 0) {
    return columns && columns.length > 0 ? columns.map(csvEscape).join(",") : "";
  }
  const keys =
    columns && columns.length > 0
      ? columns
      : Object.keys(rows[0]).filter((k) => !OBJECT_METADATA_KEYS.has(k));
  const header = keys.map(csvEscape).join(",");
  const lines = rows.map((row) => keys.map((k) => csvEscape(row[k])).join(","));
  return [header, ...lines].join("\n");
}

export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename.endsWith(".csv") ? filename : `${filename}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

/** Union of property keys across compared objects, metadata stripped. */
export function comparePropertyKeys(objects: Record<string, unknown>[]): string[] {
  const keys = new Set<string>();
  for (const obj of objects) {
    for (const k of Object.keys(obj)) {
      if (!OBJECT_METADATA_KEYS.has(k)) keys.add(k);
    }
  }
  return [...keys].sort((a, b) => a.localeCompare(b));
}

export function valuesDiffer(a: unknown, b: unknown): boolean {
  if (a === b) return false;
  if (a == null && b == null) return false;
  try {
    return JSON.stringify(a) !== JSON.stringify(b);
  } catch {
    return String(a) !== String(b);
  }
}
