import { describe, expect, it } from "vitest";
import type { ActionDefinition } from "../../api/knowledge";
import { actionsForObjectType, actionTargetsObjectType } from "./objectExplorerUtils";
import {
  BATCH_ACTION_CAP,
  bulkActionTargets,
  explorerAvailableColumnKeys,
  explorerPropertyKeys,
  formatBatchInvokeMessage,
  formatSingleInvokeMessage,
  frozenLeftOffset,
  FROZEN_SELECT_WIDTH,
  nextFocusedRowId,
  resolveInvokeActionName,
  selectObjectTableBaseRows,
  shouldUseServerPaging,
  visibleObjectSetsForType,
} from "./objectTableModel";

const setStatus: ActionDefinition = {
  name: "Customer.setStatus",
  target_object_type: "Customer",
  required_permission: "write",
  risk_level: "low",
  description: "Set status",
  parameters: [{ name: "status", required: true, value_type: "string" }],
};

describe("shouldUseServerPaging", () => {
  it("is true only for unfiltered all-instances browse", () => {
    expect(shouldUseServerPaging({ predicateCount: 0 })).toBe(true);
    expect(shouldUseServerPaging({ setName: "active", predicateCount: 0 })).toBe(false);
    expect(shouldUseServerPaging({ listId: "abc", predicateCount: 0 })).toBe(false);
    expect(shouldUseServerPaging({ predicateCount: 1 })).toBe(false);
  });
});

describe("selectObjectTableBaseRows", () => {
  const allRows = [{ id: "1" }, { id: "2" }, { id: "3" }];
  const evaluated = [{ id: "2" }];
  const page = [{ id: "1" }];

  it("prefers a saved list over object sets and paging", () => {
    expect(
      selectObjectTableBaseRows({
        activeList: { instanceIds: ["2", "9"] },
        useServerPaging: true,
        setName: "active",
        setTypeMismatch: false,
        allRows,
        serverPageData: page,
        evaluatedData: evaluated,
      }),
    ).toEqual([{ id: "2" }]);
  });

  it("uses the server page when paging", () => {
    expect(
      selectObjectTableBaseRows({
        useServerPaging: true,
        setTypeMismatch: false,
        allRows,
        serverPageData: page,
        evaluatedData: evaluated,
      }),
    ).toEqual(page);
  });

  it("returns empty on object-set type mismatch", () => {
    expect(
      selectObjectTableBaseRows({
        useServerPaging: false,
        setName: "active",
        setTypeMismatch: true,
        allRows,
        evaluatedData: evaluated,
      }),
    ).toEqual([]);
  });
});

describe("visibleObjectSetsForType", () => {
  const sets = [
    { name: "durable", object_type_urn: "hl:t:w:object-type:Customer", visibility: "normal" },
    { name: "OsdkReview_1786514842831", object_type_urn: "hl:t:w:object-type:Customer", visibility: "normal" },
    { name: "hidden", object_type_urn: "hl:t:w:object-type:Customer", visibility: "hidden" },
    { name: "other", object_type_urn: "hl:t:w:object-type:Order", visibility: "normal" },
  ];

  it("hides ephemeral leftovers unless the set is active", () => {
    expect(visibleObjectSetsForType(sets, "Customer").map((s) => s.name)).toEqual(["durable"]);
    expect(visibleObjectSetsForType(sets, "Customer", "OsdkReview_1786514842831").map((s) => s.name)).toEqual([
      "durable",
      "OsdkReview_1786514842831",
    ]);
  });
});

describe("actionsForObjectType", () => {
  const viaInterface = { ...setStatus, name: "Reviewable.approve", target_object_type: null, target_interface: "Reviewable" };

  it("includes interface-targeted actions when the type implements them", () => {
    expect(actionTargetsObjectType(viaInterface, "Customer", ["Reviewable"])).toBe(true);
    expect(actionsForObjectType([setStatus, viaInterface], "Customer", ["Reviewable"]).map((a) => a.name)).toEqual([
      "Customer.setStatus",
      "Reviewable.approve",
    ]);
  });
});

describe("explorer column helpers", () => {
  it("prefers ontology mapping keys over row leftovers", () => {
    expect(explorerPropertyKeys({ property_mapping: { name: "name", status: "status" } }, [])).toEqual([
      "name",
      "status",
    ]);
    expect(
      explorerAvailableColumnKeys(
        { property_mapping: { name: "name" }, title_key: "name" },
        { id: "1", name: "Acme", title: "ignored", extra: 1 },
        [],
      ),
    ).toEqual(["name", "extra", "id"]);
  });
});

describe("invoke helpers", () => {
  it("uses the full name when the Action declares parameters", () => {
    expect(resolveInvokeActionName(setStatus, "Customer.setStatus")).toBe("Customer.setStatus");
    expect(resolveInvokeActionName({ name: "Customer.legacy" }, "Customer.legacy")).toBe("legacy");
  });

  it("caps bulk targets and formats invoke copy", () => {
    const selected = Array.from({ length: BATCH_ACTION_CAP + 2 }, (_, i) => String(i));
    const bulk = bulkActionTargets(selected, "9");
    expect(bulk.ids).toHaveLength(BATCH_ACTION_CAP);
    expect(bulk.capWarning).toMatch(/52/);
    expect(formatSingleInvokeMessage("pending_approval")).toMatch(/approval/);
    expect(formatBatchInvokeMessage("setStatus", { succeeded: 2, failed: 1, count: 3, results: [] }).ok).toBe(false);
  });
});

describe("nextFocusedRowId", () => {
  it("opens preview on the first selection", () => {
    expect(nextFocusedRowId({ a: true }, null)).toEqual({ focusedId: "a", openPreview: true });
    expect(nextFocusedRowId({ b: true }, "a")).toEqual({ focusedId: "b", openPreview: false });
  });
});

describe("frozenLeftOffset", () => {
  it("accounts for the select column", () => {
    expect(frozenLeftOffset(0)).toBe(FROZEN_SELECT_WIDTH);
  });
});
