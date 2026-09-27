import type { OwnerApi } from "./client";

export type RealtimeStatus =
  | "CONNECTING"
  | "CONNECTED"
  | "RECONNECTING"
  | "OFFLINE"
  | "UNAVAILABLE";

export interface RealtimeEvent {
  cursor: number;
  event_id: string;
  event_type: string;
  [key: string]: unknown;
}

export interface RealtimeOptions {
  api: OwnerApi;
  deviceId: string;
  organizationId: string;
  initialCursor: number;
  onStatus: (status: RealtimeStatus) => void;
  onEvent: (event: RealtimeEvent) => Promise<void>;
  socketFactory?: (url: string) => WebSocket;
}

const RETRY_MAX_MS = 30_000;

export function connectOwnerEvents(options: RealtimeOptions): () => void {
  let stopped = false;
  let socket: WebSocket | undefined;
  let retryTimer: number | undefined;
  let retryDelay = 1_000;
  let lastCursor = options.initialCursor;
  let messageChain = Promise.resolve();
  const cursorKey = `commerce-brain.owner.cursor.${options.deviceId}`;
  const socketFactory =
    options.socketFactory || ((url: string) => new WebSocket(url));

  try {
    const stored = Number(localStorage.getItem(cursorKey));
    if (Number.isSafeInteger(stored) && stored >= lastCursor) lastCursor = stored;
  } catch {
    // A blocked local storage only disables cursor persistence.
  }

  const scheduleRetry = (status: RealtimeStatus) => {
    if (stopped || retryTimer !== undefined) return;
    options.onStatus(status);
    const jitter = Math.floor(Math.random() * Math.max(100, retryDelay * 0.15));
    retryTimer = window.setTimeout(() => {
      retryTimer = undefined;
      void open();
    }, retryDelay + jitter);
    retryDelay = Math.min(retryDelay * 2, RETRY_MAX_MS);
  };

  const processEvent = async (event: RealtimeEvent) => {
    if (!Number.isSafeInteger(event.cursor) || event.cursor < 0) return;
    if (event.cursor <= lastCursor) {
      socket?.send(JSON.stringify({ type: "ack", cursor: event.cursor }));
      return;
    }
    await options.onEvent(event);
    lastCursor = event.cursor;
    try {
      localStorage.setItem(cursorKey, String(lastCursor));
    } catch {
      // Replay will start from the earlier cursor when persistence is blocked.
    }
    socket?.send(JSON.stringify({ type: "ack", cursor: lastCursor }));
  };

  const open = async () => {
    if (stopped) return;
    if (!navigator.onLine) {
      options.onStatus("OFFLINE");
      return;
    }
    options.onStatus(retryDelay === 1_000 ? "CONNECTING" : "RECONNECTING");

    try {
      const ticket = await options.api.realtimeTicket(
        options.deviceId,
        options.organizationId,
        lastCursor,
      );
      if (stopped) return;
      const url = new URL(options.api.realtimeUrl("/ws/events"));
      url.searchParams.set("device_id", options.deviceId);
      url.searchParams.set("cursor", String(lastCursor));
      socket = socketFactory(url.toString());
      socket.onopen = () => {
        socket?.send(JSON.stringify({ type: "authenticate", ticket }));
      };
      socket.onmessage = (message) => {
        let value: unknown;
        try {
          value = JSON.parse(String(message.data));
        } catch {
          return;
        }
        if (!value || typeof value !== "object") return;
        const payload = value as Record<string, unknown>;
        if (payload.type === "authenticated") {
          retryDelay = 1_000;
          options.onStatus("CONNECTED");
          return;
        }
        if (typeof payload.event_type === "string") {
          messageChain = messageChain
            .then(() => processEvent(payload as RealtimeEvent))
            .catch(() => {
              socket?.close();
            });
        }
      };
      socket.onerror = () => socket?.close();
      socket.onclose = () => {
        if (!stopped) scheduleRetry("RECONNECTING");
      };
    } catch {
      scheduleRetry("RECONNECTING");
    }
  };

  const handleOffline = () => options.onStatus("OFFLINE");
  const handleOnline = () => {
    if (retryTimer !== undefined) {
      window.clearTimeout(retryTimer);
      retryTimer = undefined;
    }
    retryDelay = 1_000;
    void open();
  };
  window.addEventListener("offline", handleOffline);
  window.addEventListener("online", handleOnline);
  void open();

  return () => {
    stopped = true;
    if (retryTimer !== undefined) window.clearTimeout(retryTimer);
    window.removeEventListener("offline", handleOffline);
    window.removeEventListener("online", handleOnline);
    socket?.close();
  };
}

export function cursorForDevice(deviceId: string): number {
  try {
    const stored = Number(
      localStorage.getItem(`commerce-brain.owner.cursor.${deviceId}`),
    );
    return Number.isSafeInteger(stored) && stored > 0 ? stored : 0;
  } catch {
    return 0;
  }
}

export function ownerEventRefreshTargets(eventType: string): string[] {
  if (eventType.startsWith("task.")) return ["summary", "inbox", "shops"];
  if (eventType.startsWith("alert.")) {
    return ["summary", "inbox", "alerts", "shops"];
  }
  if (eventType.startsWith("approval.")) {
    return ["summary", "inbox", "approvals", "shops"];
  }
  if (eventType.startsWith("worker.")) return ["summary", "system-health"];
  if (eventType.startsWith("device.")) return ["system-health"];
  return [];
}
