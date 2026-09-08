import { describe, expect, it } from "vitest";
import { NAV_ITEMS, NAV_SECTIONS, SEQUENTIAL_SHORTCUTS } from "./navigation";

describe("navigation", () => {
  it("groups every destination into Explore / Build / Govern", () => {
    expect(NAV_SECTIONS.map((s) => s.id)).toEqual(["explore", "build", "govern"]);
    expect(NAV_ITEMS.map((item) => item.to)).toEqual([
      "/objects",
      "/search",
      "/glossary",
      "/approvals",
      "/sources",
      "/pipelines",
      "/catalog",
      "/ontology",
      "/applications",
      "/collections",
      "/admin",
    ]);
  });

  it("keeps sequential shortcuts pointing at real nav destinations", () => {
    const destinations = new Set(NAV_ITEMS.map((item) => item.to));
    for (const to of Object.values(SEQUENTIAL_SHORTCUTS)) {
      expect(destinations.has(to)).toBe(true);
    }
  });
});
