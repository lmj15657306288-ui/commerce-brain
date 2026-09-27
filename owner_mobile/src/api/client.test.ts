import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, ControlPlaneClient } from "./client";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("ControlPlaneClient", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
  });

  it("uses the Control Plane origin for realtime sockets", () => {
    const client = new ControlPlaneClient("https://brain.apizz.cc.cd");
    expect(client.realtimeUrl("/ws/events")).toBe(
      "wss://brain.apizz.cc.cd/ws/events",
    );
  });

  it("opens a mobile session and sends device_id on Owner actions", async () => {
    const calls: Array<{ url: string; body: unknown }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ url, body });
      if (url.endsWith("/devices/session")) return response({ ok: true });
      if (url.endsWith("/owner/alerts/alert_1/ack")) return response({ ok: true });
      if (url.endsWith("/owner/alerts/alert_1")) {
        return response({
          alert_id: "alert_1",
          scope: { organization_id: "org_1", shop_id: "shop_1" },
          priority: "P1",
          status: "ACKNOWLEDGED",
          reason_code: "TEST",
          summary: "Test alert",
          evidence_refs: [],
          business_impact: {},
          recommended_action: "Review",
          created_at: "2026-09-27T00:00:00Z",
          updated_at: "2026-09-27T00:00:00Z",
          cooldown_until: null,
          freshness: "FRESH",
        });
      }
      throw new Error(`unexpected URL ${url}`);
    });
    const client = new ControlPlaneClient("https://brain.apizz.cc.cd", fetcher);
    client.setSession("owner-token");

    const deviceId = await client.openMobileSession("org_1");
    await client.acknowledgeAlert("alert_1", "ack-1");

    expect(deviceId).toMatch(/^device_owner_/);
    expect(calls.find((call) => call.url.endsWith("/owner/alerts/alert_1/ack"))?.body).toEqual({
      device_id: deviceId,
      idempotency_key: "ack-1",
    });
  });

  it("clears the session and reports expiry on 401", async () => {
    const fetcher = vi.fn(async () => response({ error_code: "UNAUTHENTICATED" }, 401));
    const client = new ControlPlaneClient("https://brain.apizz.cc.cd", fetcher);
    const expired = vi.fn();
    client.setSession("expired-token");
    client.setSessionExpiredHandler(expired);

    try {
      await client.summary();
      throw new Error("expected session expiry");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(401);
    }

    expect(client.hasSession()).toBe(false);
    expect(expired).toHaveBeenCalledOnce();
  });
});
