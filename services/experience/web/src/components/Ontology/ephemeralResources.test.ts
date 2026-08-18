import { describe, expect, it } from "vitest";
import { isEphemeralTestName, partitionEphemeral } from "./ephemeralResources";

describe("isEphemeralTestName", () => {
  it("keeps durable ontology names", () => {
    expect(isEphemeralTestName("Customer")).toBe(false);
    expect(isEphemeralTestName("Order")).toBe(false);
    expect(isEphemeralTestName("SupportTicket")).toBe(false);
    expect(isEphemeralTestName("putOnCreditHold")).toBe(false);
  });

  it("flags pytest unique-name patterns", () => {
    expect(isEphemeralTestName("OsdkReview_1786514842831")).toBe(true);
    expect(isEphemeralTestName("MetaType_1786548686683")).toBe(true);
    expect(isEphemeralTestName("CanRelax214e0233")).toBe(true);
    expect(isEphemeralTestName("CityStringd90ef62f")).toBe(true);
    expect(isEphemeralTestName("pipeline-a1b2c3d4")).toBe(true);
    expect(isEphemeralTestName("test-app-deadbeef")).toBe(true);
    expect(isEphemeralTestName("ShippedOrdersAppa1b2c3")).toBe(true);
    expect(isEphemeralTestName("OsdkReview_1786514842831.setPriority")).toBe(true);
    expect(isEphemeralTestName("Supplier.flag292d5b7f")).toBe(true);
    expect(isEphemeralTestName("iface_reviews0181ae13")).toBe(true);
    expect(isEphemeralTestName("TestInOperator_1786547170")).toBe(true);
    expect(isEphemeralTestName("ordersViaGenJoin_0118d88c")).toBe(true);
    expect(isEphemeralTestName("Customer.ordersViaMidOv_0a5b311e")).toBe(true);
    expect(isEphemeralTestName("productsViaJoinExec_1786916091265")).toBe(true);
  });
});

describe("partitionEphemeral", () => {
  it("splits by name", () => {
    const { kept, hidden } = partitionEphemeral(
      [{ name: "Customer" }, { name: "OsdkReview_1786514842831" }],
      (x) => x.name,
    );
    expect(kept.map((x) => x.name)).toEqual(["Customer"]);
    expect(hidden).toHaveLength(1);
  });
});
