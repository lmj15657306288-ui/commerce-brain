import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fixtures = vi.hoisted(() => {
  const alert = {
    alert_id: "alert_1",
    scope: { organization_id: "org_1", shop_id: "shop_1" },
    priority: "P1",
    status: "OPEN",
    reason_code: "LOW_STOCK",
    summary: "库存信号需要关注",
    evidence_refs: [],
    business_impact: {},
    recommended_action: "请检查关联证据。",
    created_at: "2026-09-27T00:00:00Z",
    updated_at: "2026-09-27T00:00:00Z",
    cooldown_until: null,
    shop_name: "Shop One",
    freshness: "FRESH",
  };
  const approval = {
    approval_id: "approval_1",
    title: "Proposal review",
    proposal_id: "proposal_1",
    scope: { organization_id: "org_1", shop_id: "shop_1" },
    shop_name: "Shop One",
    risk_level: "HIGH",
    requested_by: "actor_operator",
    requested_at: "2026-09-27T00:00:00Z",
    reason: "需要 Owner 审批的提案。",
    business_impact: null,
    evidence: [],
    expires_at: "2026-09-28T00:00:00Z",
    status: "PENDING",
    decided_by: null,
    decided_at: null,
    decision_note: null,
    freshness: "FRESH",
  };
  return {
    alert,
    approval,
    calls: [] as string[],
  };
});

vi.mock("./api/client", () => {
  class FakeApiError extends Error {
    status = 0;
    get userMessage() {
      return this.message;
    }
  }

  class FakeControlPlaneClient {
    hasSession() {
      return true;
    }
    setSession() {}
    clearSession() {}
    setSessionExpiredHandler() {}
    async publicHealth() {
      return { status: "ok" };
    }
    async summary() {
      return {
        schema_version: 1,
        organization_id: "org_1",
        overall_health: "DEGRADED",
        attention_counts: { need_decision: 0, need_approval: 1, need_awareness: 1 },
        shop_count: 1,
        worker_health: "OFFLINE",
        system_health: "HEALTHY",
        generated_at: "2026-09-27T00:00:00Z",
        freshness: "FRESH",
        counts_complete: true,
      };
    }
    async inbox() {
      return {
        items: [
          {
            item_id: fixtures.alert.alert_id,
            item_type: "ALERT",
            owner_category: "NEED_AWARENESS",
            title: fixtures.alert.summary,
            summary: fixtures.alert.summary,
            why_it_matters: "Impact not quantified yet.",
            recommended_action: fixtures.alert.recommended_action,
            scope: fixtures.alert.scope,
            priority: "P1",
            business_impact: null,
            status: "OPEN",
            created_at: fixtures.alert.created_at,
            updated_at: fixtures.alert.updated_at,
            freshness: "FRESH",
            source_ref: fixtures.alert.alert_id,
            shop_name: fixtures.alert.shop_name,
            rank_score: null,
            rank_reason: null,
          },
          {
            item_id: fixtures.approval.approval_id,
            item_type: "APPROVAL",
            owner_category: "NEED_APPROVAL",
            title: fixtures.approval.title,
            summary: fixtures.approval.reason,
            why_it_matters: "A proposal is waiting for an owner decision.",
            recommended_action: "Review the proposal.",
            scope: fixtures.approval.scope,
            priority: "HIGH",
            business_impact: null,
            status: "PENDING",
            created_at: fixtures.approval.requested_at,
            updated_at: fixtures.approval.requested_at,
            freshness: "FRESH",
            source_ref: fixtures.approval.approval_id,
            shop_name: fixtures.approval.shop_name,
            rank_score: null,
            rank_reason: null,
          },
        ],
      };
    }
    async alerts() {
      return { items: [fixtures.alert] };
    }
    async alert() {
      return fixtures.alert;
    }
    async approvals() {
      return { items: [fixtures.approval] };
    }
    async approval() {
      return fixtures.approval;
    }
    async shops() {
      return {
        items: [{
          shop_id: "shop_1",
          shop_name: "Shop One",
          status: "ACTIVE",
          category_memberships: [],
          high_priority_alerts: 1,
          pending_approvals: 1,
          important_tasks: 0,
          health: "ATTENTION",
          live_status: "NOT_CONNECTED",
          freshness: "FRESH",
          last_updated: "2026-09-27T00:00:00Z",
        }],
      };
    }
    async shop() {
      return { shop: (await this.shops()).items[0], attention_items: [], approvals: [], business_data_status: "NOT_CONNECTED" };
    }
    async systemHealth() {
      return {
        cloud_status: "HEALTHY",
        brain_worker_status: "OFFLINE",
        realtime_status: "HEALTHY",
        data_freshness: "FRESH",
        last_updated: "2026-09-27T00:00:00Z",
        workers: [{
          worker_id: "worker_mac",
          worker_type: "MAC_BRAIN",
          status: "OFFLINE",
          last_seen_at: "2026-09-27T00:00:00Z",
          capabilities: ["Laya"],
          freshness: "STALE",
        }],
      };
    }
    async liveStatus() {
      return {
        status: "NOT_CONNECTED",
        freshness: "UNKNOWN",
        last_updated: null,
        message: "Live intelligence not connected yet",
      };
    }
    async openMobileSession() {
      return "device_owner_test";
    }
    async realtimeTicket() {
      return "ticket";
    }
    async acknowledgeAlert() {
      fixtures.calls.push("acknowledge-alert");
      return fixtures.alert;
    }
    async resolveAlert() {
      fixtures.calls.push("resolve-alert");
      return fixtures.alert;
    }
    async decideApproval(_id: string, decision: string) {
      fixtures.calls.push(`decide:${decision}`);
      return fixtures.approval;
    }
  }

  return { ApiError: FakeApiError, ControlPlaneClient: FakeControlPlaneClient };
});

vi.mock("./api/realtime", () => ({
  connectOwnerEvents: vi.fn((options: { onStatus: (status: "CONNECTED") => void }) => {
    options.onStatus("CONNECTED");
    return () => undefined;
  }),
  ownerEventRefreshTargets: vi.fn(() => []),
}));

import { App } from "./app";

describe("Owner Cockpit", () => {
  beforeEach(() => {
    fixtures.calls.length = 0;
    sessionStorage.setItem(
      "commerce-brain.owner.session",
      "header.eyJvcmdhbml6YXRpb25faWQiOiJvcmcxIn0.signature",
    );
  });

  afterEach(() => {
    cleanup();
    sessionStorage.clear();
  });

  it("renders overview with worker offline instead of claiming all healthy", async () => {
    render(<App />);
    expect(await screen.findByText("全店群状态")).toBeInTheDocument();
    expect(screen.getByText("Worker 离线")).toBeInTheDocument();
    expect(screen.getByText("直播未接入")).toBeInTheDocument();
  });

  it("groups alert and approval into the Owner Inbox", async () => {
    render(<App />);
    await screen.findByText("全店群状态");
    fireEvent.click(screen.getByRole("button", { name: /待办/ }));
    expect(screen.getAllByText("库存信号需要关注").length).toBeGreaterThan(0);
    expect(screen.getByText("Proposal review")).toBeInTheDocument();
  });

  it("requires an online confirmation before approving a proposal", async () => {
    render(<App />);
    await screen.findByText("全店群状态");
    fireEvent.click(screen.getByRole("button", { name: "打开导航" }));
    fireEvent.click(screen.getByRole("button", { name: "审批" }));
    fireEvent.click(screen.getByRole("button", { name: /Proposal review/ }));
    fireEvent.click(screen.getByRole("button", { name: "批准提案" }));
    expect(screen.getByText("确认批准该提案？")).toBeInTheDocument();
    expect(fixtures.calls).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "确认" }));
    await waitFor(() => expect(fixtures.calls).toContain("decide:approve"));
  });
});
