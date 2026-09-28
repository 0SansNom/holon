import { describe, expect, it } from "vitest";
import {
  findPropertyWithTypeClass,
  hasTypeClass,
  isValidTypeClass,
  parseTypeClassesInput,
} from "./typeClassUtils";

describe("typeClassUtils", () => {
  it("parses comma-separated type classes", () => {
    expect(parseTypeClassesInput(" display:icon , hierarchy:parent ")).toEqual([
      "display:icon",
      "hierarchy:parent",
    ]);
  });

  it("validates bare tags and kind:name", () => {
    expect(isValidTypeClass("priority")).toBe(true);
    expect(isValidTypeClass("display:media_url")).toBe(true);
    expect(isValidTypeClass("explorer:hide-action")).toBe(true);
    expect(isValidTypeClass("Bad Class!")).toBe(false);
  });

  it("matches explorer:hide-action for Action dropdown filtering", () => {
    expect(hasTypeClass(["explorer:hide-action"], "explorer", "hide-action")).toBe(true);
    expect(hasTypeClass(["priority"], "explorer", "hide-action")).toBe(false);
  });

  it("finds property carrying display:icon", () => {
    const props = {
      title: { type_classes: ["priority"] },
      logoUrl: { type_classes: ["display:icon"] },
    };
    expect(findPropertyWithTypeClass(props, "display", "icon")).toBe("logoUrl");
    expect(findPropertyWithTypeClass(props, "display", "media_url")).toBeNull();
  });
});
