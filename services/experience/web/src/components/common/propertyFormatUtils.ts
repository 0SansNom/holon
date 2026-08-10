import type { CSSProperties } from "react";
import type { ConditionalFormatRule } from "../../api/knowledge";

export function camelToSnake(s: string): string {
  return s.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);
}

const INTENT_CSS_VAR: Record<string, string> = {
  primary: "var(--hl-accent)", success: "var(--hl-success)", warning: "var(--hl-warning)", danger: "var(--hl-danger)",
};

function conditionSubject(condition: ConditionalFormatRule, row: Record<string, unknown>, ownValue: unknown): unknown {
  return condition.compareTo?.kind === "property" ? row[camelToSnake(condition.compareTo.property)] : ownValue;
}

function matchesCondition(rule: ConditionalFormatRule, row: Record<string, unknown>, ownValue: unknown): boolean {
  const subject = conditionSubject(rule, row, ownValue);
  const c = rule.condition;
  switch (c.type) {
    case "always": return true;
    case "is-null": return subject === null || subject === undefined;
    case "string-equals": return c.caseSensitive === false ? String(subject).toLowerCase() === c.value.toLowerCase() : String(subject) === c.value;
    case "string-contains": return c.caseSensitive === false ? String(subject).toLowerCase().includes(c.value.toLowerCase()) : String(subject).includes(c.value);
    case "string-starts-with": return c.caseSensitive === false ? String(subject).toLowerCase().startsWith(c.value.toLowerCase()) : String(subject).startsWith(c.value);
    case "number-range": {
      const n = Number(subject);
      return !Number.isNaN(n) && (c.min === undefined || n >= c.min) && (c.max === undefined || n <= c.max);
    }
    case "number-equals": return Number(subject) === c.value;
    default: return false;
  }
}

export function applyConditionalStyle(
  rules: ConditionalFormatRule[] | undefined,
  row: Record<string, unknown>,
  ownValue: unknown,
): CSSProperties {
  if (!rules) return {};
  for (const rule of rules) {
    if (matchesCondition(rule, row, ownValue)) {
      return {
        ...(rule.style.color ? { color: INTENT_CSS_VAR[rule.style.color] ?? rule.style.color } : {}),
        ...(rule.style.backgroundColor ? { backgroundColor: INTENT_CSS_VAR[rule.style.backgroundColor] ?? rule.style.backgroundColor } : {}),
        ...(rule.style.textAlign ? { textAlign: rule.style.textAlign } : {}),
      };
    }
  }
  return {};
}
