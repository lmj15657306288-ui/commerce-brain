# Phase 2B.5-1 Owner Mobile Cockpit PWA

## Product goal

Owner Mobile Cockpit is the first mobile experience surface for Commerce Brain. It
answers, in a short scan:

- Is the shop group healthy?
- What needs an Owner decision, approval, or awareness?
- Which alerts and approvals are waiting?
- Is Cloud and the Mac Brain Worker available?
- How fresh is the shared state?

It is not a mobile copy of the Employee Workbench and it does not create a second
business truth.

## Information architecture

The mobile primary navigation is intentionally small:

- Overview;
- Owner Inbox;
- Shops;
- System Health.

Alerts and Approvals are focused views inside the same surface. Live Status is a
small, explicit `NOT_CONNECTED` placeholder until a real live data source exists.

The Overview prioritizes overall health, the first five owner attention items, shop
status, system health, and live integration status. It never invents GMV, ROI,
profit, inventory, refund, viewer, or live product values.

## Owner read models

The backend adds aggregation-only endpoints:

```text
GET  /owner/summary
GET  /owner/inbox
GET  /owner/alerts
GET  /owner/alerts/{alert_id}
GET  /owner/approvals
GET  /owner/approvals/{approval_id}
GET  /owner/shops
GET  /owner/shops/{shop_id}
GET  /owner/system-health
GET  /owner/live-status
POST /owner/realtime-ticket
```

`OwnerSummaryModel`, `OwnerInboxItemModel`, `OwnerAlertModel`,
`OwnerApprovalModel`, `OwnerShopModel`, `OwnerSystemHealthModel`, and
`OwnerLiveStatusModel` are UI read models. Task, Alert, Approval, Context Registry,
Device, Worker, and Event Store contracts remain the source of truth. The OpenAPI
schema is exported from FastAPI and used to generate `owner_mobile/src/api/generated.ts`.

Inbox classification is deterministic:

- Owner-assigned Task -> `NEED_DECISION`;
- high-priority Alert -> `NEED_AWARENESS`;
- pending Approval -> `NEED_APPROVAL`.

The list is still scope-filtered by the existing Context Registry and capability
checks. A hidden item in the UI is not a security boundary.

## Authentication and device session

The PWA accepts a short-lived production Owner JWT issued by the existing admin
signer. It stores the token only in `sessionStorage`; it is never in source,
Service Worker cache, URL query parameters, or logs. The organization ID is read
from the JWT only as a UI hint and is still verified by the server.

On connect, the PWA opens an active `MOBILE` DeviceSession through
`POST /devices/session`. Owner mutations include that server-validated device ID.
Expired or invalid JWTs produce `Session expired`, not a generic network error.

## Approval and alert boundary

The PWA may acknowledge/resolve authorized alerts and approve, reject, or request
revision on an Approval. These mutations are online-only and use idempotency keys.
`APPROVED` remains an approval state change only. It does not call an executor,
platform API, browser runtime, budget control, pricing, inventory, refund, message,
or live control path.

High-risk actions require an in-app confirmation. Offline mode disables every
mutation button and never queues an approval for later replay.

## PWA and offline policy

The frontend is a React + Vite + TypeScript PWA with:

- installable manifest and icons;
- mobile-first responsive layout;
- safe-area aware bottom navigation;
- static app-shell Service Worker;
- desktop browser fallback.

The Service Worker caches only the shell and static assets. Requests carrying an
Authorization header and all non-GET requests bypass the cache. A last-known
read-only snapshot is kept in `sessionStorage` and visibly marked when offline.

## Realtime and replay

The browser first requests a one-time, origin-bound realtime ticket over HTTPS.
The ticket is consumed in the first WSS frame, so a JWT is not placed in a WebSocket
URL. The client stores its last event cursor per device and reconnects with bounded
exponential backoff.

WebSocket is not the source of truth. Event Store is the recovery source. Events
only invalidate the affected read models:

- Task -> summary, inbox, shops;
- Alert -> summary, inbox, alerts, shops;
- Approval -> summary, inbox, approvals, shops;
- Worker/device -> summary or system health.

On reconnect, cursor replay refreshes the relevant read model. Duplicate cursors
are ignored and acknowledged without re-applying a UI mutation.

## Freshness and System Health

Every important view carries `FRESH`, `DELAYED`, `STALE`, or `UNKNOWN`. Cloud
availability does not imply fresh business data. A Mac Worker outage is shown as
`Cloud Healthy / Brain Worker Offline`; shared Task, Alert, Approval, and Event
history remain readable.

The Owner surface translates infrastructure facts into Owner language:

- `Healthy`;
- `Degraded`;
- `Offline`;
- `Realtime reconnecting`.

Database and Redis stack traces are not shown in normal Owner UI.

## Deployment

The frontend has a production-like image at `owner_mobile/Dockerfile` and a
Compose overlay at `deploy/docker-compose.owner-mobile.yml`. It is intentionally
separate from the Phase 2B Control Plane base compose:

```bash
cd /opt/commerce-brain
docker compose -f deploy/docker-compose.yml \
  -f deploy/docker-compose.owner-mobile.yml \
  --env-file deploy/.env \
  up -d --build owner-mobile reverse-proxy
```

The overlay uses the existing Caddy reverse proxy and adds only:

```text
app.apizz.cc.cd -> owner-mobile:80
```

The Control Plane site remains:

```text
brain.apizz.cc.cd -> control-plane:8000
```

Before activation, add the DNS record manually:

```text
A  app.apizz.cc.cd  39.109.62.227
```

Then set `OWNER_PUBLIC_DOMAIN=app.apizz.cc.cd` and add
`https://app.apizz.cc.cd` to `CORS_ALLOWED_ORIGINS` in the production environment.
Validate DNS and Caddy configuration before reloading. No production deployment
or TLS claim is made while the app hostname is unresolved.

## Non-goals

This phase does not include real commerce data collection, Browser Observer,
Browser Runtime, Platform Map, Live Audio, Profit Engine, Inventory Engine,
native push, native mobile apps, employee workbench, or real platform write
operations. It does not enter Phase 2C or Phase 2B.5-2.

## Phase 2B.5-2 future work

The next mobile iteration should only begin after review. Candidate work is
notification preferences and a controlled notification channel, richer evidence
presentation, and a 24-hour summary backed by real data sources. It must preserve
the same scope, freshness, approval, and no-execution boundaries.
