import { Tag, type Intent } from "@blueprintjs/core";
import type { PropertyFormatRule } from "../../api/knowledge";

// object_type.property_formats is keyed by the ontology's camelCase
// property name (e.g. "lifetimeValue"), but object payloads always use
// the raw source column name (resolver.py/serving_store.py serve rows
// verbatim) — same conversion ObjectDetailPage.tsx already relies on for
// foreign-key link targets.
export function camelToSnake(s: string): string {
  return s.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);
}

const BADGE_INTENT: Record<string, Intent | undefined> = {
  primary: "primary",
  success: "success",
  warning: "warning",
  danger: "danger",
  none: undefined,
};

const CURRENCY_FORMATTERS = new Map<string, Intl.NumberFormat>();

function currencyFormatter(currency: string): Intl.NumberFormat {
  let formatter = CURRENCY_FORMATTERS.get(currency);
  if (!formatter) {
    formatter = new Intl.NumberFormat(undefined, { style: "currency", currency });
    CURRENCY_FORMATTERS.set(currency, formatter);
  }
  return formatter;
}

export function FormattedValue({ rule, value }: { rule: PropertyFormatRule | undefined; value: unknown }) {
  if (value === null || value === undefined) return <>—</>;

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

  if (rule?.kind === "badge") {
    const color = rule.colors[String(value)];
    return (
      <Tag minimal intent={color ? BADGE_INTENT[color] : undefined}>
        {String(value)}
      </Tag>
    );
  }

  return <span className="hl-mono">{String(value)}</span>;
}
