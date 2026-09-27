import type { components } from "./generated";

export type OwnerSummary = components["schemas"]["OwnerSummaryModel"];
export type OwnerInboxItem = components["schemas"]["OwnerInboxItemModel"];
export type OwnerInboxPage = components["schemas"]["OwnerInboxResponseModel"];
export type OwnerAlert = components["schemas"]["OwnerAlertModel"];
export type OwnerAlertPage = components["schemas"]["OwnerAlertPageModel"];
export type OwnerApproval = components["schemas"]["OwnerApprovalModel"];
export type OwnerApprovalPage = components["schemas"]["OwnerApprovalPageModel"];
export type OwnerShop = components["schemas"]["OwnerShopModel"];
export type OwnerShopPage = components["schemas"]["OwnerShopPageModel"];
export type OwnerShopDetail = components["schemas"]["OwnerShopDetailModel"];
export type OwnerSystemHealth = components["schemas"]["OwnerSystemHealthModel"];
export type OwnerLiveStatus = components["schemas"]["OwnerLiveStatusModel"];
export type OwnerCategory = OwnerInboxItem["owner_category"];
export type Freshness = OwnerInboxItem["freshness"];
export type Scope = components["schemas"]["ScopeModel"];

export interface PublicHealth {
  status: "ok" | "degraded" | string;
  dependencies?: {
    redis?: "ok" | "degraded" | string;
    postgresql_or_local_adapter?: "ok" | "unhealthy" | string;
  };
}

export type InboxFilter =
  | "ALL"
  | "NEED_DECISION"
  | "NEED_APPROVAL"
  | "NEED_AWARENESS";

export type AppPage = "overview" | "inbox" | "shops" | "system";
