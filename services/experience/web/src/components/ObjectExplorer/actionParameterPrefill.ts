/** Prefill Action parameters from Form defaults + Foundry type classes. */

import type { ActionParameter, ActionParameterDefault } from "../../api/knowledge";
import { hasTypeClass } from "../Ontology/typeClassUtils";

export type PrefillContext = {
  principalUrn?: string | null;
  /** Target instance id of the Action being invoked. */
  currentObjectId?: string | null;
  /** Target instance row (for object_property defaults with object=current). */
  currentObject?: Record<string, unknown> | null;
  /** Resolved rows for object_reference parameters (name → row). */
  objectsByParameter?: Record<string, Record<string, unknown> | null | undefined>;
  /**
   * When set, only fill defaults whose object_property.source matches this
   * parameter name (used when an object_reference picker changes).
   */
  onlyFromObjectParameter?: string;
};

/** Read a property from an instance row, trying camelCase and snake_case. */
export function readObjectProperty(
  obj: Record<string, unknown> | null | undefined,
  property: string,
): unknown {
  if (!obj) return undefined;
  if (property in obj) return obj[property];
  const snake = property.replace(/[A-Z]/g, (m) => `_${m.toLowerCase()}`);
  if (snake in obj) return obj[snake];
  const camel = property.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase());
  if (camel in obj) return obj[camel];
  return undefined;
}

function normalizeContext(
  ctxOrPrincipal?: PrefillContext | string | null,
): PrefillContext {
  if (typeof ctxOrPrincipal === "string" || ctxOrPrincipal == null) {
    return { principalUrn: ctxOrPrincipal };
  }
  return ctxOrPrincipal;
}

function resolveDefault(
  defaultDef: ActionParameterDefault | undefined,
  ctx: PrefillContext,
): unknown {
  if (!defaultDef) return undefined;
  if (defaultDef.kind === "static") {
    return defaultDef.value;
  }
  if (defaultDef.kind === "current_object") {
    return ctx.currentObjectId ?? undefined;
  }
  if (defaultDef.kind === "object_property") {
    const source = defaultDef.object ?? "current";
    if (ctx.onlyFromObjectParameter != null && source !== ctx.onlyFromObjectParameter) {
      return undefined;
    }
    const row =
      source === "current"
        ? ctx.currentObject
        : ctx.objectsByParameter?.[source] ?? null;
    if (!defaultDef.property) return undefined;
    return readObjectProperty(row ?? undefined, defaultDef.property);
  }
  return undefined;
}

/**
 * Prefill map for an Action form. Order: Form `default` first, then type-class
 * generators (`actions:generate_uuid`, `actions:prefill_current_user`) which
 * win when present — same Foundry split (defaults vs type-class prefills).
 */
export function prefillActionParameters(
  parameters: ActionParameter[] | undefined,
  ctxOrPrincipal?: PrefillContext | string | null,
): Record<string, unknown> {
  const ctx = normalizeContext(ctxOrPrincipal);
  const out: Record<string, unknown> = {};

  for (const p of parameters ?? []) {
    if (ctx.onlyFromObjectParameter != null) {
      const d = p.default;
      if (
        !d ||
        d.kind !== "object_property" ||
        (d.object ?? "current") !== ctx.onlyFromObjectParameter
      ) {
        continue;
      }
    }

    const fromDefault = resolveDefault(p.default, ctx);
    if (fromDefault !== undefined) {
      out[p.name] = fromDefault;
    }

    const classes = p.type_classes;
    if (hasTypeClass(classes, "actions", "generate_uuid")) {
      out[p.name] = crypto.randomUUID();
      continue;
    }
    if (hasTypeClass(classes, "actions", "prefill_current_user") && ctx.principalUrn) {
      out[p.name] = ctx.principalUrn;
    }
  }
  return out;
}
