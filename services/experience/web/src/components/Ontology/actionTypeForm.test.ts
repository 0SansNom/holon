import { describe, expect, it } from "vitest";
import {
  DEFAULT_ACTION_TYPE_FORM,
  isActionTypeCreateValid,
  parseActionTypeJsonFields,
} from "./actionTypeForm";

describe("parseActionTypeJsonFields", () => {
  it("parses valid JSON fields", () => {
    const result = parseActionTypeJsonFields(DEFAULT_ACTION_TYPE_FORM);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.parameters).toEqual([]);
      expect(result.edits).toEqual(JSON.parse(DEFAULT_ACTION_TYPE_FORM.editsJson));
    }
  });

  it("rejects invalid JSON", () => {
    const result = parseActionTypeJsonFields({ ...DEFAULT_ACTION_TYPE_FORM, parametersJson: "{" });
    expect(result.ok).toBe(false);
  });
});

describe("isActionTypeCreateValid", () => {
  it("requires target, local name, description, and function name when needed", () => {
    expect(isActionTypeCreateValid(DEFAULT_ACTION_TYPE_FORM)).toBe(false);
    expect(
      isActionTypeCreateValid({
        ...DEFAULT_ACTION_TYPE_FORM,
        localName: "setPriority",
        description: "Sets priority",
        targetObjectType: "Ticket",
      }),
    ).toBe(true);
  });
});
