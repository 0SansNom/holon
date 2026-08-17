import { describe, expect, it } from "vitest";
import { collectObjectMediaItems } from "./objectMedia";

describe("collectObjectMediaItems", () => {
  it("picks media_url and icon type classes", () => {
    const items = collectObjectMediaItems(
      { id: 1, photo: "https://example.com/a.jpg", logo: "https://example.com/b.png", name: "x" },
      {
        name: "Customer",
        property_mapping: { id: "id", photo: "photo", logo: "logo", name: "name" },
        property_types: {
          photo: { kind: "value_type", value_type: "string", type_classes: ["hubble:media_url"] },
          logo: { kind: "value_type", value_type: "string", type_classes: ["hubble:icon"] },
        },
      } as never,
    );
    expect(items).toEqual([
      { property: "photo", url: "https://example.com/a.jpg", kind: "media_url" },
      { property: "logo", url: "https://example.com/b.png", kind: "icon" },
    ]);
  });
});
