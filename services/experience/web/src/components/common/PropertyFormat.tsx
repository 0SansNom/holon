import { Tag, type Intent } from "@blueprintjs/core";
import { Link } from "@tanstack/react-router";
import type { PropertyFormatRule, PropertyTypeRule } from "../../api/knowledge";

// object_type.property_formats is keyed by the ontology's camelCase
// property name (e.g. "lifetimeValue"), but object payloads always use
// the raw source column name (resolver.py/serving_store.py serve rows
// verbatim) — same conversion ObjectDetailPage.tsx already relies on for
// foreign-key link targets.
const BADGE_INTENT: Record<string, Intent | undefined> = {
  primary: "primary",
  success: "success",
  warning: "warning",
  danger: "danger",
  none: undefined,
};

// The same closed vocabulary Blueprint's own `Intent` maps to CSS custom
// properties already defined in theme.css — reused so a conditional
// format's `color: "danger"` renders the exact same red as everywhere
// else danger is signaled, not a second, drifting color choice.
const CURRENCY_FORMATTERS = new Map<string, Intl.NumberFormat>();

function currencyFormatter(currency: string): Intl.NumberFormat {
  let formatter = CURRENCY_FORMATTERS.get(currency);
  if (!formatter) {
    formatter = new Intl.NumberFormat(undefined, { style: "currency", currency });
    CURRENCY_FORMATTERS.set(currency, formatter);
  }
  return formatter;
}

// General-purpose numeric formatting — the rule's fields are named to
// match `Intl.NumberFormat`'s own constructor options exactly (see
// api/knowledge.ts's `PropertyFormatRule`), so this is a passthrough,
// not a translation layer. Cached per distinct option-set the same way
// `currencyFormatter` caches per currency code.
const NUMERIC_FORMATTERS = new Map<string, Intl.NumberFormat>();

function numericFormatter(rule: Extract<PropertyFormatRule, { kind: "numeric" }>): Intl.NumberFormat {
  const key = JSON.stringify(rule);
  let formatter = NUMERIC_FORMATTERS.get(key);
  if (!formatter) {
    formatter = new Intl.NumberFormat(undefined, {
      style: rule.style ?? "decimal",
      currency: rule.currency,
      unit: rule.unit,
      useGrouping: rule.useGrouping,
      notation: rule.notation,
      minimumFractionDigits: rule.minimumFractionDigits,
      maximumFractionDigits: rule.maximumFractionDigits,
      minimumSignificantDigits: rule.minimumSignificantDigits,
      maximumSignificantDigits: rule.maximumSignificantDigits,
      minimumIntegerDigits: rule.minimumIntegerDigits,
    });
    NUMERIC_FORMATTERS.set(key, formatter);
  }
  return formatter;
}

const DATETIME_FORMATTERS = new Map<string, Intl.DateTimeFormat>();

function dateTimeFormatter(style: string, timezone?: string): Intl.DateTimeFormat {
  const key = `${style}|${timezone ?? ""}`;
  let formatter = DATETIME_FORMATTERS.get(key);
  if (!formatter) {
    const options: Intl.DateTimeFormatOptions = { timeZone: timezone };
    if (style === "date") Object.assign(options, { dateStyle: "medium" });
    else if (style === "datetime-long") Object.assign(options, { dateStyle: "full", timeStyle: "medium" });
    else if (style === "datetime-short") Object.assign(options, { dateStyle: "medium", timeStyle: "short" });
    else if (style === "time") Object.assign(options, { timeStyle: "short" });
    formatter = new Intl.DateTimeFormat(undefined, options);
    DATETIME_FORMATTERS.set(key, formatter);
  }
  return formatter;
}

const RELATIVE_FORMATTER = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
const RELATIVE_UNITS: Array<[Intl.RelativeTimeFormatUnit, number]> = [
  ["year", 60 * 60 * 24 * 365],
  ["month", 60 * 60 * 24 * 30],
  ["week", 60 * 60 * 24 * 7],
  ["day", 60 * 60 * 24],
  ["hour", 60 * 60],
  ["minute", 60],
  ["second", 1],
];

function formatRelative(date: Date): string {
  const deltaSeconds = (date.getTime() - Date.now()) / 1000;
  for (const [unit, secondsInUnit] of RELATIVE_UNITS) {
    if (Math.abs(deltaSeconds) >= secondsInUnit || unit === "second") {
      return RELATIVE_FORMATTER.format(Math.round(deltaSeconds / secondsInUnit), unit);
    }
  }
  return RELATIVE_FORMATTER.format(0, "second");
}

function formatDateTime(rule: Extract<PropertyFormatRule, { kind: "datetime" }>, value: unknown): string {
  const date = value instanceof Date ? value : new Date(String(value));
  if (Number.isNaN(date.getTime())) return String(value);
  if (rule.style === "iso8601") return date.toISOString();
  if (rule.style === "relative") return formatRelative(date);
  return dateTimeFormatter(rule.style, rule.timezone).format(date);
}

// hl:{tenant}:{workspace}:{type}:{id} — the `id` segment is what every
// resource-owning route already keys off (ObjectType name, Application
// name), same URN shape used throughout the backend and this app's own
// resource-tag/pin/collection work.
function urnId(urn: string): string {
  return urn.split(":").pop() ?? urn;
}

function formatPrimitive(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function structFieldEntries(
  value: Record<string, unknown>,
  typeRule: PropertyTypeRule | undefined,
  compact: boolean,
): Array<{ name: string; value: unknown; description?: string }> {
  const properties =
    typeRule?.kind === "struct"
      ? typeRule.properties
      : typeRule?.kind === "array" && typeRule.element.kind === "struct"
        ? typeRule.element.properties
        : undefined;

  if (!properties) {
    return Object.entries(value).map(([name, fieldValue]) => ({ name, value: fieldValue }));
  }

  const declared = Object.entries(properties).map(([name, leaf]) => ({
    name,
    value: value[name],
    description: leaf.description,
    main: !!leaf.main_field,
  }));
  const hasMain = declared.some((f) => f.main);
  const visible = compact && hasMain ? declared.filter((f) => f.main) : declared;
  // Preserve undeclared extras in full view only.
  if (!compact) {
    for (const [name, fieldValue] of Object.entries(value)) {
      if (!(name in properties)) visible.push({ name, value: fieldValue, description: undefined, main: false });
    }
  }
  return visible.map(({ name, value: fieldValue, description }) => ({ name, value: fieldValue, description }));
}

function StructObjectView({
  value,
  typeRule,
  compact,
}: {
  value: Record<string, unknown>;
  typeRule?: PropertyTypeRule;
  compact: boolean;
}) {
  const fields = structFieldEntries(value, typeRule, compact);
  if (compact) {
    return (
      <span className="hl-struct-compact" title={fields.map((f) => `${f.name}: ${formatPrimitive(f.value)}`).join(" · ")}>
        {fields.map((f, i) => (
          <span key={f.name} className="hl-struct-compact-field">
            {i > 0 && <span className="hl-struct-compact-sep"> · </span>}
            <span className="hl-struct-field-name">{f.name}</span>
            <span className="hl-struct-field-value">{formatPrimitive(f.value)}</span>
          </span>
        ))}
      </span>
    );
  }
  return (
    <dl className="hl-struct-fields-view">
      {fields.map((f) => (
        <div key={f.name} className="hl-struct-field-view-row">
          <dt title={f.description}>{f.name}</dt>
          <dd className="hl-mono">{formatPrimitive(f.value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function StructuredValue({
  value,
  typeRule,
  compact,
}: {
  value: object;
  typeRule?: PropertyTypeRule;
  compact: boolean;
}) {
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="hl-text-muted">[]</span>;
    const elementRule =
      typeRule?.kind === "array"
        ? typeRule.element.kind === "struct"
          ? ({ kind: "struct", properties: typeRule.element.properties } as PropertyTypeRule)
          : undefined
        : typeRule?.kind === "struct"
          ? typeRule
          : undefined;
    return (
      <ul className={compact ? "hl-struct-array-compact" : "hl-struct-array"}>
        {value.map((item, index) => (
          <li key={index}>
            {item !== null && typeof item === "object" && !Array.isArray(item) ? (
              <StructObjectView value={item as Record<string, unknown>} typeRule={elementRule} compact={compact} />
            ) : (
              <span className="hl-mono">{formatPrimitive(item)}</span>
            )}
          </li>
        ))}
      </ul>
    );
  }
  return <StructObjectView value={value as Record<string, unknown>} typeRule={typeRule} compact={compact} />;
}

export function FormattedValue({
  rule,
  value,
  principalsByUrn,
  typeRule,
  compact = false,
}: {
  rule: PropertyFormatRule | undefined;
  value: unknown;
  principalsByUrn?: Map<string, string>;
  /** When set, object/array values render as labeled struct fields (main fields in compact mode). */
  typeRule?: PropertyTypeRule;
  /** Table cells: main fields only when designated; detail views pass false. */
  compact?: boolean;
}) {
  if (value === null || value === undefined) return <>—</>;

  // Foundry `identifier` render hint: treat as opaque key — no locale /
  // currency / numeric formatting (Object Views won't format as numbers).
  const asIdentifier = typeRule?.render_hints?.includes("identifier");
  if (asIdentifier) {
    return <span className="hl-mono">{String(value)}</span>;
  }

  if (rule?.kind === "currency") {
    // Postgres NUMERIC columns are serialized as strings (avoids float
    // precision loss), not JSON numbers — e.g. Customer.lifetimeValue
    // comes back as "184500.00". Parse either representation; fall back
    // to the raw value only if it genuinely isn't numeric.
    const amount = typeof value === "number" ? value : Number(value);
    if (!Number.isNaN(amount)) {
      return <span className="hl-mono">{currencyFormatter(rule.currency).format(amount)}</span>;
    }
  }

  if (rule?.kind === "numeric") {
    const amount = typeof value === "number" ? value : Number(value);
    if (!Number.isNaN(amount)) {
      const formatted = numericFormatter(rule).format(amount);
      return (
        <span className="hl-mono">
          {rule.prefix}
          {formatted}
          {rule.suffix}
        </span>
      );
    }
  }

  if (rule?.kind === "datetime") {
    return <span>{formatDateTime(rule, value)}</span>;
  }

  if (rule?.kind === "principal") {
    const displayName = principalsByUrn?.get(String(value));
    return <span title={String(value)}>{displayName ?? String(value)}</span>;
  }

  if (rule?.kind === "resource-link") {
    const id = urnId(String(value));
    if (rule.resourceType === "object-type") {
      return (
        <Link to="/objects/$type" params={{ type: id }} onClick={(e) => e.stopPropagation()}>
          {id}
        </Link>
      );
    }
    return (
      <Link to="/applications/$name" params={{ name: id }} onClick={(e) => e.stopPropagation()}>
        {id}
      </Link>
    );
  }

  if (rule?.kind === "badge") {
    const color = rule.colors[String(value)];
    return (
      <Tag minimal intent={color ? BADGE_INTENT[color] : undefined}>
        {String(value)}
      </Tag>
    );
  }

  // Struct / array / plain object — labeled fields when we have a type
  // rule; otherwise still avoid the useless "[object Object]".
  if (typeof value === "object") {
    return <StructuredValue value={value} typeRule={typeRule} compact={compact} />;
  }
  return <span className="hl-mono">{String(value)}</span>;
}
