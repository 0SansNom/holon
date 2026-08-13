import { describe, expect, it } from "vitest";
import {
  findPropertyWithTypeClass,
  hasTypeClass,
  isValidTypeClass,
  parseTypeClassesInput,
} from "./typeClassUtils";

describe("typeClassUtils", () => {
  it("parses comma-separated type classes", () => {
    expect(parseTypeClassesInput(" hubble:icon , hierarchy:parent ")).toEqual([
      "hubble:icon",
      "hierarchy:parent",
    ]);
  });

  it("validates bare tags and kind:name", () => {
    expect(isValidTypeClass("priority")).toBe(true);
    expect(isValidTypeClass("hubble:media_url")).toBe(true);
    expect(isValidTypeClass("hubble-oe:hide-action")).toBe(true);
    expect(isValidTypeClass("Bad Class!")).toBe(false);
  });

  it("matches hubble-oe:hide-action for Action dropdown filtering", () => {
    expect(hasTypeClass(["hubble-oe:hide-action"], "hubble-oe", "hide-action")).toBe(true);
    expect(hasTypeClass(["priority"], "hubble-oe", "hide-action")).toBe(false);
  });

  it("finds property carrying hubble:icon", () => {
    const props = {
      title: { type_classes: ["priority"] },
      logoUrl: { type_classes: ["hubble:icon"] },
    };
    expect(findPropertyWithTypeClass(props, "hubble", "icon")).toBe("logoUrl");
    expect(findPropertyWithTypeClass(props, "hubble", "media_url")).toBeNull();
  });
});
