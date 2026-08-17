// Mock data + domain constants ported verbatim from the design prototype
// (Local Data Workspace.dc.html). These stand in for real DuckDB / Databricks
// results until the Rust core lands.

export type Row = {
  customer_id: number;
  company: string;
  country: string;
  email: string;
  updated_at: string;
  revenue: number;
  cost: number;
  [k: string]: string | number | undefined;
};

export const RAW_ROWS: Row[] = [
  { customer_id: 83728, company: "ACME", country: "NL", email: "contact@acme.com", updated_at: "2026-01-05", revenue: 125000, cost: 90000 },
  { customer_id: 83729, company: "Foo BV", country: "Netherland", email: "info@foobv.nl", updated_at: "2026-02-11", revenue: 42000, cost: 30000 },
  { customer_id: 83729, company: "Foo BV", country: "Netherland", email: "info@foobv.nl", updated_at: "2025-11-02", revenue: 42000, cost: 30000 },
  { customer_id: 83730, company: "Bar GmbH", country: "DE", email: "kontakt@bargmbh.de", updated_at: "2026-01-18", revenue: 91000, cost: 70000 },
  { customer_id: 83731, company: "Nederland Foods", country: "Nederland", email: "sales@nlfoods.nl", updated_at: "2026-03-01", revenue: 88000, cost: 61000 },
  { customer_id: 83732, company: "Klant Groep", country: "NL", email: "hello@klantgroep.nl", updated_at: "2026-01-20", revenue: 15000, cost: 9000 },
  { customer_id: 83733, company: "De Boer BV", country: "Netherlands", email: "info@deboer.nl", updated_at: "2026-02-02", revenue: 30500, cost: 21000 },
  { customer_id: 83734, company: "Tulip Trading", country: "NL", email: "", updated_at: "2025-12-15", revenue: 76000, cost: 50000 },
  { customer_id: 83735, company: "Windmill Co", country: "Netherland", email: "finance@windmill.nl", updated_at: "2026-01-30", revenue: 54000, cost: 39500 },
  { customer_id: 83736, company: "Amstel Logistics", country: "Nederland", email: "ops@amstellogistics.nl", updated_at: "2026-02-18", revenue: 61000, cost: 44000 },
];

export const BASE_COLS = ["customer_id", "company", "country", "email", "updated_at", "revenue"];

export const OPERATORS = ["equals", "not equals", "greater than", "less than", "before", "after", "contains", "in list", "is null"];

// Operators offered in the working-set checkout filter builder. Strings match
// backend FilterOp.parse (dbsql.py). Ops that take no value: "is null", "is not null".
export const FILTER_OPERATORS = [
  "equals", "not equals", "greater than", "less than", "before", "after", "contains", "in list", "is null", "is not null",
];
export const VALUELESS_OPS = new Set(["is null", "is not null"]);

export const STEP_LABELS: Record<string, string> = {
  source: "Source",
  filter: "Filter",
  select_columns: "Select Columns",
  rename: "Rename Column",
  formula: "Formula",
  join_file: "Join File",
  deduplicate: "Deduplicate",
  replace: "Replace Values",
  validate: "Validate",
  manual_review: "Manual Review",
};

export const MODAL_FOR_TYPE: Record<string, string> = {
  filter: "filter",
  join_file: "join",
  deduplicate: "dedupe",
  replace: "replace",
  validate: "validate",
};

export const ACCT_MGRS = ["J. van Dijk", "M. Bakker", "S. de Vries"];

export const TABLE_SCHEMAS: Record<string, string[]> = {
  "main.crm.customers": ["customer_id", "company", "country", "email", "updated_at", "revenue"],
  "main.crm.customers_enriched": ["customer_id", "company", "country", "email", "updated_at", "revenue", "region", "account_manager", "margin"],
};

export const ADD_STEP_ORDER = [
  "filter", "select_columns", "rename", "formula", "join_file",
  "deduplicate", "replace", "validate", "manual_review",
];
