// Typed frontend ↔ Python backend bridge.
//
// The UI runs inside a pywebview native window (or a browser in dev) and calls
// the FastAPI backend over localhost. Every wrapper mirrors an endpoint in
// backend/pyjama_backend/server.py. Shapes match src/contracts.ts.
//
// In `vite dev` the backend is a separate origin (:8000); in the packaged app
// FastAPI serves the built UI, so the API is same-origin at `/api`.

import type {
  AuthUser,
  Catalog,
  Schema,
  TableSummary,
  TableMetadata,
  CheckoutSpec,
  OperationId,
  WorkspaceSummary,
  StepSpec,
  PipelineRevision,
  PreviewRequest,
  PreviewPage,
  DiffSummary,
  ValidationSummary,
  CommitOptions,
  WatcherId,
  WarehouseSummary,
  WorkspaceSummaryCard,
  AppConfig,
  FilterSpec,
  Pong,
} from "./contracts";

export type BackendStep = { id: string; type: string; config: any; enabled: boolean; input_id?: string | null };

// A standalone, encrypted, locally-materialized dataset — decoupled from any
// workspace. Reusable as a notebook's primary table or a join input across any
// number of workspaces.
export type SourceSummary = {
  source_id: string;
  id: string; // alias of source_id, kept for the join picker
  name: string;
  kind: string; // "uc_table" | "csv" | "xlsx" | "parquet"
  created_at: string;
  refreshed_at: string;
  columns: string[];
  row_count: number;
  logical_bytes: number;
  uc_table: string | null;
  refreshable: boolean;
};

export type AiStatus = {
  installed: boolean;
  verified: boolean;
  smoke_test_passed: boolean;
  model_id: string;
  size_bytes: number;
  runtime_running: boolean;
};

export type AnalysisMeasure = { column: string; aggregation: string; alias?: string | null };
export type AnalysisFilter = { column: string; operator: string; value: string | number | null };
export type AnalysisSortSpec = { column: string; direction: string };
export type AnalysisJoin = { local_source_id: string; join_type: "inner" | "left" | "right" | "full" | "semi" | "anti"; keys: [string, string][] };
export type AnalysisSpec = {
  dimensions: string[];
  measures: AnalysisMeasure[];
  filters: AnalysisFilter[];
  sort: AnalysisSortSpec[];
  join?: AnalysisJoin | null;
  distinct?: boolean;
  having?: AnalysisFilter[];
  limit: number;
};
export type AnalysisResult = {
  columns: string[];
  rows: unknown[][];
  row_count: number;
  generated_sql: string;
  visualization_hint: "kpi" | "bar" | "line" | "table" | "grid";
  recipe?: string;
};

export type RecipeName =
  | "summarize" | "trend" | "top_bottom_n" | "compare_periods" | "running_total"
  | "moving_average" | "contribution" | "duplicates" | "missing_values" | "distribution";

export type AiAskResult = {
  id: string;
  question: string;
  status: "SUCCESS" | "BLOCKED" | "INVALID" | "CANCELED";
  generated_sql: string | null;
  attempt_count: number;
  inference_ms: number;
  execution_ms: number;
  rows_returned: number;
  columns: string[];
  preview_rows: unknown[][];
  error: string | null;
};

const API_BASE = import.meta.env.DEV ? "http://127.0.0.1:8000/api" : "/api";

async function call<T>(cmd: string, args?: Record<string, unknown>, signal?: AbortSignal): Promise<T> {
  const resp = await fetch(`${API_BASE}/${cmd}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args ?? {}),
    signal,
  });
  if (!resp.ok) {
    let detail = `${resp.status}`;
    try {
      const body = await resp.json();
      detail = body?.detail ?? detail;
    } catch {
      /* non-JSON error */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

/** True once the backend answers a ping. Callers use this to pick live vs mock. */
export async function backendReachable(): Promise<boolean> {
  try {
    await call<Pong>("ping", { message: "health" });
    return true;
  } catch {
    return false;
  }
}

// Event stream placeholder. pywebview build will add SSE/websocket for
// checkout/commit progress + auth expiry (Phase 2).
export async function on<T>(_name: string, _handler: (payload: T) => void): Promise<() => void> {
  return () => {};
}

export const api = {
  ping: (message: string) => call<Pong>("ping", { message }),

  authConnect: () => call<AuthUser>("auth_connect"),
  authLogout: () => call<void>("auth_logout"),
  authStatus: () => call<boolean>("auth_status"),
  configGet: () => call<AppConfig>("config_get"),

  catalogList: () => call<Catalog[]>("catalog_list"),
  schemaList: (catalog: string) => call<Schema[]>("schema_list", { catalog }),
  tableList: (catalog: string, schema: string) => call<TableSummary[]>("table_list", { catalog, schema }),
  tableGet: (fullName: string) => call<TableMetadata>("table_get", { fullName }),

  warehouseList: () => call<WarehouseSummary[]>("warehouse_list"),
  warehouseGet: (id: string) => call<WarehouseSummary>("warehouse_get", { id }),
  warehouseStart: (id: string) => call<void>("warehouse_start", { id }),

  // Statement Execution spike (P1.10)
  runSelectSpike: (warehouseId: string, table: string, columns: string[], filters: FilterSpec[]) =>
    call<PreviewPage>("run_select_spike", { warehouseId, table, columns, filters }),

  // Encrypted checkout (Phase 2) — always creates a standalone Source; a
  // Workspace ("notebook") reading it as primary is optional (createWorkspace).
  checkoutStart: (table: string, columns: string[], filters: FilterSpec[], rowKey: string[], createWorkspace = true) =>
    call<{ operation_id: string; workspace_id: string | null; source_id: string }>("checkout_start", { table, columns, filters, rowKey, createWorkspace }),
  checkoutStatus: (operationId: string) =>
    call<{ state: string; workspace_id?: string | null; source_id?: string; completed_chunks: number; total_chunks: number | null; row_count: number; error: string | null }>(
      "checkout_status",
      { operationId }
    ),

  workspaceCreate: (name: string) => call<WorkspaceSummary>("workspace_create", { name }),
  workspaceCreateFromSource: (sourceId: string, name?: string) =>
    call<WorkspaceSummary>("workspace_create_from_source", { sourceId, name: name ?? "" }),
  workspaceOpen: (workspaceId: string) => call<WorkspaceSummary>("workspace_open", { workspaceId }),
  workspaceList: () => call<string[]>("workspace_list"),
  workspaceDelete: (workspaceId: string) => call<{ ok: boolean }>("workspace_delete", { workspaceId }),
  workspaceSummaries: () => call<WorkspaceSummaryCard[]>("workspace_summaries"),
  workspaceSchema: (workspaceId: string) => call<{ columns: { name: string; type: string }[]; total: number }>("workspace_schema", { workspaceId }),

  // Source registry (Sources tab) — decoupled from any workspace, reusable
  // across all notebooks.
  sourceList: () => call<SourceSummary[]>("source_list"),
  sourceDelete: (sourceId: string) => call<{ ok: boolean }>("source_delete", { sourceId }),
  sourceRefresh: (sourceId: string) => call<SourceSummary>("source_refresh", { sourceId }),

  // Windowed preview (Phase 3)
  previewWindow: (workspaceId: string, offset: number, limit: number, sort: { column: string; direction: string }[]) =>
    call<PreviewPage>("preview_query", { workspaceId, offset, limit, sort }),

  // Pipeline (Phase 4)
  previewStep: (
    workspaceId: string,
    steps: BackendStep[],
    stepIndex: number,
    offset: number,
    limit: number,
    sort: { column: string; direction: string }[]
  ) => call<PreviewPage & { output_columns: string[] }>("preview_step", { workspaceId, steps, stepIndex, offset, limit, sort }),
  pipelineCounts: (workspaceId: string, steps: BackendStep[]) =>
    call<{ step_id: string; row_count: number | null; error: string | null }[]>("pipeline_counts", { workspaceId, steps }),
  pipelineSave: (workspaceId: string, steps: BackendStep[]) =>
    call<{ workspace_id: string; pipeline_revision: number }>("pipeline_save", { workspaceId, steps }),
  pipelineGet: (workspaceId: string) => call<{ steps: BackendStep[]; pipeline_revision: number }>("pipeline_get", { workspaceId }),

  // Local files (Phase 5) — imports land in the shared source registry,
  // reusable from any workspace, not scoped to whichever one triggered it.
  localSourceImport: async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const resp = await fetch(`${API_BASE}/local_source_import`, { method: "POST", body: fd });
    if (!resp.ok) {
      let detail = `${resp.status}`;
      try { detail = (await resp.json()).detail ?? detail; } catch { /* */ }
      throw new Error(detail);
    }
    return (await resp.json()) as SourceSummary;
  },
  localSourceList: () => call<SourceSummary[]>("local_source_list"),
  localSourceFromUc: (table: string, columns: string[], filters: FilterSpec[]) =>
    call<SourceSummary>("local_source_from_uc", { table, columns, filters }),
  watchScan: (folder: string) =>
    call<{ name: string; path: string; size: number; format: string; stable: boolean }[]>("watch_scan", { folder }),
  watchImport: (path: string) => call<SourceSummary>("watch_import", { path }),

  pipelineAddStep: (workspaceId: string, step: StepSpec) => call<PipelineRevision>("pipeline_add_step", { workspaceId, step }),
  pipelineUpdateStep: (workspaceId: string, stepId: string, step: StepSpec) =>
    call<PipelineRevision>("pipeline_update_step", { workspaceId, stepId, step }),
  // Row identity + diff + validation (Phase 6)
  rowKeyGet: (workspaceId: string) => call<{ keys: string[]; columns: string[] }>("row_key_get", { workspaceId }),
  rowKeySet: (workspaceId: string, keys: string[]) => call<{ unique: boolean; duplicate: unknown[] | null; error?: string }>("row_key_set", { workspaceId, keys }),
  diffCompute: (workspaceId: string, steps: BackendStep[], keys: string[]) =>
    call<{ added: number; modified: number; deleted: number; unchanged: number; samples: { key: Record<string, unknown>; changes: { column: string; before: unknown; after: unknown }[] }[] }>(
      "diff_compute",
      { workspaceId, steps, keys }
    ),
  validationRun: (workspaceId: string, steps: BackendStep[], rules: any[]) =>
    call<{ total: number; valid: number; invalid: number; blocking: boolean; per_rule: { id: string; invalid: number | null; error: string | null; severity: string }[]; failed_columns: string[]; failed_rows: unknown[][] }>(
      "validation_run",
      { workspaceId, steps, rules }
    ),
  // Commit write-back (Phase 7)
  commitReadiness: (workspaceId: string, steps: BackendStep[], keys: string[]) =>
    call<{ checks: Record<string, boolean>; ready: boolean; keys: string[]; staging_volume: string | null }>("commit_readiness", { workspaceId, steps, keys }),
  commitStart: (workspaceId: string, steps: BackendStep[], keys: string[], targetTable: string, createNew: boolean) =>
    call<{ operation_id: string; change_count: number }>("commit_start", { workspaceId, steps, keys, targetTable, createNew }),
  columnStats: (workspaceId: string, column: string) =>
    call<{
      column: string; type: string; total: number; nulls: number; null_pct: number; distinct: number;
      min: unknown; max: unknown; top_values: { value: unknown; count: number }[];
    }>("column_stats", { workspaceId, column }),
  columnDistinctValues: (workspaceId: string, column: string, limit = 50) =>
    call<{ column: string; total_distinct: number; truncated: boolean; values: { value: unknown; count: number }[] }>(
      "column_distinct_values",
      { workspaceId, column, limit }
    ),
  checkoutEstimate: (warehouseId: string, table: string, filters: FilterSpec[]) =>
    call<{ row_count: number }>("checkout_estimate", { warehouseId, table, columns: [], filters }),
  checkoutSample: (warehouseId: string, table: string, columns: string[], filters: FilterSpec[]) =>
    call<{ columns: string[]; rows: unknown[][] }>("checkout_sample", { warehouseId, table, columns, filters }),
  namespaceCheck: (catalog: string, schema: string) =>
    call<{ catalog_exists: boolean; schema_exists: boolean }>("namespace_check", { catalog, schema }),
  namespaceCreate: (catalog: string, schema: string, createCatalog: boolean, createSchema: boolean) =>
    call<{ ok: boolean }>("namespace_create", { catalog, schema, createCatalog, createSchema }),
  commitStatus: (operationId: string) =>
    call<{ state: string; error: string | null; result: { new_version: number | null; target_table: string; row_count: number; created: boolean } | null; base_version?: number; current_version?: number }>(
      "commit_status",
      { operationId }
    ),
  watcherAdd: (folderPath: string) => call<WatcherId>("watcher_add", { folderPath }),
  watcherRemove: (watcherId: string) => call<void>("watcher_remove", { watcherId }),

  // Explore: AnalysisSpec (Phase 11, P11.7)
  exploreRunAnalysis: (workspaceId: string, steps: BackendStep[], stepIndex: number | null, spec: AnalysisSpec, signal?: AbortSignal) =>
    call<AnalysisResult>("explore_run_analysis", { workspaceId, steps, stepIndex, spec }, signal),
  explorePromoteAnalysis: (workspaceId: string, steps: BackendStep[], stepIndex: number | null, spec: AnalysisSpec, name: string) =>
    call<{ workspace_id: string; step_id: string; pipeline_revision: number; name: string }>("explore_promote_analysis", { workspaceId, steps, stepIndex, spec, name }),

  // Explore: intent recipes (Phase 12, P12.9)
  exploreRunRecipe: (workspaceId: string, steps: BackendStep[], stepIndex: number | null, recipe: RecipeName, params: Record<string, unknown>, signal?: AbortSignal) =>
    call<AnalysisResult>("explore_run_recipe", { workspaceId, steps, stepIndex, recipe, params }, signal),
  explorePromoteRecipe: (workspaceId: string, steps: BackendStep[], stepIndex: number | null, recipe: RecipeName, params: Record<string, unknown>) =>
    call<{ workspace_id: string; step_id: string; pipeline_revision: number; name: string }>("explore_promote_recipe", { workspaceId, steps, stepIndex, recipe, params }),

  // Local NL-to-SQL assistant (Phase 10)
  aiStatus: () => call<AiStatus>("ai_status"),
  aiModelInstall: () => call<{ operation_id: string }>("ai_model_install"),
  aiModelInstallStatus: (operationId: string) =>
    call<{ state: string; bytes: number; total_bytes: number; error: string | null }>("ai_model_install_status", { operationId }),
  aiModelUninstall: () => call<{ ok: boolean }>("ai_model_uninstall"),
  aiAsk: (workspaceId: string, steps: BackendStep[], stepIndex: number | null, question: string) =>
    call<AiAskResult>("ai_ask", { workspaceId, steps, stepIndex, question }),
  aiRunSql: (workspaceId: string, steps: BackendStep[], stepIndex: number | null, sql: string) =>
    call<AiAskResult>("ai_run_sql", { workspaceId, steps, stepIndex, sql }),
  aiPromote: (workspaceId: string, sql: string, sourceStepIndex: number | null, question: string, stepId?: string | null) =>
    call<{ workspace_id: string; step_id: string; pipeline_revision: number }>("ai_promote", { workspaceId, sql, sourceStepIndex, question, stepId: stepId ?? null }),
};
