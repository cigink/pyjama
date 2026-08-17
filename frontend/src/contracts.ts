// TypeScript mirror of src-tauri/src/model.rs. Hand-kept in sync — these are the
// wire contracts crossing the Tauri command boundary (IMPLEMENTATION_PLAN §18).

export type AuthUser = {
  workspace_url: string;
  user_subject: string;
  scopes: string[];
};

export type Catalog = { name: string };
export type Schema = { catalog: string; name: string };
export type TableSummary = { full_name: string; name: string; kind: string };
export type ColumnMeta = { name: string; type_name: string; nullable: boolean };
export type TableMetadata = { full_name: string; columns: ColumnMeta[]; row_count: number | null };

export type FilterSpec = { column: string; op: string; value: string };
export type CheckoutSpec = {
  workspace_url: string;
  table: string;
  columns: string[];
  filters: FilterSpec[];
  row_key: string[];
};

export type OperationId = { operation_id: string };

export type WorkspaceSummary = {
  workspace_id: string;
  name: string;
  source_table: string;
  base_version: number;
  pipeline_revision: number;
  row_count: number;
  logical_bytes: number;
};

export type WorkspaceSummaryCard = {
  workspace_id: string;
  name: string;
  source_table: string;
  base_version: number;
  row_count: number;
  logical_bytes: number;
  created_at: string;
};

export type StepSpec = {
  id: string;
  ordinal: number;
  type: string;
  enabled: boolean;
  config: unknown;
};

export type PipelineRevision = { workspace_id: string; pipeline_revision: number };

export type SortSpec = { column: string; direction: "asc" | "desc" };
export type PreviewRequest = {
  workspace_id: string;
  step_id: string;
  offset: number;
  limit: number;
  sort?: SortSpec[];
};
export type PreviewPage = {
  columns: string[];
  rows: unknown[][];
  offset: number;
  total: number | null;
};

export type WarehouseSummary = { id: string; name: string; state: string };

export type AppConfig = {
  workspace_url: string;
  client_id: string;
  warehouse_id: string | null;
  staging_volume: string | null;
  configured: boolean;
};

export type DiffSummary = { added: number; modified: number; deleted: number; unchanged: number };
export type ValidationSummary = { valid_rows: number; invalid_rows: number; blocking: boolean };
export type CommitOptions = { target_table: string; create_new: boolean };
export type WatcherId = { watcher_id: string };

export type Pong = { message: string; echoed: string; operation_id: string };
