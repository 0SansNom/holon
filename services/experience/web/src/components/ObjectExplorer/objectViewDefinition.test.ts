import { describe, expect, it } from "vitest";
import {
  defaultObjectViewDefinition,
  normalizeObjectViewDefinition,
  resolveIframeUrl,
} from "./objectViewDefinition";

describe("objectViewDefinition", () => {
  it("builds a default configured view with Main/Details/History", () => {
    const def = defaultObjectViewDefinition("Aircraft");
    expect(def.objectType).toBe("Aircraft");
    expect(def.tabs.map((t) => t.id)).toEqual(["main", "details", "history"]);
    expect(def.tabs[0].widgets.some((w) => w.kind === "overview")).toBe(true);
  });

  it("normalizes and drops invalid widgets", () => {
    const normalized = normalizeObjectViewDefinition({
      objectType: "Aircraft",
      tabs: [
        {
          id: "main",
          title: "Main",
          widgets: [
            { id: "a", kind: "overview" },
            { id: "b", kind: "nope" } as unknown as { id: string; kind: "overview" },
          ],
        },
      ],
    });
    expect(normalized?.tabs[0].widgets).toHaveLength(1);
    expect(normalized?.tabs[0].widgets[0].kind).toBe("overview");
  });

  it("resolves iframe placeholders", () => {
    expect(resolveIframeUrl("https://x/{{objectType}}/{{objectId}}", "Aircraft", "42")).toBe(
      "https://x/Aircraft/42",
    );
  });
});
