import { describe, expect, it } from "vitest";
import type { RelationType } from "../../api/knowledge";
import {
  CREATE_STEPS,
  DEFAULT_RELATION_TYPE_FORM,
  defaultJoinDatasetName,
  isRelationTypeCreateStepValid,
  relationTypeCreateBody,
  relationTypeFormFromRecord,
  relationTypeUpdateBody,
} from "./relationTypeForm";

const sample: RelationType = {
  urn: "hl:t:w:relation-type:Order.customer",
  name: "acme.orderCustomer",
  source_object_type_urn: "hl:t:w:object-type:Order",
  target_object_type_urn: "hl:t:w:object-type:Customer",
  source_property: "customer_id",
  target_property: "orders",
  cardinality: "many_to_one",
  storage_kind: "foreign_key",
  source_api_name: "customer",
  lifecycle_status: "active",
  project_urn: "hl:t:w:project:sales",
};

describe("relationTypeFormFromRecord", () => {
  it("maps URNs to local names and side metadata", () => {
    const form = relationTypeFormFromRecord(sample);
    expect(form.sourceObjectType).toBe("Order");
    expect(form.targetObjectType).toBe("Customer");
    expect(form.sourceApiName).toBe("customer");
    expect(form.targetApiName).toBe("orders");
    expect(form.projectUrn).toBe("hl:t:w:project:sales");
  });
});

describe("isRelationTypeCreateStepValid", () => {
  it("requires ends then storage-specific fields", () => {
    expect(isRelationTypeCreateStepValid(DEFAULT_RELATION_TYPE_FORM, 0)).toBe(false);
    const ends = { ...DEFAULT_RELATION_TYPE_FORM, name: "Order.customer", sourceObjectType: "Order", targetObjectType: "Customer" };
    expect(isRelationTypeCreateStepValid(ends, 0)).toBe(true);
    expect(isRelationTypeCreateStepValid(ends, 1)).toBe(false);
    expect(isRelationTypeCreateStepValid({ ...ends, sourceProperty: "customer_id" }, 1)).toBe(true);
    expect(CREATE_STEPS).toHaveLength(4);
  });
});

describe("relation type payloads", () => {
  it("omits empty optional create fields and deprecation unless deprecated", () => {
    const body = relationTypeCreateBody({
      ...DEFAULT_RELATION_TYPE_FORM,
      name: "Order.customer",
      sourceObjectType: "Order",
      targetObjectType: "Customer",
      sourceProperty: "customer_id",
      targetProperty: "orders",
    });
    expect(body.join_dataset_urn).toBeUndefined();
    expect(body.deprecation_reason).toBeUndefined();
    expect(defaultJoinDatasetName("Order", "Customer")).toBe("Order_Customer_bridge");
  });

  it("clears project URN on update when emptied", () => {
    const form = relationTypeFormFromRecord(sample);
    expect(relationTypeUpdateBody({ ...form, projectUrn: "" }, sample.project_urn ?? "").clear_project_urn).toBe(true);
    expect(relationTypeUpdateBody(form, sample.project_urn ?? "").clear_project_urn).toBe(false);
  });
});
