/** Client-side exploration aggregates for Object Explorer charts. */

export type ExplorationChartKind = "bar" | "pie" | "histogram";

export type ExplorationBucket = {
  label: string;
  count: number;
  /** Exact value for categorical eq drill-down. */
  value?: unknown;
  /** Inclusive min for numeric range drill-down. */
  min?: number;
  /** Exclusive max for numeric range (inclusive on last bin). */
  max?: number;
  lastBin?: boolean;
};

export type ExplorationSeries = {
  kind: ExplorationChartKind;
  property: string;
  buckets: ExplorationBucket[];
  total: number;
  mode: "categorical" | "numeric";
};

const MAX_CATEGORIES = 12;

function readProperty(row: Record<string, unknown>, property: string, mapping: Record<string, string>): unknown {
  let actual: unknown = row[property];
  if (actual === undefined || actual === null) {
    const col = mapping[property];
    if (col) actual = row[col];
  }
  return actual;
}

export function isMostlyNumeric(values: unknown[]): boolean {
  const defined = values.filter((v) => v !== null && v !== undefined && v !== "");
  if (defined.length === 0) return false;
  let numeric = 0;
  for (const v of defined) {
    if (typeof v === "number" && Number.isFinite(v)) numeric += 1;
    else if (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v.trim()) && Number.isFinite(Number(v))) numeric += 1;
  }
  return numeric / defined.length >= 0.8;
}

function toNumber(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v.trim())) {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

export function aggregateCategorical(
  rows: Record<string, unknown>[],
  property: string,
  mapping: Record<string, string> = {},
  limit = MAX_CATEGORIES,
): ExplorationBucket[] {
  const counts = new Map<string, { count: number; value: unknown }>();
  for (const row of rows) {
    const raw = readProperty(row, property, mapping);
    const key = raw === null || raw === undefined || raw === "" ? "(empty)" : String(raw);
    const value = raw === null || raw === undefined || raw === "" ? "" : raw;
    const prev = counts.get(key);
    if (prev) prev.count += 1;
    else counts.set(key, { count: 1, value });
  }
  const sorted = [...counts.entries()].sort((a, b) => b[1].count - a[1].count || a[0].localeCompare(b[0]));
  const top = sorted.slice(0, limit);
  const rest = sorted.slice(limit);
  const buckets: ExplorationBucket[] = top.map(([label, { count, value }]) => ({ label, count, value }));
  if (rest.length > 0) {
    buckets.push({
      label: `Other (${rest.length})`,
      count: rest.reduce((s, [, v]) => s + v.count, 0),
      value: rest.map(([, v]) => v.value),
    });
  }
  return buckets;
}

export function aggregateHistogram(
  rows: Record<string, unknown>[],
  property: string,
  mapping: Record<string, string> = {},
  binCount = 10,
): ExplorationBucket[] {
  const nums: number[] = [];
  let empty = 0;
  for (const row of rows) {
    const raw = readProperty(row, property, mapping);
    if (raw === null || raw === undefined || raw === "") {
      empty += 1;
      continue;
    }
    const n = toNumber(raw);
    if (n == null) continue;
    nums.push(n);
  }
  if (nums.length === 0) {
    return empty > 0 ? [{ label: "(empty)", count: empty, value: "" }] : [];
  }
  const min = Math.min(...nums);
  const max = Math.max(...nums);
  if (min === max) {
    const buckets: ExplorationBucket[] = [{ label: String(min), count: nums.length, value: min, min, max, lastBin: true }];
    if (empty > 0) buckets.push({ label: "(empty)", count: empty, value: "" });
    return buckets;
  }
  const bins = Math.max(2, Math.min(binCount, nums.length));
  const width = (max - min) / bins;
  const counts = Array.from({ length: bins }, () => 0);
  for (const n of nums) {
    let idx = Math.floor((n - min) / width);
    if (idx >= bins) idx = bins - 1;
    if (idx < 0) idx = 0;
    counts[idx] += 1;
  }
  const buckets: ExplorationBucket[] = counts.map((count, i) => {
    const lo = min + i * width;
    const hi = i === bins - 1 ? max : min + (i + 1) * width;
    const label =
      Number.isInteger(lo) && Number.isInteger(hi)
        ? `${lo}–${hi}`
        : `${lo.toPrecision(4)}–${hi.toPrecision(4)}`;
    return { label, count, min: lo, max: hi, lastBin: i === bins - 1 };
  });
  if (empty > 0) buckets.push({ label: "(empty)", count: empty, value: "" });
  return buckets;
}

export function buildExplorationSeries(
  rows: Record<string, unknown>[],
  property: string,
  preferredKind: ExplorationChartKind | "auto",
  mapping: Record<string, string> = {},
): ExplorationSeries | null {
  if (!property || rows.length === 0) return null;
  const values = rows.map((r) => readProperty(r, property, mapping));
  const numeric = isMostlyNumeric(values);
  const mode = numeric ? "numeric" : "categorical";
  let kind: ExplorationChartKind;
  if (preferredKind === "auto") {
    kind = numeric ? "histogram" : "bar";
  } else if (preferredKind === "histogram" && !numeric) {
    kind = "bar";
  } else {
    kind = preferredKind;
  }

  const buckets =
    kind === "histogram"
      ? aggregateHistogram(rows, property, mapping)
      : aggregateCategorical(rows, property, mapping);

  return { kind, property, buckets, total: rows.length, mode };
}

/** Turn a chart click into Object Set–style filter form rows (AND). */
export function bucketToFilterPredicates(
  property: string,
  bucket: ExplorationBucket,
): { property: string; op: string; value: string }[] {
  if (bucket.label.startsWith("Other (")) {
    const values = Array.isArray(bucket.value) ? bucket.value : [];
    if (values.length === 0) return [];
    return [
      {
        property,
        op: "in",
        value: values.map((v) => (v === "" || v == null ? "" : String(v))).join(", "),
      },
    ];
  }
  if (bucket.min != null && bucket.max != null && bucket.value === undefined) {
    const preds = [
      { property, op: "gte", value: String(bucket.min) },
      { property, op: bucket.lastBin ? "lte" : "lt", value: String(bucket.max) },
    ];
    return preds;
  }
  const raw = bucket.value;
  return [{ property, op: "eq", value: raw == null ? "" : String(raw) }];
}

/** Replace predicates for a property, then append drill-down preds. */
export function mergeDrillDownFilters(
  existing: { property: string; op: string; value: string }[],
  property: string,
  bucket: ExplorationBucket,
): { property: string; op: string; value: string }[] {
  const next = existing.filter((p) => p.property !== property);
  return [...next, ...bucketToFilterPredicates(property, bucket)];
}
