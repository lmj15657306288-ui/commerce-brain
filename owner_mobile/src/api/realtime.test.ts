import { describe, expect, it } from "vitest";
import { ownerEventRefreshTargets } from "./realtime";

describe("owner realtime event routing", () => {
  it("refreshes only read models affected by an event family", () => {
    expect(ownerEventRefreshTargets("approval.updated")).toEqual([
      "summary",
      "inbox",
      "approvals",
      "shops",
    ]);
    expect(ownerEventRefreshTargets("worker.health.changed")).toEqual([
      "summary",
      "system-health",
    ]);
    expect(ownerEventRefreshTargets("unrelated.event")).toEqual([]);
  });
});
