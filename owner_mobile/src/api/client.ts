import type { paths } from "./generated";
import type {
  InboxFilter,
  OwnerAlert,
  OwnerAlertPage,
  OwnerApproval,
  OwnerApprovalPage,
  OwnerInboxPage,
  OwnerLiveStatus,
  OwnerShopDetail,
  OwnerShopPage,
  OwnerSummary,
  OwnerSystemHealth,
  PublicHealth,
} from "./types";

const TOKEN_KEY = "commerce-brain.owner.session";
const DEVICE_PREFIX = "commerce-brain.owner.device.";
export const API_ORIGIN =
  import.meta.env.VITE_CONTROL_PLANE_URL?.replace(/\/+$/, "") ||
  "https://brain.apizz.cc.cd";

type OwnerActionBody = NonNullable<
  paths["/owner/alerts/{alert_id}/ack"]["post"]["requestBody"]
>["content"]["application/json"];
type ApprovalDecisionPath =
  | "/owner/approvals/{approval_id}/approve"
  | "/owner/approvals/{approval_id}/reject"
  | "/owner/approvals/{approval_id}/request-revision";
type DecisionBody = NonNullable<
  paths[ApprovalDecisionPath]["post"]["requestBody"]
>["content"]["application/json"];

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }

  get userMessage(): string {
    if (this.status === 401) return "Session expired. Re-enter your Owner token.";
    if (this.status === 403) return "No permission to view or change this item.";
    if (this.status === 404) return "This item is no longer available.";
    if (this.status === 409) return "State changed. Refresh before trying again.";
    if (this.status === 503) return "System temporarily unavailable. No change was made.";
    if (this.status === 0) return "Cloud is unreachable. Read-only actions are unavailable.";
    return "The request could not be completed.";
  }
}

export interface OwnerApi {
  setSession(token: string): void;
  clearSession(): void;
  hasSession(): boolean;
  setSessionExpiredHandler(handler: () => void): void;
  realtimeUrl(path: string): string;
  publicHealth(): Promise<PublicHealth>;
  summary(): Promise<OwnerSummary>;
  inbox(filter?: InboxFilter, shopId?: string): Promise<OwnerInboxPage>;
  alerts(status?: string): Promise<OwnerAlertPage>;
  alert(id: string): Promise<OwnerAlert>;
  approvals(status?: string): Promise<OwnerApprovalPage>;
  approval(id: string): Promise<OwnerApproval>;
  shops(): Promise<OwnerShopPage>;
  shop(id: string): Promise<OwnerShopDetail>;
  systemHealth(): Promise<OwnerSystemHealth>;
  liveStatus(): Promise<OwnerLiveStatus>;
  openMobileSession(organizationId: string): Promise<string>;
  realtimeTicket(deviceId: string, organizationId: string, cursor: number): Promise<string>;
  acknowledgeAlert(id: string, idempotencyKey: string): Promise<OwnerAlert>;
  resolveAlert(id: string, idempotencyKey: string): Promise<OwnerAlert>;
  decideApproval(
    id: string,
    decision: "approve" | "reject" | "request-revision",
    idempotencyKey: string,
    note?: string,
  ): Promise<OwnerApproval>;
}

export class ControlPlaneClient implements OwnerApi {
  private sessionExpiredHandler: () => void = () => undefined;
  private token: string | null;
  private deviceId: string | null = null;

  constructor(
    private readonly baseUrl = API_ORIGIN,
    private readonly fetcher: typeof fetch = fetch,
  ) {
    this.token = safeStorageGet(sessionStorage, TOKEN_KEY);
  }

  setSession(token: string): void {
    const normalized = token.trim();
    this.token = normalized;
    safeStorageSet(sessionStorage, TOKEN_KEY, normalized);
  }

  clearSession(): void {
    this.token = null;
    safeStorageRemove(sessionStorage, TOKEN_KEY);
    this.deviceId = null;
  }

  hasSession(): boolean {
    return Boolean(this.token);
  }

  setSessionExpiredHandler(handler: () => void): void {
    this.sessionExpiredHandler = handler;
  }

  realtimeUrl(path: string): string {
    const url = new URL(path, `${this.baseUrl}/`);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    return url.toString();
  }

  async publicHealth(): Promise<PublicHealth> {
    return this.request("/health", { authenticated: false });
  }

  summary(): Promise<OwnerSummary> {
    return this.request("/owner/summary");
  }

  inbox(
    filter: InboxFilter = "ALL",
    shopId?: string,
  ): Promise<OwnerInboxPage> {
    const query = new URLSearchParams({ limit: "100" });
    if (filter !== "ALL") query.set("owner_category", filter);
    if (shopId) query.set("shop_id", shopId);
    return this.request(`/owner/inbox?${query}`);
  }

  alerts(status = "OPEN"): Promise<OwnerAlertPage> {
    return this.request(`/owner/alerts?${new URLSearchParams({ status, limit: "100" })}`);
  }

  alert(id: string): Promise<OwnerAlert> {
    return this.request(`/owner/alerts/${encodeURIComponent(id)}`);
  }

  approvals(status = "PENDING"): Promise<OwnerApprovalPage> {
    return this.request(
      `/owner/approvals?${new URLSearchParams({ status, limit: "100" })}`,
    );
  }

  approval(id: string): Promise<OwnerApproval> {
    return this.request(`/owner/approvals/${encodeURIComponent(id)}`);
  }

  shops(): Promise<OwnerShopPage> {
    return this.request("/owner/shops?limit=100");
  }

  shop(id: string): Promise<OwnerShopDetail> {
    return this.request(`/owner/shops/${encodeURIComponent(id)}`);
  }

  systemHealth(): Promise<OwnerSystemHealth> {
    return this.request("/owner/system-health");
  }

  liveStatus(): Promise<OwnerLiveStatus> {
    return this.request("/owner/live-status");
  }

  async openMobileSession(organizationId: string): Promise<string> {
    const storageKey = `${DEVICE_PREFIX}${organizationId}`;
    let deviceId = safeStorageGet(localStorage, storageKey);
    if (!deviceId) {
      deviceId = `device_owner_${crypto.randomUUID().replaceAll("-", "")}`;
      safeStorageSet(localStorage, storageKey, deviceId);
    }
    await this.request("/devices/session", {
      method: "POST",
      body: {
        device_id: deviceId,
        device_type: "MOBILE",
        organization_id: organizationId,
        shop_ids: [],
        capabilities: ["owner_inbox", "event_replay"],
        ttl_seconds: 86400,
        idempotency_key: `owner_mobile_${crypto.randomUUID()}`,
      },
    });
    this.deviceId = deviceId;
    return deviceId;
  }

  async realtimeTicket(
    deviceId: string,
    organizationId: string,
    cursor: number,
  ): Promise<string> {
    const response = await this.request<{ ticket: string }>("/owner/realtime-ticket", {
      method: "POST",
      body: {
        device_id: deviceId,
        scope: { organization_id: organizationId },
        cursor,
      },
    });
    return response.ticket;
  }

  async acknowledgeAlert(id: string, idempotencyKey: string): Promise<OwnerAlert> {
    await this.request(`/owner/alerts/${encodeURIComponent(id)}/ack`, {
      method: "POST",
      body: this.ownerActionBody(idempotencyKey),
    });
    return this.alert(id);
  }

  async resolveAlert(id: string, idempotencyKey: string): Promise<OwnerAlert> {
    await this.request(`/owner/alerts/${encodeURIComponent(id)}/resolve`, {
      method: "POST",
      body: this.ownerActionBody(idempotencyKey),
    });
    return this.alert(id);
  }

  async decideApproval(
    id: string,
    decision: "approve" | "reject" | "request-revision",
    idempotencyKey: string,
    note?: string,
  ): Promise<OwnerApproval> {
    const path: Record<typeof decision, ApprovalDecisionPath> = {
      approve: "/owner/approvals/{approval_id}/approve",
      reject: "/owner/approvals/{approval_id}/reject",
      "request-revision": "/owner/approvals/{approval_id}/request-revision",
    };
    const pathName = path[decision].replace(
      "{approval_id}",
      encodeURIComponent(id),
    );
    const body: DecisionBody = {
      ...this.ownerActionBody(idempotencyKey),
      decision_note: note,
    };
    await this.request(pathName, {
      method: "POST",
      body,
    });
    return this.approval(id);
  }

  private ownerActionBody(
    idempotencyKey: string,
  ): OwnerActionBody {
    if (!this.deviceId) {
      throw new ApiError(401, "MOBILE_SESSION_REQUIRED", "Mobile session is required.");
    }
    return {
      device_id: this.deviceId,
      idempotency_key: idempotencyKey,
    };
  }

  private async request<T>(
    path: string,
    options: RequestOptions = {},
  ): Promise<T> {
    const headers = new Headers({
      Accept: "application/json",
      "X-Request-ID": `owner-${crypto.randomUUID()}`,
    });
    if (options.body !== undefined) headers.set("Content-Type", "application/json");
    if (options.authenticated !== false && this.token) {
      headers.set("Authorization", `Bearer ${this.token}`);
    }

    let response: Response;
    try {
      response = await this.fetcher(`${this.baseUrl}${path}`, {
        method: options.method || "GET",
        headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        cache: "no-store",
        credentials: "omit",
      });
    } catch {
      throw new ApiError(0, "NETWORK_UNAVAILABLE", "Cloud is unreachable.");
    }

    if (!response.ok) {
      let code = "REQUEST_FAILED";
      try {
        const error = (await response.json()) as { error_code?: string };
        code = error.error_code || code;
      } catch {
        // A proxy error body is intentionally not surfaced to the owner.
      }
      if (response.status === 401) {
        this.clearSession();
        this.sessionExpiredHandler();
      }
      throw new ApiError(response.status, code, code);
    }
    return (await response.json()) as T;
  }
}

function safeStorageGet(storage: Storage, key: string): string | null {
  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

function safeStorageSet(storage: Storage, key: string, value: string): void {
  try {
    storage.setItem(key, value);
  } catch {
    // Session can continue without persistence in restricted browser contexts.
  }
}

function safeStorageRemove(storage: Storage, key: string): void {
  try {
    storage.removeItem(key);
  } catch {
    // Session cleanup still clears the in-memory credential.
  }
}

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  authenticated?: boolean;
}

export type ApprovalDecisionRequestBody =
  DecisionBody;
