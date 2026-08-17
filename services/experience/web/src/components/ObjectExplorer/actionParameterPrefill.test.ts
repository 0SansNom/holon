import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { prefillActionParameters, readObjectProperty } from "./actionParameterPrefill";
import type { ActionParameter } from "../../api/knowledge";

describe("prefillActionParameters", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", {
      randomUUID: () => "11111111-2222-3333-4444-555555555555",
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("prefills generate_uuid and prefill_current_user", () => {
    const parameters: ActionParameter[] = [
      { name: "id", required: true, value_type: "string", type_classes: ["actions:generate_uuid"] },
      {
        name: "actor",
        required: false,
        value_type: "string",
        type_classes: ["actions:prefill_current_user"],
      },
      { name: "note", required: false, value_type: "string" },
    ];
    expect(prefillActionParameters(parameters, "hl:acme:user:jdoe")).toEqual({
      id: "11111111-2222-3333-4444-555555555555",
      actor: "hl:acme:user:jdoe",
    });
  });

  it("skips current_user prefill without principal", () => {
    const parameters: ActionParameter[] = [
      {
        name: "actor",
        required: false,
        value_type: "string",
        type_classes: ["actions:prefill_current_user"],
      },
    ];
    expect(prefillActionParameters(parameters, null)).toEqual({});
  });

  it("applies static, current_object, and object_property defaults", () => {
    const parameters: ActionParameter[] = [
      {
        name: "type",
        required: true,
        value_type: "string",
        default: { kind: "static", value: "A320" },
      },
      {
        name: "plane",
        required: true,
        kind: "object_reference",
        object_type: "Plane",
        default: { kind: "current_object" },
      },
      {
        name: "hours",
        required: false,
        value_type: "integer",
        default: { kind: "object_property", object: "current", property: "flightHours" },
      },
    ];
    expect(
      prefillActionParameters(parameters, {
        currentObjectId: "42",
        currentObject: { id: "42", flight_hours: 1200 },
      }),
    ).toEqual({
      type: "A320",
      plane: "42",
      hours: 1200,
    });
  });

  it("fills dependents when onlyFromObjectParameter is set", () => {
    const parameters: ActionParameter[] = [
      { name: "customerId", required: true, kind: "object_reference", object_type: "Customer" },
      {
        name: "email",
        required: false,
        value_type: "string",
        default: { kind: "object_property", object: "customerId", property: "email" },
      },
      {
        name: "staticNote",
        required: false,
        value_type: "string",
        default: { kind: "static", value: "keep" },
      },
    ];
    expect(
      prefillActionParameters(parameters, {
        objectsByParameter: { customerId: { id: "7", email: "a@b.co" } },
        onlyFromObjectParameter: "customerId",
      }),
    ).toEqual({ email: "a@b.co" });
  });

  it("type class wins over static default", () => {
    const parameters: ActionParameter[] = [
      {
        name: "id",
        required: true,
        value_type: "string",
        default: { kind: "static", value: "should-not-win" },
        type_classes: ["actions:generate_uuid"],
      },
    ];
    expect(prefillActionParameters(parameters, {})).toEqual({
      id: "11111111-2222-3333-4444-555555555555",
    });
  });
});

describe("readObjectProperty", () => {
  it("resolves camel and snake keys", () => {
    expect(readObjectProperty({ flightHours: 1 }, "flightHours")).toBe(1);
    expect(readObjectProperty({ flight_hours: 2 }, "flightHours")).toBe(2);
    expect(readObjectProperty({ flightHours: 3 }, "flight_hours")).toBe(3);
  });
});
