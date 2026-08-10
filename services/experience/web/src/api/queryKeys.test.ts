import { describe, expect, it } from "vitest";
import { queryKeys } from "./queryKeys";

describe("queryKeys", () => {
  it("returns stable object keys", () => {
    expect(queryKeys.objectTypes()).toEqual(["objectTypes"]);
    expect(queryKeys.object("Customer", 42)).toEqual(["object", "Customer", 42]);
  });

  it("scopes search by optional filters", () => {
    expect(queryKeys.search("foo", "Customer", 0, 25)).toEqual(["search", "foo", "Customer", 0, 25]);
  });
});
