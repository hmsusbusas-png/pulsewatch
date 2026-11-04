import type { components } from "./schema";

type Schemas = components["schemas"];

export type Site = Schemas["SiteResponse"];
export type SiteCreate = Schemas["SiteCreateRequest"];
export type SiteUpdate = Schemas["SiteUpdateRequest"];
export type SiteStatus = Schemas["SiteStatus"];
export type MonitorType = Schemas["SiteCreateRequest"]["monitor_type"];
export type Assertion = Schemas["Assertion"];
export type Check = Schemas["CheckResponse"];
export type Stats = Schemas["SiteStats"];
export type StatsPoint = Schemas["StatsPoint"];
export type Span = Schemas["Span"];
export type Timings = Schemas["TimingBreakdown"];
export type Slo = Schemas["SloReport"];
export type SlaReport = Schemas["SlaReport"];
export type SiteDays = Schemas["SiteDays"];
export type DayUptime = Schemas["DayUptime"];
export type RegionState = Schemas["RegionState"];
export type Region = Schemas["RegionResponse"];
export type Incident = Schemas["IncidentResponse"];
export type IncidentListItem = Schemas["IncidentListItem"];
export type IncidentDetail = Schemas["IncidentDetail"];
export type TimelineEntry = Schemas["TimelineEntry"];
export type Diagnostic = Schemas["DiagnosticResponse"];
export type IncidentNote = Schemas["IncidentNoteResponse"];
export type Dashboard = Schemas["DashboardSummary"];
export type User = Schemas["UserResponse"];
export type Channel = Schemas["ChannelResponse"];
export type ChannelCreate = Schemas["ChannelCreate"];
export type Delivery = Schemas["DeliveryResponse"];
export type Rule = Schemas["RuleResponse"];
export type Template = Schemas["TemplateResponse"];
export type StatusPage = Schemas["PageResponse"];
export type PageCreate = Schemas["PageCreate"];
export type Announcement = Schemas["AnnouncementResponse"];
export type PublicPage = Schemas["PublicPageResponse"];
export type PublicSite = Schemas["PublicPageSite"];
export type Plan = Schemas["PlanResponse"];
export type Subscription = Schemas["SubscriptionResponse"];
export type Payment = Schemas["PaymentResponse"];
export type Session = Schemas["SessionResponse"];
export type AuditEntry = Schemas["AuditEntry"];
export type ApiKey = Schemas["KeyResponse"];
export type ApiKeyCreated = Schemas["KeyCreated"];
export type AdminStats = Schemas["AdminStats"];
export type AdminUser = Schemas["AdminUserRow"];
export type ImportReport = Schemas["ImportReport"];
export type BulkResult = Schemas["BulkResult"];
export type BulkAction = Schemas["BulkRequest"]["action"];
export type AssertionPreview = Schemas["AssertionPreview"];
export type TelegramStatus = Schemas["TelegramStatus"];
export type TelegramLink = Schemas["LinkResponse"];
export type CheckNow = Schemas["CheckNowResponse"];
export type StatsRange = Schemas["SiteStats"]["range"];

export const RANGES: readonly StatsRange[] = ["24h", "7d", "30d", "90d"];

export function isRange(value: string | null): value is StatsRange {
  return value !== null && (RANGES as readonly string[]).includes(value);
}
