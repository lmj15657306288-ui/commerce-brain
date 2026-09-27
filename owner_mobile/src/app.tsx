import {
  Activity,
  AlertTriangle,
  Bell,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Clock3,
  Cloud,
  Inbox as InboxIcon,
  LayoutDashboard,
  LoaderCircle,
  LockKeyhole,
  LogOut,
  Menu,
  RefreshCw,
  Server,
  ShieldCheck,
  Store,
  Wifi,
  WifiOff,
  X,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, ControlPlaneClient } from "./api/client";
import {
  connectOwnerEvents,
  ownerEventRefreshTargets,
  type RealtimeStatus,
} from "./api/realtime";
import type {
  AppPage,
  InboxFilter,
  OwnerAlert,
  OwnerApproval,
  OwnerInboxItem,
  OwnerLiveStatus,
  OwnerShop,
  OwnerSummary,
  OwnerSystemHealth,
  PublicHealth,
} from "./api/types";

const client = new ControlPlaneClient();
const SNAPSHOT_KEY = "commerce-brain.owner.snapshot";

type ViewKey = AppPage | "alerts" | "approvals";
type RefreshKey =
  | "summary"
  | "inbox"
  | "alerts"
  | "approvals"
  | "shops"
  | "system-health"
  | "live";

interface OwnerData {
  summary: OwnerSummary | null;
  inbox: OwnerInboxItem[];
  alerts: OwnerAlert[];
  approvals: OwnerApproval[];
  shops: OwnerShop[];
  system: OwnerSystemHealth | null;
  live: OwnerLiveStatus | null;
  publicHealth: PublicHealth | null;
  generatedAt: string | null;
}

interface JwtHints {
  organizationId: string;
}

const emptyData: OwnerData = {
  summary: null,
  inbox: [],
  alerts: [],
  approvals: [],
  shops: [],
  system: null,
  live: null,
  publicHealth: null,
  generatedAt: null,
};

function storedOrganizationId(): string {
  try {
    const token = sessionStorage.getItem("commerce-brain.owner.session");
    return token ? decodeJwtHints(token).organizationId : "";
  } catch {
    return "";
  }
}

function readSnapshot(): OwnerData | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(SNAPSHOT_KEY) || "null");
    return value && typeof value === "object" ? (value as OwnerData) : null;
  } catch {
    return null;
  }
}

function saveSnapshot(data: OwnerData): void {
  try {
    sessionStorage.setItem(
      SNAPSHOT_KEY,
      JSON.stringify({
        ...data,
        generatedAt: new Date().toISOString(),
      }),
    );
  } catch {
    // Read-only snapshots are best effort in restricted browsers.
  }
}

function decodeJwtHints(token: string): JwtHints {
  try {
    const payload = token.split(".")[1];
    const decoded = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/")));
    return { organizationId: typeof decoded.organization_id === "string" ? decoded.organization_id : "" };
  } catch {
    return { organizationId: "" };
  }
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function statusLabel(value: string): string {
  const labels: Record<string, string> = {
    HEALTHY: "健康",
    DEGRADED: "降级",
    OFFLINE: "离线",
    UNKNOWN: "未知",
    ONLINE: "在线",
    FRESH: "新鲜",
    DELAYED: "延迟",
    STALE: "过期",
    NEED_DECISION: "需要决定",
    NEED_APPROVAL: "需要审批",
    NEED_AWARENESS: "需要知晓",
    PENDING: "待处理",
    ACKNOWLEDGED: "已确认",
    RESOLVED: "已解决",
    APPROVED: "已批准",
    REJECTED: "已拒绝",
    REVISION_REQUESTED: "需修改",
    OPEN: "开放",
    NOT_CONNECTED: "未接入",
  };
  return labels[value] || value.replaceAll("_", " ");
}

function priorityLabel(priority: string | number): string {
  if (typeof priority === "number") return priority >= 75 ? "高优先级" : "普通";
  return priority;
}

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.userMessage : "加载失败，请稍后重试。";
}

export function App() {
  const [token, setToken] = useState(() => (client.hasSession() ? "stored" : ""));
  const [organizationId, setOrganizationId] = useState(storedOrganizationId);
  const [page, setPage] = useState<ViewKey>("overview");
  const [inboxFilter, setInboxFilter] = useState<InboxFilter>("ALL");
  const [data, setData] = useState<OwnerData>(() => readSnapshot() || emptyData);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [sessionExpired, setSessionExpired] = useState(false);
  const [realtimeStatus, setRealtimeStatus] = useState<RealtimeStatus>("UNAVAILABLE");
  const [deviceId, setDeviceId] = useState("");
  const [selectedItem, setSelectedItem] = useState<OwnerInboxItem | null>(null);
  const [selectedAlert, setSelectedAlert] = useState<OwnerAlert | null>(null);
  const [selectedApproval, setSelectedApproval] = useState<OwnerApproval | null>(null);
  const [selectedShop, setSelectedShop] = useState<OwnerShop | null>(null);
  const [online, setOnline] = useState(() => navigator.onLine);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionMessage, setActionMessage] = useState("");
  const stopRealtime = useRef<(() => void) | null>(null);
  const sessionLoadInFlight = useRef(false);

  const refresh = useCallback(
    async (targets?: RefreshKey[], options: { initial?: boolean } = {}) => {
      if (!client.hasSession()) return;
      const initial = options.initial ?? false;
      if (initial) setLoading(true);
      else setRefreshing(true);
      setError("");
      try {
        const wanted = new Set(
          targets || ["summary", "inbox", "alerts", "approvals", "shops", "system-health", "live"],
        );
        const next = { ...data };
        const requests: Promise<void>[] = [];
        if (wanted.has("summary")) requests.push(client.summary().then((value) => { next.summary = value; }));
        if (wanted.has("inbox")) requests.push(client.inbox().then((value) => { next.inbox = value.items; }));
        if (wanted.has("alerts")) requests.push(client.alerts().then((value) => { next.alerts = value.items; }));
        if (wanted.has("approvals")) requests.push(client.approvals().then((value) => { next.approvals = value.items; }));
        if (wanted.has("shops")) requests.push(client.shops().then((value) => { next.shops = value.items; }));
        if (wanted.has("system-health")) requests.push(client.systemHealth().then((value) => { next.system = value; }));
        if (wanted.has("live")) requests.push(client.liveStatus().then((value) => { next.live = value; }));
        if (initial || wanted.has("summary")) {
          requests.push(client.publicHealth().then((value) => { next.publicHealth = value; }));
        }
        await Promise.all(requests);
        next.generatedAt = new Date().toISOString();
        setData(next);
        saveSnapshot(next);
      } catch (caught) {
        if (!(caught instanceof ApiError && caught.status === 401)) setError(errorText(caught));
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [data],
  );

  const startRealtime = useCallback(
    (currentDevice: string, currentOrganization: string) => {
      stopRealtime.current?.();
      stopRealtime.current = connectOwnerEvents({
        api: client,
        deviceId: currentDevice,
        organizationId: currentOrganization,
        initialCursor: 0,
        onStatus: setRealtimeStatus,
        onEvent: async (event) => {
          const targets = ownerEventRefreshTargets(event.event_type) as RefreshKey[];
          if (targets.length) await refresh(targets);
        },
      });
    },
    [refresh],
  );

  const loadSession = useCallback(
    async (providedOrganization?: string) => {
      if (sessionLoadInFlight.current) return;
      const org = providedOrganization || organizationId.trim();
      if (!org) {
        setError("请输入 JWT 中的 organization_id。");
        return;
      }
      sessionLoadInFlight.current = true;
      setSessionExpired(false);
      setLoading(true);
      setError("");
      try {
        const currentDevice = await client.openMobileSession(org);
        setDeviceId(currentDevice);
        setOrganizationId(org);
        await refresh(undefined, { initial: true });
        startRealtime(currentDevice, org);
      } catch (caught) {
        setError(errorText(caught));
        setLoading(false);
      } finally {
        sessionLoadInFlight.current = false;
      }
    },
    [organizationId, refresh, startRealtime],
  );

  useEffect(() => {
    client.setSessionExpiredHandler(() => {
      setSessionExpired(true);
      stopRealtime.current?.();
    });
    const onOnline = () => setOnline(true);
    const onOffline = () => {
      setOnline(false);
      setRealtimeStatus("OFFLINE");
    };
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      stopRealtime.current?.();
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, []);

  useEffect(() => {
    if (token !== "stored" || !organizationId || deviceId || sessionLoadInFlight.current) return;
    void loadSession(organizationId);
  }, [deviceId, loadSession, organizationId, token]);

  const signIn = (value: string, org: string) => {
    client.setSession(value);
    setToken("stored");
    setOrganizationId(org || decodeJwtHints(value).organizationId);
    void loadSession(org || decodeJwtHints(value).organizationId);
  };

  const signOut = () => {
    stopRealtime.current?.();
    client.clearSession();
    setToken("");
    setDeviceId("");
    setData(emptyData);
    setPage("overview");
  };

  const doAction = async (action: () => Promise<unknown>, success: string) => {
    if (!online || !deviceId) return;
    setActionBusy(true);
    setActionMessage("");
    try {
      await action();
      setActionMessage(success);
      setSelectedAlert(null);
      setSelectedApproval(null);
      await refresh(["summary", "inbox", "alerts", "approvals", "shops"]);
    } catch (caught) {
      setActionMessage(errorText(caught));
    } finally {
      setActionBusy(false);
    }
  };

  if (!client.hasSession()) {
    return (
      <div className="auth-shell">
        <div className="auth-panel">
          <div className="brand-mark"><ShieldCheck size={22} /></div>
          <p className="eyebrow">COMMERCE BRAIN</p>
          <h1>Owner Cockpit</h1>
          <p className="muted">生产 Control Plane 的只读状态、异常与审批入口。</p>
          <AuthForm onSubmit={signIn} error={error} />
          <p className="auth-note"><LockKeyhole size={14} /> 仅接受短期 Owner JWT，不会保存到源码。</p>
        </div>
      </div>
    );
  }

  const title = page === "overview" ? "总览" : page === "inbox" ? "待办" : page === "shops" ? "店铺" : page === "system" ? "系统" : page === "alerts" ? "异常" : "审批";
  const offlineSnapshot = !online || realtimeStatus === "OFFLINE";

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar-left">
          <button className="icon-button menu-button" aria-label="打开导航" onClick={() => setMobileMenuOpen(true)}><Menu size={20} /></button>
          <div className="brand-lockup"><span className="brand-mark small"><ShieldCheck size={18} /></span><span>Owner Cockpit</span></div>
        </div>
        <div className="topbar-actions">
          <ConnectionPill online={online} realtimeStatus={realtimeStatus} />
          <button className="icon-button" aria-label="刷新数据" onClick={() => void refresh()} disabled={refreshing}><RefreshCw className={refreshing ? "spin" : ""} size={18} /></button>
          <button className="icon-button" aria-label="退出" onClick={signOut}><LogOut size={18} /></button>
        </div>
      </header>

      {mobileMenuOpen && <MobileMenu page={page} onNavigate={(next) => { setPage(next); setMobileMenuOpen(false); }} onClose={() => setMobileMenuOpen(false)} />}

      <main className="main-content">
        {offlineSnapshot && <div className="offline-banner"><WifiOff size={16} /> 当前离线，显示最近一次快照。审批和异常操作已禁用。</div>}
        {sessionExpired && <div className="error-banner"><LockKeyhole size={16} /> Session expired，请重新输入 Owner JWT。<button onClick={signOut}>重新认证</button></div>}
        {error && <div className="error-banner"><CircleAlert size={16} /> {error}<button onClick={() => setError("")} aria-label="关闭错误"><X size={16} /></button></div>}
        {actionMessage && <div className="notice-banner"><CheckCircle2 size={16} /> {actionMessage}</div>}

        <div className="page-heading">
          <div><p className="eyebrow">OWNER VIEW</p><h1>{title}</h1></div>
          <span className="freshness-text">{data.generatedAt ? `更新于 ${formatDate(data.generatedAt)}` : "等待数据"}</span>
        </div>

        {loading && <LoadingState />}
        {!loading && page === "overview" && <Overview data={data} onNavigate={setPage} onOpenItem={setSelectedItem} onOpenAlert={setSelectedAlert} onOpenApproval={setSelectedApproval} onOpenShop={setSelectedShop} />}
        {!loading && page === "inbox" && <InboxView items={data.inbox} filter={inboxFilter} onFilter={setInboxFilter} onOpenItem={setSelectedItem} />}
        {!loading && page === "alerts" && <AlertsView alerts={data.alerts} onOpen={setSelectedAlert} onAction={(alert) => void doAction(() => client.acknowledgeAlert(alert.alert_id, crypto.randomUUID()), "异常已确认。")} online={online} />}
        {!loading && page === "approvals" && <ApprovalsView approvals={data.approvals} onOpen={setSelectedApproval} />}
        {!loading && page === "shops" && <ShopsView shops={data.shops} onOpen={setSelectedShop} />}
        {!loading && page === "system" && <SystemView system={data.system} publicHealth={data.publicHealth} realtimeStatus={realtimeStatus} live={data.live} />}
      </main>

      <BottomNav page={page} onNavigate={setPage} summary={data.summary} />
      {selectedItem && <InboxDetail item={selectedItem} onClose={() => setSelectedItem(null)} />}
      {selectedAlert && <AlertDetail alert={selectedAlert} onClose={() => setSelectedAlert(null)} onAcknowledge={() => void doAction(() => client.acknowledgeAlert(selectedAlert.alert_id, crypto.randomUUID()), "异常已确认。")} online={online} busy={actionBusy} />}
      {selectedApproval && <ApprovalDetail approval={selectedApproval} onClose={() => setSelectedApproval(null)} onDecision={(decision) => void doAction(() => client.decideApproval(selectedApproval.approval_id, decision, crypto.randomUUID()), decision === "approve" ? "提案已批准，未执行平台操作。" : decision === "reject" ? "提案已拒绝。" : "已要求修改提案。")} online={online} busy={actionBusy} />}
      {selectedShop && <ShopDetail shop={selectedShop} onClose={() => setSelectedShop(null)} />}
    </div>
  );
}

function AuthForm({ onSubmit, error }: { onSubmit: (token: string, organization: string) => void; error: string }) {
  const [value, setValue] = useState("");
  const [organization, setOrganization] = useState("");
  const [show, setShow] = useState(false);
  return (
    <form className="auth-form" onSubmit={(event) => { event.preventDefault(); onSubmit(value, organization); }}>
      <label>Owner JWT<input required value={value} onChange={(event) => setValue(event.target.value)} type={show ? "text" : "password"} placeholder="粘贴短期 Bearer JWT" autoComplete="off" /></label>
      <label>Organization ID<input required value={organization} onChange={(event) => setOrganization(event.target.value)} placeholder="org_..." /></label>
      <label className="checkbox-row"><input type="checkbox" checked={show} onChange={(event) => setShow(event.target.checked)} /> 显示 token</label>
      <button className="primary-button" type="submit">连接 Control Plane <ChevronRight size={17} /></button>
      {error && <p className="form-error">{error}</p>}
    </form>
  );
}

function ConnectionPill({ online, realtimeStatus }: { online: boolean; realtimeStatus: RealtimeStatus }) {
  const label = !online ? "离线" : realtimeStatus === "CONNECTED" ? "实时已连接" : realtimeStatus === "RECONNECTING" ? "正在重连" : "实时待连接";
  return <span className={`connection-pill ${!online || realtimeStatus === "RECONNECTING" ? "warn" : "ok"}`}><span className="status-dot" />{label}</span>;
}

function LoadingState() {
  return <div className="loading-state"><LoaderCircle className="spin" size={26} /><span>正在读取共享状态…</span></div>;
}

function Overview({ data, onNavigate, onOpenItem, onOpenAlert, onOpenApproval, onOpenShop }: { data: OwnerData; onNavigate: (page: ViewKey) => void; onOpenItem: (item: OwnerInboxItem) => void; onOpenAlert: (alert: OwnerAlert) => void; onOpenApproval: (approval: OwnerApproval) => void; onOpenShop: (shop: OwnerShop) => void }) {
  const summary = data.summary;
  const health = summary?.overall_health || "UNKNOWN";
  return (
    <div className="stack">
      <section className="hero-status">
        <div><span className={`status-icon ${health.toLowerCase()}`}><Activity size={21} /></span><div><p className="eyebrow">全店群状态</p><h2>{statusLabel(health)}</h2></div></div>
        <div className="hero-metrics"><Metric label="需要决定" value={summary?.attention_counts.need_decision} onClick={() => onNavigate("inbox")} /><Metric label="需要审批" value={summary?.attention_counts.need_approval} onClick={() => onNavigate("approvals")} /><Metric label="需要知晓" value={summary?.attention_counts.need_awareness} onClick={() => onNavigate("alerts")} /></div>
      </section>
      <section className="section-block">
        <SectionHeader title="老板关注" action="查看全部" onClick={() => onNavigate("inbox")} />
        {data.inbox.length ? <div className="attention-list">{data.inbox.slice(0, 5).map((item) => <AttentionCard key={item.item_id} item={item} onClick={() => item.item_type === "ALERT" ? onOpenAlert(data.alerts.find((alert) => alert.alert_id === item.item_id) || null!) : item.item_type === "APPROVAL" ? onOpenApproval(data.approvals.find((approval) => approval.approval_id === item.item_id) || null!) : onOpenItem(item)} />)}</div> : <EmptyState icon={<CheckCircle2 />} title="目前没有需要老板处理的事项" detail="No critical alerts or approvals waiting." />}
      </section>
      <section className="section-block">
        <SectionHeader title="店铺状态" action="查看店铺" onClick={() => onNavigate("shops")} />
        {data.shops.length ? <div className="shop-grid">{data.shops.slice(0, 4).map((shop) => <ShopCard key={shop.shop_id} shop={shop} onClick={() => onOpenShop(shop)} />)}</div> : <EmptyState icon={<Store />} title="暂无可见店铺" detail="请检查 Owner scope 授权。" />}
      </section>
      <section className="section-block">
        <SectionHeader title="系统健康" action="打开系统" onClick={() => onNavigate("system")} />
        <SystemHealthStrip system={data.system} />
      </section>
      <section className="section-block">
        <SectionHeader title="直播状态" action="" />
        <div className="live-placeholder"><Activity size={18} /><div><strong>{data.live?.message || "Live intelligence not connected yet"}</strong><span>不会显示未经数据源验证的 GMV、ROI 或在线人数。</span></div><span className="status-badge neutral">未接入</span></div>
      </section>
    </div>
  );
}

function Metric({ label, value, onClick }: { label: string; value: number | null | undefined; onClick: () => void }) {
  return <button className="metric" onClick={onClick}><span>{label}</span><strong>{value ?? "—"}</strong></button>;
}

function InboxView({ items, filter, onFilter, onOpenItem }: { items: OwnerInboxItem[]; filter: InboxFilter; onFilter: (filter: InboxFilter) => void; onOpenItem: (item: OwnerInboxItem) => void }) {
  const groups: InboxFilter[] = ["ALL", "NEED_DECISION", "NEED_APPROVAL", "NEED_AWARENESS"];
  const visible = filter === "ALL" ? items : items.filter((item) => item.owner_category === filter);
  return <div className="stack"><div className="segmented">{groups.map((group) => <button key={group} className={filter === group ? "active" : ""} onClick={() => onFilter(group)}>{group === "ALL" ? "全部" : statusLabel(group)}</button>)}</div>{visible.length ? <div className="attention-list">{visible.map((item) => <AttentionCard key={item.item_id} item={item} onClick={() => onOpenItem(item)} />)}</div> : <EmptyState icon={<InboxIcon />} title="暂无待办" detail="No owner attention items waiting." />}</div>;
}

function AlertsView({ alerts, onOpen, onAction, online }: { alerts: OwnerAlert[]; onOpen: (alert: OwnerAlert) => void; onAction: (alert: OwnerAlert) => void; online: boolean }) {
  return <div className="stack">{alerts.length ? alerts.map((alert) => <AlertCard key={alert.alert_id} alert={alert} onOpen={() => onOpen(alert)} onAction={() => onAction(alert)} online={online} />) : <EmptyState icon={<Bell />} title="没有高优先级异常" detail="No critical alerts." />}</div>;
}

function ApprovalsView({ approvals, onOpen }: { approvals: OwnerApproval[]; onOpen: (approval: OwnerApproval) => void }) {
  return <div className="stack">{approvals.length ? approvals.map((approval) => <ApprovalCard key={approval.approval_id} approval={approval} onOpen={() => onOpen(approval)} />) : <EmptyState icon={<CheckCircle2 />} title="没有待审批提案" detail="No approvals waiting." />}</div>;
}

function ShopsView({ shops, onOpen }: { shops: OwnerShop[]; onOpen: (shop: OwnerShop) => void }) {
  return <div className="shop-grid page-grid">{shops.length ? shops.map((shop) => <ShopCard key={shop.shop_id} shop={shop} onClick={() => onOpen(shop)} />) : <EmptyState icon={<Store />} title="暂无店铺" detail="No authorized shops available." />}</div>;
}

function SystemView({ system, publicHealth, realtimeStatus, live }: { system: OwnerSystemHealth | null; publicHealth: PublicHealth | null; realtimeStatus: RealtimeStatus; live: OwnerLiveStatus | null }) {
  const cloudStatus = system?.cloud_status || (publicHealth?.status === "ok" ? "HEALTHY" : "UNKNOWN");
  return <div className="stack"><div className="health-grid"><HealthCard icon={<Cloud />} title="Cloud Control Plane" value={statusLabel(cloudStatus)} tone={cloudStatus} detail={publicHealth?.status === "ok" ? "核心共享状态可读" : "等待健康检查"} /><HealthCard icon={<Server />} title="Mac Brain Worker" value={statusLabel(system?.brain_worker_status || "UNKNOWN")} tone={system?.brain_worker_status || "UNKNOWN"} detail={system?.workers?.[0] ? `${system.workers[0].worker_id} · ${formatDate(system.workers[0].last_seen_at)}` : "暂无 Worker 状态"} /><HealthCard icon={<Wifi />} title="实时通信" value={realtimeStatus === "CONNECTED" ? "已连接" : realtimeStatus === "RECONNECTING" ? "正在重连" : "未连接"} tone={realtimeStatus === "CONNECTED" ? "HEALTHY" : "DEGRADED"} detail="WebSocket 断线会通过 Event Store replay 恢复" /><HealthCard icon={<Clock3 />} title="数据新鲜度" value={statusLabel(system?.data_freshness || "UNKNOWN")} tone={system?.data_freshness || "UNKNOWN"} detail={system?.last_updated ? `最近检查 ${formatDate(system.last_updated)}` : "等待数据"} /></div><div className="panel quiet-panel"><div className="panel-heading"><h3>当前边界</h3></div><p>Cloud online 不等于 Brain Worker online。Worker 离线时，历史 Task、Alert、Approval 和 Event 仍可读取。</p><p>批准提案不代表平台操作已经执行。真实平台写操作在本阶段保持关闭。</p><div className="live-placeholder"><Activity size={18} /><div><strong>{live?.message || "Live intelligence not connected yet"}</strong><span>Live 数据等待后续数据平面接入。</span></div></div></div></div>;
}

function HealthCard({ icon, title, value, tone, detail }: { icon: React.ReactNode; title: string; value: string; tone: string; detail: string }) {
  return <div className="health-card"><span className={`health-icon ${tone.toLowerCase()}`}>{icon}</span><div><span className="card-label">{title}</span><strong>{value}</strong><small>{detail}</small></div></div>;
}

function SystemHealthStrip({ system }: { system: OwnerSystemHealth | null }) {
  return <div className="system-strip"><div><span className={`status-dot large ${(system?.cloud_status || "unknown").toLowerCase()}`} /><strong>Cloud {statusLabel(system?.cloud_status || "UNKNOWN")}</strong></div><div><span className={`status-dot large ${(system?.brain_worker_status || "unknown").toLowerCase()}`} /><strong>Worker {statusLabel(system?.brain_worker_status || "UNKNOWN")}</strong></div><div><span className={`status-dot large ${(system?.data_freshness || "unknown").toLowerCase()}`} /><strong>数据 {statusLabel(system?.data_freshness || "UNKNOWN")}</strong></div></div>;
}

function AttentionCard({ item, onClick }: { item: OwnerInboxItem; onClick: () => void }) {
  return <button className="attention-card" onClick={onClick}><div className="attention-main"><span className={`category-icon ${item.owner_category.toLowerCase()}`}><AttentionIcon type={item.owner_category} /></span><div><div className="card-overline">{statusLabel(item.owner_category)} · {priorityLabel(item.priority)}</div><strong>{item.title}</strong><p>{item.summary}</p><span className="card-meta">{item.shop_name || "组织级"} · {statusLabel(item.freshness)} · {formatDate(item.created_at)}</span></div></div><ChevronRight size={18} /></button>;
}

function AttentionIcon({ type }: { type: string }) {
  if (type === "NEED_APPROVAL") return <CheckCircle2 size={17} />;
  if (type === "NEED_AWARENESS") return <Bell size={17} />;
  return <CircleAlert size={17} />;
}

function AlertCard({ alert, onOpen, onAction, online }: { alert: OwnerAlert; onOpen: () => void; onAction: () => void; online: boolean }) {
  return <div className="record-card"><button className="record-body" onClick={onOpen}><div className={`priority-mark ${alert.priority.toLowerCase()}`}>{alert.priority}</div><div><strong>{alert.summary}</strong><p>{alert.recommended_action}</p><span className="card-meta">{alert.shop_name || "组织级"} · {statusLabel(alert.status)} · {formatDate(alert.created_at)}</span></div><ChevronRight size={18} /></button>{alert.status === "OPEN" && <button className="text-button" disabled={!online} onClick={onAction}><Check size={16} />确认异常</button>}</div>;
}

function ApprovalCard({ approval, onOpen }: { approval: OwnerApproval; onOpen: () => void }) {
  return <div className="record-card"><button className="record-body" onClick={onOpen}><div className={`priority-mark ${approval.risk_level.toLowerCase()}`}>{approval.risk_level[0]}</div><div><strong>{approval.title}</strong><p>{approval.reason}</p><span className="card-meta">{approval.shop_name || "组织级"} · {statusLabel(approval.status)} · {formatDate(approval.requested_at)}</span></div><ChevronRight size={18} /></button></div>;
}

function ShopCard({ shop, onClick }: { shop: OwnerShop; onClick: () => void }) {
  return <button className="shop-card" onClick={onClick}><div className="shop-card-top"><span className={`shop-status ${shop.health.toLowerCase()}`}><Store size={17} /></span><ChevronRight size={17} /></div><strong>{shop.shop_name}</strong><span className="card-meta">{statusLabel(shop.health)} · 数据 {statusLabel(shop.freshness)}</span><div className="shop-counts"><span><AlertTriangle size={14} />{shop.high_priority_alerts}</span><span><CheckCircle2 size={14} />{shop.pending_approvals}</span><span><InboxIcon size={14} />{shop.important_tasks}</span></div><span className="muted small-text">直播未接入</span></button>;
}

function SectionHeader({ title, action, onClick }: { title: string; action: string; onClick?: () => void }) {
  return <div className="section-header"><h2>{title}</h2>{action && <button className="link-button" onClick={onClick}>{action}<ChevronRight size={15} /></button>}</div>;
}

function EmptyState({ icon, title, detail }: { icon: React.ReactNode; title: string; detail: string }) {
  return <div className="empty-state"><span>{icon}</span><strong>{title}</strong><small>{detail}</small></div>;
}

function MobileMenu({ page, onNavigate, onClose }: { page: ViewKey; onNavigate: (page: ViewKey) => void; onClose: () => void }) {
  return <div className="menu-overlay" onClick={onClose}><aside className="side-menu" onClick={(event) => event.stopPropagation()}><div className="side-menu-head"><strong>Owner Cockpit</strong><button className="icon-button" onClick={onClose} aria-label="关闭导航"><X size={18} /></button></div>{[["overview", "首页", LayoutDashboard], ["inbox", "待办", InboxIcon], ["alerts", "异常", Bell], ["approvals", "审批", CheckCircle2], ["shops", "店铺", Store], ["system", "系统", Server]].map(([key, label, Icon]) => <button key={String(key)} className={page === key ? "menu-link active" : "menu-link"} onClick={() => onNavigate(key as ViewKey)}><Icon size={18} />{String(label)}</button>)}</aside></div>;
}

function BottomNav({ page, onNavigate, summary }: { page: ViewKey; onNavigate: (page: ViewKey) => void; summary: OwnerSummary | null }) {
  return <nav className="bottom-nav">{[["overview", "首页", LayoutDashboard], ["inbox", "待办", InboxIcon], ["shops", "店铺", Store], ["system", "系统", Server]].map(([key, label, Icon]) => <button key={String(key)} className={page === key ? "active" : ""} onClick={() => onNavigate(key as AppPage)}><span className="nav-icon"><Icon size={19} />{key === "inbox" && (summary?.attention_counts.need_decision || 0) + (summary?.attention_counts.need_approval || 0) + (summary?.attention_counts.need_awareness || 0) > 0 && <b>!</b>}</span><span>{String(label)}</span></button>)}</nav>;
}

function InboxDetail({ item, onClose }: { item: OwnerInboxItem; onClose: () => void }) {
  return <Modal title={item.title} onClose={onClose}><div className="detail-stack"><div className="detail-status"><span className={`category-icon ${item.owner_category.toLowerCase()}`}><AttentionIcon type={item.owner_category} /></span><div><strong>{statusLabel(item.owner_category)}</strong><span>{statusLabel(item.status)} · {statusLabel(item.freshness)}</span></div></div><DetailRow label="发生了什么" value={item.summary} /><DetailRow label="为什么重要" value={item.why_it_matters} /><DetailRow label="建议下一步" value={item.recommended_action} /><DetailRow label="店铺" value={item.shop_name || "组织级"} /><DetailRow label="创建时间" value={formatDate(item.created_at)} />{!item.business_impact && <div className="impact-note">Impact not quantified yet.</div>}</div></Modal>;
}

function AlertDetail({ alert, onClose, onAcknowledge, online, busy }: { alert: OwnerAlert; onClose: () => void; onAcknowledge: () => void; online: boolean; busy: boolean }) {
  return <Modal title="异常详情" onClose={onClose}><div className="detail-stack"><div className="detail-status"><div className={`priority-mark ${alert.priority.toLowerCase()}`}>{alert.priority}</div><div><strong>{alert.summary}</strong><span>{statusLabel(alert.status)} · {statusLabel(alert.freshness)}</span></div></div><DetailRow label="原因" value={alert.reason_code} /><DetailRow label="建议动作" value={alert.recommended_action} /><DetailRow label="店铺" value={alert.shop_name || "组织级"} /><DetailRow label="发生时间" value={formatDate(alert.created_at)} />{!alert.business_impact && <div className="impact-note">Impact not quantified yet.</div>}{alert.status === "OPEN" && <button className="primary-button full-width" disabled={!online || busy} onClick={onAcknowledge}><Check size={17} />确认异常</button>}</div></Modal>;
}

function ApprovalDetail({ approval, onClose, onDecision, online, busy }: { approval: OwnerApproval; onClose: () => void; onDecision: (decision: "approve" | "reject" | "request-revision") => void; online: boolean; busy: boolean }) {
  const [confirm, setConfirm] = useState<"approve" | "reject" | "request-revision" | null>(null);
  return <Modal title="审批详情" onClose={onClose}><div className="detail-stack"><div className="detail-status"><div className={`priority-mark ${approval.risk_level.toLowerCase()}`}>{approval.risk_level[0]}</div><div><strong>{approval.title}</strong><span>{statusLabel(approval.status)} · 风险 {approval.risk_level}</span></div></div><DetailRow label="提案" value={approval.proposal_id} /><DetailRow label="原因" value={approval.reason} /><DetailRow label="店铺" value={approval.shop_name || "组织级"} /><DetailRow label="请求时间" value={formatDate(approval.requested_at)} /><DetailRow label="到期时间" value={formatDate(approval.expires_at)} /><div className="approval-boundary"><ShieldCheck size={17} /><span>批准的是提案，不代表平台操作已经执行。</span></div>{approval.status === "PENDING" && <>{confirm && <div className="confirm-box"><strong>确认{confirm === "approve" ? "批准该提案" : confirm === "reject" ? "拒绝该提案" : "要求修改该提案"}？</strong><span>操作会在线写入 Control Plane，离线时不会本地暂存。</span><div className="inline-actions"><button className="primary-button" disabled={busy} onClick={() => onDecision(confirm)}>确认</button><button className="secondary-button" onClick={() => setConfirm(null)}>取消</button></div></div>} {!confirm && <div className="inline-actions stacked-mobile"><button className="primary-button" disabled={!online || busy} onClick={() => setConfirm("approve")}><Check size={17} />批准提案</button><button className="secondary-button" disabled={!online || busy} onClick={() => setConfirm("request-revision")}><RefreshCw size={17} />要求修改</button><button className="danger-button" disabled={!online || busy} onClick={() => setConfirm("reject")}><XCircle size={17} />拒绝</button></div>}</>}</div></Modal>;
}

function ShopDetail({ shop, onClose }: { shop: OwnerShop; onClose: () => void }) {
  return <Modal title={shop.shop_name} onClose={onClose}><div className="detail-stack"><div className="detail-status"><span className={`shop-status ${shop.health.toLowerCase()}`}><Store size={18} /></span><div><strong>{statusLabel(shop.health)}</strong><span>数据 {statusLabel(shop.freshness)}</span></div></div><DetailRow label="高优先级异常" value={String(shop.high_priority_alerts)} /><DetailRow label="待审批" value={String(shop.pending_approvals)} /><DetailRow label="重要任务" value={String(shop.important_tasks)} /><DetailRow label="直播" value="Live data not connected yet" /><div className="impact-note">经营数据尚未接入。</div></div></Modal>;
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return <div className="modal-overlay" onClick={onClose}><section className="modal" onClick={(event) => event.stopPropagation()}><div className="modal-head"><h2>{title}</h2><button className="icon-button" aria-label="关闭" onClick={onClose}><X size={19} /></button></div>{children}</section></div>;
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return <div className="detail-row"><span>{label}</span><strong>{value}</strong></div>;
}
