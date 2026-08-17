import { useEffect, useRef, useState } from "react";
import { api, backendReachable, BackendStep, SourceSummary, AiStatus, AiAskResult, AnalysisSpec, AnalysisResult, AnalysisMeasure, AnalysisJoin, RecipeName } from "./bridge";
import { s, Hv, MONO, dot, box, fmtNum } from "./ui";
import {
  RAW_ROWS,
  BASE_COLS,
  OPERATORS,
  FILTER_OPERATORS,
  VALUELESS_OPS,
  STEP_LABELS,
  MODAL_FOR_TYPE,
  TABLE_SCHEMAS,
  ADD_STEP_ORDER,
} from "./data";
import {
  Step,
  Workspace,
  NewFlow,
  stepSummary,
  buildExistingWorkspace,
  buildNewWorkspace,
  computeRows,
  computeValidation,
} from "./logic";

type ModalKind =
  | null
  | "filter"
  | "join"
  | "dedupe"
  | "replace"
  | "formula"
  | "validate"
  | "reviewDiff"
  | "commitTarget"
  | "readyCommit"
  | "committing"
  | "committed";

type State = {
  screen: "signin" | "home" | "watched" | "sources" | "newWorkspace" | "browser" | "workingset" | "checkout" | "workspace";
  authing: boolean;
  activeWorkspace: Workspace | null;
  selectedStepIndex: number;
  addStepOpen: boolean;
  modal: ModalKind;
  modalStepId: string | null;
  modalConfig: any;
  validateShowFailedOnly: boolean;
  newFlow: NewFlow;
  cpu: number;
  ramMB: number;
  checkoutProgress: number;
  commitTarget: "existing" | "new";
  commitExistingTable: string;
  commitNewCatalog: string;
  commitNewSchema: string;
  commitNewTableName: string;
  // ---- live (Tauri) read-path ----
  warehouseId: string | null;
  authError: string | null;
  checkoutError: string | null;
  checkoutMsg: string;
  // ---- Phase 3: saved workspaces + windowed grid ----
  savedWorkspaces: { workspace_id: string; name: string; source_table: string; row_count: number; logical_bytes: number; created_at: string }[];
  openWorkspaceId: string | null;
  gridTotal: number;
  gridSort: { column: string; direction: string }[];
  gridLoading: boolean;
  gridColWidths: Record<string, number>;
  stepCounts: Record<string, number | null>;
  stepErrors: Record<string, string | null>;
  // ---- Phase 5: watched folder + imported sources ----
  watchFolder: string;
  watchFiles: { name: string; path: string; size: number; format: string; stable: boolean }[];
  watchTargetWs: string;
  watchMsg: string | null;
  localSources: SourceSummary[];
  // importing a second UC table as a join source (reuses the browser flow)
  importMode: boolean;
  importTargetWs: string | null;
  importJoinStepId: string | null;
  // ---- Phase 6: row identity, diff, validation ----
  rowKey: string[];
  rowKeyColumns: string[];
  rowKeyUnique: boolean | null;
  diffResult: { added: number; modified: number; deleted: number; unchanged: number; samples: { key: Record<string, unknown>; changes: { column: string; before: unknown; after: unknown }[] }[] } | null;
  diffError: string | null;
  validateRules: { id: string; column: string; kind: string; value: string; severity: string }[];
  validateResult: { total: number; valid: number; invalid: number; blocking: boolean; per_rule: { id: string; invalid: number | null; error: string | null; severity: string }[]; failed_columns: string[]; failed_rows: unknown[][] } | null;
  // ---- Phase 7: commit ----
  sourceTable: string;
  commitChecks: Record<string, boolean> | null;
  commitResult: { new_version: number | null; target_table: string; row_count: number; created: boolean } | null;
  commitConflict: { base: number; current: number } | null;
  commitMsg: string;
  commitAddedCols: string[];
  commitNs: { catalog_exists: boolean; schema_exists: boolean } | null;
  commitNsBusy: boolean;
  commitNsError: string | null;
  // ---- Phase 9: UX pass ----
  checkoutEstimate: number | null;
  tableTotalRowCount: number | null; // unfiltered row count — shown regardless of filters
  checkoutSample: { columns: string[]; rows: unknown[][] } | null;
  checkoutSampleLoading: boolean;
  checkoutSampleError: string | null;
  checkoutEstimating: boolean;
  checkoutOnly: boolean; // checkout launched from Sources tab: no workspace opened after
  modalPreview: { rows: any[]; cols: string[]; totalBefore: number; totalAfter: number } | null;
  modalPreviewLoading: boolean;
  replaceDistinct: { value: unknown; count: number }[];
  replaceDistinctLoading: boolean;
  // ---- column stats popover ----
  statsCol: string | null;
  statsData: { column: string; type: string; total: number; nulls: number; null_pct: number; distinct: number; min: unknown; max: unknown; top_values: { value: unknown; count: number }[] } | null;
  statsLoading: boolean;
  statsAnchor: { x: number; y: number } | null;
  // ---- header AutoFilter ----
  headerFilterCol: string | null;
  headerFilterValues: { value: unknown; count: number }[];
  headerFilterSelected: Set<string>;
  headerFilterLoading: boolean;
  headerFilterAnchor: { x: number; y: number } | null;
  // ---- manual cell edit ----
  editingCell: { rowIdx: number; column: string } | null;
  editingCellValue: string;
  // ---- Sources tab (decoupled, shared registry) ----
  sourcesList: SourceSummary[];
  sourcesLoading: boolean;
  sourceActionMsg: string | null;
  sourceActionBusy: string | null; // source_id currently being deleted/refreshed
  wsActionBusy: string | null; // workspace_id currently being deleted
  aiStatus: AiStatus | null;
  aiInstalling: boolean;
  aiInstallProgress: { bytes: number; total_bytes: number } | null;
  aiInstallError: string | null;
  aiQuestion: string;
  aiAsking: boolean;
  aiResult: AiAskResult | null;
  aiPromoting: boolean;
  aiSqlDraft: string;
  aiSqlRunning: boolean;
  aiEditingStepId: string | null;
  workspaceTab: "data" | "explore" | "workflow";
  analysisSpec: AnalysisSpec;
  analysisResult: AnalysisResult | null;
  analysisLoading: boolean;
  analysisError: string | null;
  analysisShowSql: boolean;
  analysisDimPickerOpen: boolean;
  analysisMeasurePickerOpen: boolean;
  analysisDrillRow: { rowIndex: number; x: number; y: number; breakdownOpen: boolean } | null;
  analysisEditingFilter: number | null;
  analysisEditingHaving: number | null;
  analysisJoinPickerOpen: boolean;
  analysisPromoting: boolean;
  recipeName: RecipeName | null;
  recipeParams: Record<string, any>;
  recipeResult: AnalysisResult | null;
  recipeLoading: boolean;
  recipeError: string | null;
  recipePromoting: boolean;
  recipeShowSql: boolean;
  browse: {
    catalogs: { name: string }[];
    schemas: Record<string, { name: string }[]>;
    tables: Record<string, { full_name: string; name: string }[]>;
    openCat: string | null;
    openSch: string | null;
    loading: boolean;
  };
};

const INITIAL: State = {
  screen: "signin",
  authing: false,
  activeWorkspace: null,
  selectedStepIndex: 0,
  addStepOpen: false,
  modal: null,
  modalStepId: null,
  modalConfig: {},
  validateShowFailedOnly: false,
  newFlow: {
    tableName: null,
    columns: { customer_id: true, company: true, country: true, email: true, updated_at: true, revenue: false, cost: false },
    rowId: "single",
    filters: [],
    rowKeyCols: [],
  },
  cpu: 6,
  ramMB: 340,
  checkoutProgress: 0,
  commitTarget: "existing",
  commitExistingTable: "main.crm.customers",
  commitNewCatalog: "main",
  commitNewSchema: "crm",
  commitNewTableName: "",
  warehouseId: null,
  authError: null,
  checkoutError: null,
  checkoutMsg: "",
  savedWorkspaces: [],
  openWorkspaceId: null,
  gridTotal: 0,
  gridSort: [],
  gridLoading: false,
  gridColWidths: {},
  stepCounts: {},
  stepErrors: {},
  watchFolder: "~/Company Data/Inbox",
  watchFiles: [],
  watchTargetWs: "",
  watchMsg: null,
  localSources: [],
  importMode: false,
  importTargetWs: null,
  importJoinStepId: null,
  rowKey: [],
  rowKeyColumns: [],
  rowKeyUnique: null,
  diffResult: null,
  diffError: null,
  validateRules: [],
  validateResult: null,
  sourceTable: "",
  commitChecks: null,
  commitResult: null,
  commitConflict: null,
  commitMsg: "",
  commitAddedCols: [],
  commitNs: null,
  commitNsBusy: false,
  commitNsError: null,
  checkoutEstimate: null,
  tableTotalRowCount: null,
  checkoutSample: null,
  checkoutSampleLoading: false,
  checkoutSampleError: null,
  checkoutEstimating: false,
  checkoutOnly: false,
  modalPreview: null,
  modalPreviewLoading: false,
  replaceDistinct: [],
  replaceDistinctLoading: false,
  statsCol: null,
  statsData: null,
  statsLoading: false,
  statsAnchor: null,
  headerFilterCol: null,
  headerFilterValues: [],
  headerFilterSelected: new Set(),
  headerFilterLoading: false,
  headerFilterAnchor: null,
  editingCell: null,
  editingCellValue: "",
  sourcesList: [],
  sourcesLoading: false,
  sourceActionMsg: null,
  sourceActionBusy: null,
  wsActionBusy: null,
  aiStatus: null,
  aiInstalling: false,
  aiInstallProgress: null,
  aiInstallError: null,
  aiQuestion: "",
  aiAsking: false,
  aiResult: null,
  aiPromoting: false,
  aiSqlDraft: "",
  aiSqlRunning: false,
  aiEditingStepId: null,
  workspaceTab: "data",
  analysisSpec: { dimensions: [], measures: [], filters: [], sort: [], limit: 500 },
  analysisResult: null,
  analysisLoading: false,
  analysisError: null,
  analysisShowSql: false,
  analysisDimPickerOpen: false,
  analysisMeasurePickerOpen: false,
  analysisDrillRow: null,
  analysisEditingFilter: null,
  analysisEditingHaving: null,
  analysisJoinPickerOpen: false,
  analysisPromoting: false,
  recipeName: null,
  recipeParams: {},
  recipeResult: null,
  recipeLoading: false,
  recipeError: null,
  recipePromoting: false,
  recipeShowSql: false,
  browse: { catalogs: [], schemas: {}, tables: {}, openCat: null, openSch: null, loading: false },
};

const GRID_PAGE = 200;
const DEFAULT_COL_W = 170; // columns wrap at this width until manually resized

// Convert a windowed PreviewPage (array-of-arrays) to row objects for the grid.
function pageToRows(page: { columns: string[]; rows: unknown[][] }): any[] {
  return page.rows.map((row) => {
    const obj: Record<string, unknown> = {};
    page.columns.forEach((c, i) => (obj[c] = row[i]));
    return obj;
  });
}

function modalConfigForType(type: string): any {
  if (type === "filter") return { conditions: [{ column: "country", operator: "equals", value: "Netherlands" }] };
  if (type === "join_file") return { file: "customer_mapping.xlsx", leftKey: "customer_id", rightKey: "customer_id", joinType: "left" };
  if (type === "deduplicate") return { key: "email", keep: "latest" };
  if (type === "replace")
    return { mappings: [ { from: "Netherland", to: "Netherlands" }, { from: "NL", to: "Netherlands" }, { from: "Nederland", to: "Netherlands" } ] };
  if (type === "validate") return { rules: { not_null: true, email_at: true, country_list: false, revenue_pos: false } };
  return {};
}

export default function App() {
  const [st, setSt] = useState<State>(INITIAL);
  const setState = (patch: Partial<State> | ((p: State) => Partial<State>)) =>
    setSt((p) => ({ ...p, ...(typeof patch === "function" ? patch(p) : patch) }));

  // ---- CPU / RAM metrics ticker ----
  useEffect(() => {
    const id = setInterval(() => {
      setSt((p) => {
        const busy = p.screen === "checkout" || p.modal === "committing";
        const cpu = busy ? 35 + Math.round(Math.random() * 25) : 4 + Math.round(Math.random() * 9);
        const ramMB = (busy ? 520 : 340) + Math.round(Math.random() * 60);
        return { ...p, cpu, ramMB };
      });
    }, 1800);
    return () => clearInterval(id);
  }, []);

  const checkoutTimer = useRef<number | null>(null);

  // Manual column resize: drag the header edge to set a fixed width; columns
  // wrap at DEFAULT_COL_W until dragged.
  const startColResize = (column: string, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX;
    const startW = st.gridColWidths[column] ?? DEFAULT_COL_W;
    const move = (ev: MouseEvent) => {
      const w = Math.max(60, startW + (ev.clientX - startX));
      setState((p) => ({ gridColWidths: { ...p.gridColWidths, [column]: w } }));
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  // Backend health (P0.3): live mode when the Python backend answers a ping;
  // otherwise fall back to the local mock so the UI still runs standalone.
  const [coreConnected, setCoreConnected] = useState(false);
  const [live, setLive] = useState(false);
  useEffect(() => {
    backendReachable().then((ok) => {
      setLive(ok);
      setCoreConnected(ok);
      if (ok) {
        api.configGet().then((c) => setState({ warehouseId: c.warehouse_id })).catch(() => {});
        loadSavedWorkspaces();
      }
    });
  }, []);

  const loadSavedWorkspaces = () =>
    api.workspaceSummaries().then((ws) => setState({ savedWorkspaces: ws })).catch(() => {});

  // Open a saved/checked-out workspace with a windowed grid (Phase 3).
  const openLiveWorkspace = (workspaceId: string, name: string, sourceTable: string, initialTab: "data" | "explore" | "workflow" = "data") => {
    Promise.all([api.workspaceSchema(workspaceId), api.pipelineGet(workspaceId)])
      .then(([schema, pipe]) => {
        const cols = schema.columns.map((c) => c.name);
        // Restore saved steps; ensure a source step at index 0.
        const saved = (pipe.steps || []).filter((s) => s.type !== "source");
        const pipeline: Step[] = [
          { id: "src", type: "source", summary: sourceTable || name },
          ...saved.map((s) => ({ id: s.id, type: s.type, config: s.config, summary: backendSummary(s.type, s.config), inputId: s.input_id ?? null })),
        ];
        const lastIndex = pipeline.length - 1;
        setState({
          openWorkspaceId: workspaceId,
          sourceTable,
          gridTotal: schema.total,
          gridSort: [],
          stepCounts: {},
          stepErrors: {},
          activeWorkspace: {
            name,
            version: 481,
            changes: 0,
            committed: false,
            rowCountLabel: schema.total.toLocaleString("en-US"),
            sizeLabel: "local",
            pipeline,
            sourceRows: [],
            sourceCols: cols,
          },
          selectedStepIndex: lastIndex,
          screen: "workspace",
          workspaceTab: initialTab,
          analysisSpec: { dimensions: [], measures: [], filters: [], sort: [], limit: 500 },
          analysisResult: null,
          analysisError: null,
          aiResult: null,
          aiQuestion: "",
        });
        // Load counts + preview the final step.
        refreshLivePipeline(workspaceId, pipeline, lastIndex);
        loadAiStatus();
        // Row identity (Phase 6).
        api
          .rowKeyGet(workspaceId)
          .then((rk) => {
            setState({ rowKey: rk.keys, rowKeyColumns: rk.columns, diffResult: null, validateResult: null, validateRules: [] });
            if (rk.keys.length) api.rowKeySet(workspaceId, rk.keys).then((r) => setState({ rowKeyUnique: r.unique })).catch(() => setState({ rowKeyUnique: null }));
            else setState({ rowKeyUnique: null });
          })
          .catch(() => {});
      })
      .catch((e) => setState({ authError: String(e) }));
  };

  // Fetch the next window and append (infinite scroll).
  const loadMoreRows = () => {
    if (!st.openWorkspaceId || st.gridLoading) return;
    const ws = st.activeWorkspace;
    const loaded = ws?.sourceRows?.length ?? 0;
    if (!ws || loaded >= st.gridTotal) return;
    setState({ gridLoading: true });
    const steps = toBackendSteps(ws.pipeline);
    api
      .previewStep(st.openWorkspaceId, steps, st.selectedStepIndex, loaded, GRID_PAGE, st.gridSort)
      .then((page) => {
        setSt((p) => {
          const cur = p.activeWorkspace;
          if (!cur) return { ...p, gridLoading: false };
          return { ...p, gridLoading: false, activeWorkspace: { ...cur, sourceRows: [...(cur.sourceRows ?? []), ...pageToRows(page)] } };
        });
      })
      .catch(() => setState({ gridLoading: false }));
  };

  // Server-side sort: toggle a column, re-fetch from the top (pipeline-aware).
  const toggleSort = (column: string) => {
    if (!st.openWorkspaceId || !st.activeWorkspace) return;
    const cur = st.gridSort[0];
    const direction = cur && cur.column === column && cur.direction === "asc" ? "desc" : "asc";
    const sort = [{ column, direction }];
    fetchStepPreview(st.openWorkspaceId, st.activeWorkspace.pipeline, st.selectedStepIndex, sort);
  };

  // ---- Navigation / auth ----
  const signIn = () => {
    if (live) {
      setState({ authing: true, authError: null });
      api
        .authConnect()
        .then(() => setState({ authing: false, screen: "home" }))
        .catch((e) => setState({ authing: false, authError: String(e) }));
      return;
    }
    setState({ authing: true });
    setTimeout(() => setState({ authing: false, screen: "home" }), 900);
  };
  const signOut = () => {
    if (live) api.authLogout().catch(() => {});
    setState({ screen: "signin", activeWorkspace: null });
  };
  const goHome = () => {
    setState({ screen: "home", activeWorkspace: null, modal: null, addStepOpen: false, openWorkspaceId: null });
    if (live) loadSavedWorkspaces();
  };
  // Cancel the browser/working-set flow; return to the workspace when importing.
  const cancelFlow = () => {
    if (live && st.importMode && st.importTargetWs) setState({ importMode: false, screen: "workspace" });
    else if (live && st.checkoutOnly) setState({ checkoutOnly: false, screen: "sources" });
    else if (live) setState({ screen: "newWorkspace" });
    else goHome();
  };

  const openWatchedFolder = () => {
    setState({ screen: "watched", watchMsg: null });
    if (live) {
      loadSavedWorkspaces();
      setState((p) => ({ watchTargetWs: p.watchTargetWs || p.savedWorkspaces[0]?.workspace_id || "" }));
    }
  };

  const openExistingWorkspace = () =>
    setState({ activeWorkspace: buildExistingWorkspace(), selectedStepIndex: 4, screen: "workspace" });

  // ---- Sources tab (decoupled registry: shared across every notebook) ----
  const loadSources = () => {
    if (!live) return;
    setState({ sourcesLoading: true });
    api
      .sourceList()
      .then((srcs) => setState({ sourcesList: srcs, sourcesLoading: false }))
      .catch(() => setState({ sourcesLoading: false }));
  };
  const openSources = () => {
    setState({ screen: "sources", sourceActionMsg: null });
    loadSources();
  };
  const deleteSourceHandler = (sourceId: string, name: string) => {
    setState({ sourceActionBusy: sourceId, sourceActionMsg: null });
    api
      .sourceDelete(sourceId)
      .then(() => {
        setState({ sourceActionBusy: null, sourceActionMsg: `Deleted "${name}".` });
        loadSources();
      })
      .catch((e) => setState({ sourceActionBusy: null, sourceActionMsg: String(e) }));
  };
  const refreshSourceHandler = (sourceId: string, name: string) => {
    setState({ sourceActionBusy: sourceId, sourceActionMsg: null });
    api
      .sourceRefresh(sourceId)
      .then((src) => {
        setState({ sourceActionBusy: null, sourceActionMsg: `Refreshed "${name}" — ${src.row_count.toLocaleString()} rows.` });
        loadSources();
      })
      .catch((e) => setState({ sourceActionBusy: null, sourceActionMsg: String(e) }));
  };
  const deleteWorkspaceHandler = (workspaceId: string, name: string) => {
    setState({ wsActionBusy: workspaceId });
    api
      .workspaceDelete(workspaceId)
      .then(() => {
        setState({ wsActionBusy: null });
        loadSavedWorkspaces();
      })
      .catch((e) => setState({ wsActionBusy: null, sourceActionMsg: String(e) }));
  };
  const loadAiStatus = () => {
    api.aiStatus().then((st) => setState({ aiStatus: st })).catch(() => setState({ aiStatus: null }));
  };
  const enableLocalAi = () => {
    setState({ aiInstalling: true, aiInstallError: null, aiInstallProgress: { bytes: 0, total_bytes: 1 } });
    api
      .aiModelInstall()
      .then(({ operation_id }) => {
        const poll = () => {
          api
            .aiModelInstallStatus(operation_id)
            .then((s) => {
              setState({ aiInstallProgress: { bytes: s.bytes, total_bytes: s.total_bytes || 1 } });
              if (s.state === "INSTALLED") {
                setState({ aiInstalling: false });
                loadAiStatus();
              } else if (s.state === "FAILED") {
                setState({ aiInstalling: false, aiInstallError: s.error || "install failed" });
              } else {
                setTimeout(poll, 800);
              }
            })
            .catch((e) => setState({ aiInstalling: false, aiInstallError: String(e) }));
        };
        poll();
      })
      .catch((e) => setState({ aiInstalling: false, aiInstallError: String(e) }));
  };
  // ---- Explore: AnalysisSpec (Phase 11, P11.7-P11.9) — no-AI group/measure/
  // filter/sort builder. Every change here just recompiles and reruns
  // DuckDB; never touches the pipeline (ephemeral by design, §50).
  const analysisTimer = useRef<number | null>(null);
  const analysisAbort = useRef<AbortController | null>(null);
  const runAnalysis = (spec: AnalysisSpec) => {
    const wsId = st.openWorkspaceId;
    if (!wsId || !st.activeWorkspace) return;
    if (analysisTimer.current) window.clearTimeout(analysisTimer.current);
    // A brand-new request always supersedes whatever's in flight — cancel it
    // client-side so a slow stale response can never clobber a fresher one
    // (§43: "the previous query should not block the new one").
    if (analysisAbort.current) analysisAbort.current.abort();
    setState({ analysisLoading: true, analysisError: null });
    const steps = toBackendSteps(st.activeWorkspace.pipeline);
    const stepIndex = st.selectedStepIndex;
    analysisTimer.current = window.setTimeout(() => {
      const controller = new AbortController();
      analysisAbort.current = controller;
      api
        .exploreRunAnalysis(wsId, steps, stepIndex, spec, controller.signal)
        .then((res) => setState({ analysisLoading: false, analysisResult: res }))
        .catch((e) => {
          if (controller.signal.aborted) return; // superseded — ignore, not an error
          setState({ analysisLoading: false, analysisResult: null, analysisError: String(e) });
        });
    }, 300);
  };
  const cancelAnalysis = () => {
    if (analysisTimer.current) window.clearTimeout(analysisTimer.current);
    if (analysisAbort.current) analysisAbort.current.abort();
    setState({ analysisLoading: false });
  };

  // Intent recipes (Phase 12, P12.9) — deterministic expansions to the
  // canonical operators, run/promoted through their own dedicated endpoints
  // (backend builds the AnalysisSpec server-side; the frontend only ever
  // sends recipe name + params).
  const setRecipeName = (name: RecipeName | null) => setState({ recipeName: name, recipeParams: {}, recipeResult: null, recipeError: null });
  const setRecipeParam = (key: string, value: any) => setState((p) => ({ recipeParams: { ...p.recipeParams, [key]: value } }));
  const runRecipe = (paramsOverride?: Record<string, unknown>) => {
    const wsId = st.openWorkspaceId;
    if (!wsId || !st.activeWorkspace || !st.recipeName) return;
    const params = paramsOverride ?? st.recipeParams;
    setState({ recipeLoading: true, recipeError: null, recipeParams: params });
    const steps = toBackendSteps(st.activeWorkspace.pipeline);
    api
      .exploreRunRecipe(wsId, steps, st.selectedStepIndex, st.recipeName, params)
      .then((res) => setState({ recipeLoading: false, recipeResult: res }))
      .catch((e) => setState({ recipeLoading: false, recipeResult: null, recipeError: String(e) }));
  };
  const promoteRecipe = () => {
    const wsId = st.openWorkspaceId;
    if (!wsId || !st.activeWorkspace || !st.recipeName) return;
    setState({ recipePromoting: true });
    const steps = toBackendSteps(st.activeWorkspace.pipeline);
    api
      .explorePromoteRecipe(wsId, steps, st.selectedStepIndex, st.recipeName, st.recipeParams)
      .then(() => {
        setState({ recipePromoting: false, recipeResult: null, recipeName: null, recipeParams: {} });
        openLiveWorkspace(wsId, st.activeWorkspace?.name || "", st.sourceTable || "", "workflow");
      })
      .catch((e) => setState({ recipePromoting: false, recipeError: String(e) }));
  };
  const updateAnalysisSpec = (patch: Partial<AnalysisSpec>) => {
    setState((p) => {
      const spec = { ...p.analysisSpec, ...patch };
      runAnalysis(spec);
      return { analysisSpec: spec };
    });
  };
  const toggleDimension = (col: string) => {
    const has = st.analysisSpec.dimensions.includes(col);
    updateAnalysisSpec({ dimensions: has ? st.analysisSpec.dimensions.filter((d) => d !== col) : [...st.analysisSpec.dimensions, col] });
  };
  const addMeasure = (col: string) => updateAnalysisSpec({ measures: [...st.analysisSpec.measures, { column: col, aggregation: "count" }] });
  const removeMeasure = (idx: number) => updateAnalysisSpec({ measures: st.analysisSpec.measures.filter((_, i) => i !== idx) });
  const setMeasureAgg = (idx: number, aggregation: string) =>
    updateAnalysisSpec({ measures: st.analysisSpec.measures.map((m, i) => (i === idx ? { ...m, aggregation } : m)) });
  const addAnalysisFilter = (col: string) => {
    const idx = st.analysisSpec.filters.length;
    updateAnalysisSpec({ filters: [...st.analysisSpec.filters, { column: col, operator: ">", value: "" }] });
    setState({ analysisEditingFilter: idx });
  };
  const setAnalysisFilterField = (idx: number, field: "column" | "operator" | "value", val: string) =>
    updateAnalysisSpec({ filters: st.analysisSpec.filters.map((f, i) => (i === idx ? { ...f, [field]: val } : f)) });
  const removeAnalysisFilter = (idx: number) => updateAnalysisSpec({ filters: st.analysisSpec.filters.filter((_, i) => i !== idx) });
  const setAnalysisSort = (col: string) => {
    const cur = st.analysisSpec.sort[0];
    const direction = cur?.column === col && cur.direction === "desc" ? "asc" : "desc";
    updateAnalysisSpec({ sort: [{ column: col, direction }] });
  };
  const resetAnalysis = () => setState({ analysisSpec: { dimensions: [], measures: [], filters: [], sort: [], limit: 500 }, analysisResult: null, analysisError: null });
  const measureOutputName = (m: AnalysisMeasure) => m.alias || `${m.aggregation}_${m.column}`;

  // Distinct / Having / Join (Phase 12, P12.4-P12.5, P12.10).
  const toggleDistinct = () => updateAnalysisSpec({ distinct: !st.analysisSpec.distinct });
  const addHaving = (col: string) => {
    const having = st.analysisSpec.having ?? [];
    const idx = having.length;
    updateAnalysisSpec({ having: [...having, { column: col, operator: ">", value: "" }] });
    setState({ analysisEditingHaving: idx });
  };
  const setHavingField = (idx: number, field: "column" | "operator" | "value", val: string) =>
    updateAnalysisSpec({ having: (st.analysisSpec.having ?? []).map((f, i) => (i === idx ? { ...f, [field]: val } : f)) });
  const removeHaving = (idx: number) => updateAnalysisSpec({ having: (st.analysisSpec.having ?? []).filter((_, i) => i !== idx) });

  const setJoin = (join: AnalysisJoin | null) => updateAnalysisSpec({ join });
  const addJoinKey = () => {
    const j = st.analysisSpec.join;
    if (!j) return;
    const cols = st.activeWorkspace?.sourceCols ?? [];
    setJoin({ ...j, keys: [...j.keys, [cols[0] || "", ""]] });
  };
  const setJoinKey = (idx: number, side: 0 | 1, val: string) => {
    const j = st.analysisSpec.join;
    if (!j) return;
    const keys = j.keys.map((k, i) => (i === idx ? ([side === 0 ? val : k[0], side === 1 ? val : k[1]] as [string, string]) : k));
    setJoin({ ...j, keys });
  };
  const removeJoinKey = (idx: number) => {
    const j = st.analysisSpec.join;
    if (!j) return;
    setJoin({ ...j, keys: j.keys.filter((_, i) => i !== idx) });
  };

  // "Keep as workflow" (Phase 11, P11.16) — promotes the current AnalysisSpec
  // into a durable, human-named sql_transform step. Exploration itself never
  // touches the pipeline until this is called (§26, §50).
  const promoteAnalysis = () => {
    const wsId = st.openWorkspaceId;
    if (!wsId || !st.activeWorkspace) return;
    setState({ analysisPromoting: true });
    const steps = toBackendSteps(st.activeWorkspace.pipeline);
    api
      .explorePromoteAnalysis(wsId, steps, st.selectedStepIndex, st.analysisSpec, "")
      .then(() => {
        setState({ analysisPromoting: false });
        resetAnalysis();
        openLiveWorkspace(wsId, st.activeWorkspace?.name || "", st.sourceTable || "", "workflow");
      })
      .catch((e) => setState({ analysisPromoting: false, analysisError: String(e) }));
  };

  // Direct manipulation / drill-down on a result row (§14-15) — only offered
  // when the result has exactly one dimension, so "the value under the
  // cursor" is unambiguous.
  const openDrillMenu = (rowIndex: number, x: number, y: number) => setState({ analysisDrillRow: { rowIndex, x, y, breakdownOpen: false } });
  const closeDrillMenu = () => setState({ analysisDrillRow: null });
  const toggleDrillBreakdown = () => setState((p) => (p.analysisDrillRow ? { analysisDrillRow: { ...p.analysisDrillRow, breakdownOpen: !p.analysisDrillRow.breakdownOpen } } : {}));
  const drillFilterTo = (col: string, value: unknown, exclude: boolean) => {
    const filters = [...st.analysisSpec.filters.filter((f) => f.column !== col), { column: col, operator: exclude ? "!=" : "=", value: value === null ? "" : String(value) }];
    updateAnalysisSpec({ filters });
    closeDrillMenu();
  };
  const drillBreakdownBy = (currentCol: string, currentValue: unknown, newCol: string) => {
    const filters = [...st.analysisSpec.filters.filter((f) => f.column !== currentCol), { column: currentCol, operator: "=", value: currentValue === null ? "" : String(currentValue) }];
    updateAnalysisSpec({ filters, dimensions: [newCol] });
    closeDrillMenu();
  };
  const drillViewRows = (col: string, value: unknown) => {
    const filters = [...st.analysisSpec.filters.filter((f) => f.column !== col), { column: col, operator: "=", value: value === null ? "" : String(value) }];
    updateAnalysisSpec({ filters, dimensions: [], measures: [] });
    closeDrillMenu();
  };

  const askAi = () => {
    const wsId = st.openWorkspaceId;
    const question = st.aiQuestion.trim();
    if (!wsId || !question || !st.activeWorkspace) return;
    setState({ aiAsking: true, aiResult: null, aiEditingStepId: null });
    const steps = toBackendSteps(st.activeWorkspace.pipeline);
    api
      .aiAsk(wsId, steps, st.selectedStepIndex, question)
      .then((res) => setState({ aiAsking: false, aiResult: res, aiSqlDraft: res.generated_sql || "" }))
      .catch((e) => setState({ aiAsking: false, aiResult: { id: "", question, status: "INVALID", generated_sql: null, attempt_count: 0, inference_ms: 0, execution_ms: 0, rows_returned: 0, columns: [], preview_rows: [], error: String(e) } }));
  };
  // Run the (possibly hand-edited) SQL draft directly — no model call, just
  // AST-validate + execute — so edits can be checked before saving.
  const runSqlDraft = () => {
    const wsId = st.openWorkspaceId;
    const sql = st.aiSqlDraft.trim();
    if (!wsId || !sql || !st.activeWorkspace) return;
    setState({ aiSqlRunning: true });
    const steps = toBackendSteps(st.activeWorkspace.pipeline);
    api
      .aiRunSql(wsId, steps, st.selectedStepIndex, sql)
      .then((res) => setState({ aiSqlRunning: false, aiResult: { ...res, question: st.aiResult?.question || st.aiQuestion } }))
      .catch((e) => setState({ aiSqlRunning: false, aiResult: { id: "", question: st.aiResult?.question || st.aiQuestion, status: "INVALID", generated_sql: null, attempt_count: 0, inference_ms: 0, execution_ms: 0, rows_returned: 0, columns: [], preview_rows: [], error: String(e) } }));
  };
  const promoteAiResult = () => {
    const wsId = st.openWorkspaceId;
    const sql = st.aiSqlDraft.trim();
    if (!wsId || !sql) return;
    setState({ aiPromoting: true });
    api
      .aiPromote(wsId, sql, st.selectedStepIndex, st.aiResult?.question || st.aiQuestion, st.aiEditingStepId)
      .then(() => {
        setState({ aiPromoting: false, aiResult: null, aiQuestion: "", aiSqlDraft: "", aiEditingStepId: null });
        openLiveWorkspace(wsId, st.activeWorkspace?.name || "", st.sourceTable || "");
      })
      .catch((e) => setState({ aiPromoting: false, aiInstallError: String(e) }));
  };
  // Reopen an already-promoted sql_transform step for editing, reusing the
  // same ask-bar SQL editor/preview instead of a separate modal.
  const editSqlStep = (step: Step, index: number) => {
    const wsId = st.openWorkspaceId;
    if (!wsId) return;
    const sql = step.config?.sql || "";
    setState({ aiEditingStepId: step.id, aiSqlDraft: sql, aiQuestion: step.config?.user_question || "", aiResult: null, selectedStepIndex: index > 0 ? index - 1 : 0 });
  };
  const cancelSqlEdit = () => setState({ aiEditingStepId: null, aiSqlDraft: "", aiResult: null, aiQuestion: "" });
  const startNotebookFromSource = (sourceId: string, name: string) => {
    setState({ sourceActionBusy: sourceId, sourceActionMsg: null });
    api
      .workspaceCreateFromSource(sourceId, name + " Workspace")
      .then((ws) => {
        loadSavedWorkspaces();
        openLiveWorkspace(ws.workspace_id, ws.name, ws.source_table || name);
      })
      .catch((e) => setState({ sourceActionBusy: null, sourceActionMsg: String(e) }));
  };

  const loadCatalogs = () => {
    setState((p) => ({ browse: { catalogs: [], schemas: {}, tables: {}, openCat: null, openSch: null, loading: true } }));
    api
      .catalogList()
      .then((cats) => setState((p) => ({ browse: { ...p.browse, catalogs: cats.map((c) => ({ name: c.name })), loading: false } })))
      .catch((e) => setState((p) => ({ browse: { ...p.browse, loading: false }, authError: String(e) })));
  };

  const ensureSchemasLoaded = (catalog: string) => {
    if (!catalog || st.browse.schemas[catalog]) return;
    api
      .schemaList(catalog)
      .then((schemas) => setState((p) => ({ browse: { ...p.browse, schemas: { ...p.browse.schemas, [catalog]: schemas.map((s) => ({ name: s.name })) } } })))
      .catch(() => {});
  };

  // Catalogs commonly read-only / noise for a destination picker (P9.26).
  const READONLY_CATALOG_HINTS = new Set(["samples", "system", "__databricks_internal"]);

  // "+ New Workspace" opens a chooser: use an existing source, checkout a new
  // table, or import a file — instead of jumping straight into checkout.
  const startNewWorkspace = () => {
    if (!live) {
      setState({ screen: "browser", importMode: false });
      return;
    }
    setState({ screen: "newWorkspace", checkoutOnly: false, checkoutError: null });
    loadSources();
  };

  // From the chooser: "Checkout a table from Unity Catalog" (creates a source
  // + opens a workspace on it, the normal checkout flow).
  const chooseCheckoutNewTable = () => {
    setState({ screen: "browser", importMode: false, checkoutOnly: false });
    loadCatalogs();
  };

  // From the Sources tab: "+ Checkout new table" — creates only a Source, no
  // workspace opened afterward (P: checkout decoupled from notebooks).
  const startCheckoutSourceOnly = () => {
    setState({ screen: "browser", importMode: false, checkoutOnly: true });
    loadCatalogs();
  };

  // From the chooser: import a local file straight into a brand-new workspace.
  const chooseImportFileForNewWorkspace = (file: File) => {
    setState({ checkoutError: null });
    api
      .localSourceImport(file)
      .then((src) => startNotebookFromSource(src.id, src.name))
      .catch((e) => setState({ checkoutError: String(e) }));
  };

  // Import a second Unity Catalog table into the open workspace as a join source.
  const startImportUcTable = () => {
    if (!st.openWorkspaceId) return;
    setState({ modal: null, importMode: true, importTargetWs: st.openWorkspaceId, importJoinStepId: st.modalStepId, screen: "browser" });
    loadCatalogs();
  };

  // ---- live catalog tree (lazy) ----
  const openCatalog = (cat: string) =>
    setSt((p) => {
      const openCat = p.browse.openCat === cat ? null : cat;
      if (openCat && !p.browse.schemas[cat]) {
        api
          .schemaList(cat)
          .then((schemas) =>
            setState((q) => ({ browse: { ...q.browse, schemas: { ...q.browse.schemas, [cat]: schemas.map((s) => ({ name: s.name })) } } }))
          )
          .catch(() => {});
      }
      return { ...p, browse: { ...p.browse, openCat, openSch: null } };
    });

  const openSchema = (cat: string, sch: string) =>
    setSt((p) => {
      const key = `${cat}.${sch}`;
      const openSch = p.browse.openSch === key ? null : key;
      if (openSch && !p.browse.tables[key]) {
        api
          .tableList(cat, sch)
          .then((tables) =>
            setState((q) => ({
              browse: { ...q.browse, tables: { ...q.browse.tables, [key]: tables.map((t) => ({ full_name: t.full_name, name: t.name })) } },
            }))
          )
          .catch(() => {});
      }
      return { ...p, browse: { ...p.browse, openSch } };
    });

  const selectTable = (name: string) => {
    if (live) {
      // Fetch real columns, default all selected, then go to working-set.
      api
        .tableGet(name)
        .then((meta) => {
          const columns: Record<string, boolean> = {};
          for (const c of meta.columns) columns[c.name] = true;
          // Auto-suggest a row key: prefer an exact "id" column, then any
          // "*_id" column, then fall back to the first column (P9.9).
          const colNames = meta.columns.map((c) => c.name);
          const suggested =
            colNames.find((c) => c.toLowerCase() === "id") ??
            colNames.find((c) => c.toLowerCase().endsWith("_id")) ??
            colNames[0];
          setState((p) => ({
            newFlow: { ...p.newFlow, tableName: name, columns, filters: [], rowKeyCols: suggested ? [suggested] : [] },
            screen: "workingset",
            checkoutEstimate: null,
            tableTotalRowCount: null,
            checkoutSample: null,
            checkoutSampleError: null,
          }));
          // Original row count — always shown, independent of filters.
          if (st.warehouseId) {
            api.checkoutEstimate(st.warehouseId, name, []).then((r) => setState({ tableTotalRowCount: r.row_count })).catch(() => {});
          }
          scheduleCheckoutEstimate();
        })
        .catch((e) => setState({ authError: String(e) }));
      return;
    }
    setState((p) => ({ newFlow: { ...p.newFlow, tableName: name }, screen: "workingset" }));
  };

  const toggleColumn = (key: string) => {
    setState((p) => ({ newFlow: { ...p.newFlow, columns: { ...p.newFlow.columns, [key]: !p.newFlow.columns[key] } } }));
    scheduleCheckoutEstimate();
  };
  const setRowIdSingle = () => setState((p) => ({ newFlow: { ...p.newFlow, rowId: "single" } }));
  const setRowIdComposite = () => setState((p) => ({ newFlow: { ...p.newFlow, rowId: "composite" } }));

  // ---- working-set filters ----
  const estimateTimer = useRef<number | null>(null);
  const scheduleCheckoutEstimate = () => {
    if (!live || st.importMode || !st.warehouseId || !st.newFlow.tableName) return;
    if (estimateTimer.current) window.clearTimeout(estimateTimer.current);
    setState({ checkoutEstimating: true, checkoutSampleLoading: true });
    estimateTimer.current = window.setTimeout(() => {
      const activeFilters = st.newFlow.filters
        .filter((f) => f.column && (VALUELESS_OPS.has(f.op) || f.value !== ""))
        .map((f) => ({ column: f.column, op: f.op, value: VALUELESS_OPS.has(f.op) ? "" : f.value }));
      const selectedCols = Object.keys(st.newFlow.columns).filter((k) => st.newFlow.columns[k]);
      api
        .checkoutEstimate(st.warehouseId!, st.newFlow.tableName!, activeFilters)
        .then((r) => setState({ checkoutEstimate: r.row_count, checkoutEstimating: false }))
        .catch(() => setState({ checkoutEstimate: null, checkoutEstimating: false }));
      // Bounded sample (20 rows) — same columns/filters, so it doubles as a
      // live preview while picking/dropping columns and testing filters.
      api
        .checkoutSample(st.warehouseId!, st.newFlow.tableName!, selectedCols, activeFilters)
        .then((r) => setState({ checkoutSample: r, checkoutSampleLoading: false, checkoutSampleError: null }))
        .catch((e) => setState({ checkoutSampleLoading: false, checkoutSampleError: String(e) }));
    }, 500);
  };

  const addFilter = () => {
    setState((p) => {
      const firstCol = Object.keys(p.newFlow.columns)[0] ?? "";
      return { newFlow: { ...p.newFlow, filters: [...p.newFlow.filters, { column: firstCol, op: "equals", value: "" }] } };
    });
    scheduleCheckoutEstimate();
  };
  const setFilterField = (idx: number, field: "column" | "op" | "value", val: string) => {
    setState((p) => ({
      newFlow: { ...p.newFlow, filters: p.newFlow.filters.map((f, i) => (i === idx ? { ...f, [field]: val } : f)) },
    }));
    scheduleCheckoutEstimate();
  };
  const removeFilter = (idx: number) => {
    setState((p) => ({ newFlow: { ...p.newFlow, filters: p.newFlow.filters.filter((_, i) => i !== idx) } }));
    scheduleCheckoutEstimate();
  };

  const runCheckout = () => {
    // Import-a-second-UC-table mode: reduce + encrypt into the open workspace as
    // a join source, then return and reopen the join step with it selected.
    if (live && st.importMode && st.importTargetWs) {
      const { tableName, columns, filters } = st.newFlow;
      const selected = Object.keys(columns).filter((k) => columns[k]);
      const activeFilters = filters
        .filter((f) => f.column && (VALUELESS_OPS.has(f.op) || f.value !== ""))
        .map((f) => ({ column: f.column, op: f.op, value: VALUELESS_OPS.has(f.op) ? "" : f.value }));
      const wsId = st.importTargetWs;
      const joinStepId = st.importJoinStepId;
      setState({ screen: "checkout", checkoutProgress: 30, checkoutError: null, checkoutMsg: "Checking out table as a join source…" });
      api
        .localSourceFromUc(tableName!, selected, activeFilters)
        .then((src) => {
          const wsCard = st.savedWorkspaces.find((w) => w.workspace_id === wsId);
          openLiveWorkspace(wsId, wsCard?.name || "Workspace", wsCard?.source_table || "");
          setState({ importMode: false, importTargetWs: null });
          // Reopen the join step's modal with the new source preselected.
          window.setTimeout(() => {
            loadLocalSources();
            setState((p) => ({
              modal: "join",
              modalStepId: joinStepId,
              modalConfig: { localSourceId: src.id, fileName: src.name, rightCols: src.columns, rightKey: src.columns[0] ?? "", joinType: "left", leftKey: (p.activeWorkspace?.sourceCols ?? [])[0] ?? "" },
            }));
          }, 500);
        })
        .catch((e) => setState({ screen: "workingset", checkoutError: String(e) }));
      return;
    }
    if (live) {
      const { tableName, columns, filters } = st.newFlow;
      const selected = Object.keys(columns).filter((k) => columns[k]);
      if (!tableName || !st.warehouseId) {
        setState({ checkoutError: !st.warehouseId ? "No warehouse configured (set PYJAMA_WAREHOUSE_ID)" : "No table selected" });
        return;
      }
      // Only send filters with a chosen column; valueless ops (is null / is not
      // null) don't need a value, others do.
      const activeFilters = filters
        .filter((f) => f.column && (VALUELESS_OPS.has(f.op) || f.value !== ""))
        .map((f) => ({ column: f.column, op: f.op, value: VALUELESS_OPS.has(f.op) ? "" : f.value }));
      const checkoutOnly = st.checkoutOnly;
      setState({ screen: "checkout", checkoutProgress: 5, checkoutError: null, checkoutMsg: "☁ Preparing working set in Databricks" });
      api
        .checkoutStart(tableName, selected, activeFilters, st.newFlow.rowKeyCols, !checkoutOnly)
        .then(({ operation_id, workspace_id }) => {
          // Poll the encrypted-checkout progress until terminal.
          const poll = () => {
            api
              .checkoutStatus(operation_id)
              .then((stt) => {
                const total = stt.total_chunks ?? 0;
                const pct = total > 0 ? Math.max(10, Math.round((stt.completed_chunks / total) * 100)) : 15;
                if (stt.state === "COMPLETE") {
                  if (checkoutOnly) {
                    // Checked out straight from Sources — land back there, no
                    // workspace opened.
                    setState({ checkoutOnly: false, screen: "sources", sourceActionMsg: `Checked out ${tableName} (${stt.row_count.toLocaleString()} rows) — available in Sources.` });
                    loadSources();
                    return;
                  }
                  setState({ checkoutProgress: 100, checkoutMsg: "Decrypting local preview" });
                  const name = (tableName.split(".").pop() || "") + " Workspace";
                  loadSavedWorkspaces();
                  openLiveWorkspace(workspace_id!, name, tableName);
                } else if (stt.state === "FAILED") {
                  setState({ screen: "workingset", checkoutError: stt.error || "checkout failed" });
                } else {
                  const msg =
                    stt.state === "DOWNLOADING"
                      ? `Downloading encrypted chunks • ${stt.completed_chunks}/${total || "?"}`
                      : "☁ Preparing working set in Databricks";
                  setState({ checkoutProgress: pct, checkoutMsg: msg });
                  window.setTimeout(poll, 600);
                }
              })
              .catch((e) => setState({ screen: "workingset", checkoutError: String(e) }));
          };
          poll();
        })
        .catch((e) => setState({ screen: "workingset", checkoutError: String(e) }));
      return;
    }
    setState({ screen: "checkout", checkoutProgress: 0 });
    let prog = 0;
    const tick = () => {
      prog += 20;
      if (prog >= 100) {
        setSt((p) => ({
          ...p,
          activeWorkspace: buildNewWorkspace(p.newFlow),
          selectedStepIndex: 0,
          screen: "workspace",
          checkoutProgress: 100,
        }));
      } else {
        setState({ checkoutProgress: prog });
        checkoutTimer.current = window.setTimeout(tick, 260);
      }
    };
    checkoutTimer.current = window.setTimeout(tick, 260);
  };

  // ===== Phase 4: live pipeline (server-side transforms) =====
  const LIVE_STEP_TYPES = ["filter", "formula", "deduplicate", "replace", "join_file"];
  const LIVE_MODAL_FOR: Record<string, ModalKind> = { filter: "filter", formula: "formula", deduplicate: "dedupe", replace: "replace", join_file: "join" };

  const toBackendSteps = (pipeline: Step[]): BackendStep[] =>
    pipeline.map((sstep) => ({ id: sstep.id, type: sstep.type, config: sstep.type === "source" ? {} : sstep.config ?? {}, enabled: true, input_id: sstep.type === "source" ? null : sstep.inputId ?? null }));

  const defaultStepConfig = (type: string, cols: string[]): any => {
    const first = cols[0] ?? "";
    if (type === "filter") return { conditions: [{ column: first, op: "equals", value: "" }], combine: "and" };
    if (type === "deduplicate") return { key: first, keep: "latest" };
    if (type === "replace") return { column: first, mappings: [{ from: "", to: "" }] };
    if (type === "formula") return { name: "new_column", expression: "" };
    if (type === "join_file") return { local_source_id: null, join_type: "left", keys: [{ left: first, right: "" }] };
    return {};
  };

  const backendSummary = (type: string, config: any): string => {
    if (type === "filter") return (config.conditions || []).map((c: any) => `${c.column} ${c.op} ${c.value}`).join(" AND ") || "no conditions";
    if (type === "deduplicate") return `key: ${config.key} · keep ${config.keep}`;
    if (type === "replace") return `${config.column}: ${(config.mappings || []).map((m: any) => `${m.from}→${m.to}`).join(", ")}`;
    if (type === "formula") return `${config.name} = ${config.expression}`;
    if (type === "join_file") return `${config._name || "file"} · ${(config.keys || []).map((k: any) => `${k.left}=${k.right}`).join(", ")} · ${config.join_type}`;
    if (type === "sql_transform") return config.user_question ? `"${config.user_question}"` : (config.sql || "");
    return type;
  };

  // Map the shared modal's UI config to the backend step config shape.
  const modalToConfig = (modal: ModalKind, mc: any, prevConfig: any): any => {
    if (modal === "filter") return { conditions: (mc.conditions || []).map((c: any) => ({ column: c.column, op: c.operator, value: c.value })), combine: "and" };
    if (modal === "dedupe") return { key: mc.key, keep: mc.keep };
    if (modal === "replace") return { column: mc.column ?? prevConfig?.column, mappings: mc.mappings };
    if (modal === "formula") return { name: mc.name, expression: mc.expression };
    if (modal === "join")
      return { local_source_id: mc.localSourceId ?? prevConfig?.local_source_id, join_type: mc.joinType || "left", keys: [{ left: mc.leftKey, right: mc.rightKey }], _name: mc.fileName ?? prevConfig?._name };
    return prevConfig ?? {};
  };

  const configToModal = (type: string, config: any): any => {
    if (type === "filter") return { conditions: (config.conditions || []).map((c: any) => ({ column: c.column, operator: c.op, value: c.value })) };
    if (type === "deduplicate") return { key: config.key, keep: config.keep };
    if (type === "replace") return { column: config.column, mappings: config.mappings };
    if (type === "formula") return { name: config.name, expression: config.expression };
    if (type === "join_file") {
      const k = (config.keys || [{}])[0] || {};
      return { localSourceId: config.local_source_id, joinType: config.join_type || "left", leftKey: k.left, rightKey: k.right, fileName: config._name, rightCols: [] };
    }
    return {};
  };

  // Upload a local file, import it as a standalone source (usable in any
  // notebook), populate the join modal.
  const uploadJoinFile = (file: File) => {
    if (!st.openWorkspaceId) return;
    api
      .localSourceImport(file)
      .then((src) => {
        setState((p) => ({ localSources: [...p.localSources.filter((s) => s.id !== src.id), src] }));
        updateModalConfig({ localSourceId: src.id, fileName: src.name, rightCols: src.columns, rightKey: src.columns[0] ?? "" });
      })
      .catch((e) => updateModalConfig({ uploadError: String(e) }));
  };

  // Load the shared source registry (any source, any notebook can join it).
  const loadLocalSources = () => api.localSourceList().then((srcs) => setState({ localSources: srcs })).catch(() => {});

  // Pick an existing imported source in the join modal.
  const pickImportedSource = (sourceId: string) => {
    const src = st.localSources.find((s) => s.id === sourceId);
    if (!src) return;
    updateModalConfig({ localSourceId: src.id, fileName: src.name, rightCols: src.columns, rightKey: src.columns[0] ?? "", uploadError: null });
  };

  // ---- Watched folder ----
  const scanWatch = () => {
    setState({ watchMsg: "Scanning…" });
    api
      .watchScan(st.watchFolder)
      .then((files) => setState({ watchFiles: files, watchMsg: files.length ? null : "No supported files found (CSV / XLSX / Parquet)." }))
      .catch((e) => setState({ watchMsg: String(e) }));
  };

  // Watched-folder files import straight into the shared source registry — no
  // workspace needed. From there they can be joined into any notebook, or used
  // to start a fresh one.
  const useWatchedFile = (path: string) => {
    setState({ watchMsg: "Importing…" });
    api
      .watchImport(path)
      .then((src) => {
        setState((p) => ({ localSources: [...p.localSources.filter((s) => s.id !== src.id), src], watchMsg: `Imported ${src.name} (${src.row_count} rows) — available in Sources.` }));
        loadSources();
      })
      .catch((e) => setState({ watchMsg: String(e) }));
  };

  const fetchStepPreview = (workspaceId: string, pipeline: Step[], index: number, sort: { column: string; direction: string }[]) => {
    const steps = toBackendSteps(pipeline);
    setState({ gridLoading: true });
    api
      .previewStep(workspaceId, steps, index, 0, GRID_PAGE, sort)
      .then((page) => {
        setSt((p) => {
          const cur = p.activeWorkspace;
          if (!cur) return { ...p, gridLoading: false };
          return { ...p, gridLoading: false, gridTotal: page.total ?? 0, gridSort: sort, activeWorkspace: { ...cur, sourceRows: pageToRows(page), sourceCols: page.columns } };
        });
      })
      .catch(() => setState({ gridLoading: false }));
  };

  const refreshLivePipeline = (workspaceId: string, pipeline: Step[], selectedIndex: number) => {
    const steps = toBackendSteps(pipeline);
    api.pipelineSave(workspaceId, steps).catch(() => {});
    api
      .pipelineCounts(workspaceId, steps)
      .then((counts) => {
        const sc: Record<string, number | null> = {};
        const se: Record<string, string | null> = {};
        counts.forEach((c) => { sc[c.step_id] = c.row_count; se[c.step_id] = c.error; });
        setState({ stepCounts: sc, stepErrors: se });
      })
      .catch(() => {});
    fetchStepPreview(workspaceId, pipeline, selectedIndex, []);
  };

  // ---- Phase 9: column stats popover (P9.12) ----
  const openColumnStats = (column: string, x: number, y: number) => {
    if (!st.openWorkspaceId) return;
    setState({ statsCol: column, statsData: null, statsLoading: true, statsAnchor: { x, y } });
    api
      .columnStats(st.openWorkspaceId, column)
      .then((d) => setState({ statsData: d, statsLoading: false }))
      .catch(() => setState({ statsLoading: false }));
  };
  const closeColumnStats = () => setState({ statsCol: null, statsData: null, statsAnchor: null });

  // ---- Phase 9: header AutoFilter (P9.11) ----
  const openHeaderFilter = (column: string, x: number, y: number) => {
    if (!st.openWorkspaceId) return;
    setState({ headerFilterCol: column, headerFilterValues: [], headerFilterSelected: new Set(), headerFilterLoading: true, headerFilterAnchor: { x, y } });
    api
      .columnDistinctValues(st.openWorkspaceId, column, 200)
      .then((d) => setState({ headerFilterValues: d.values, headerFilterSelected: new Set(d.values.map((v) => String(v.value ?? "∅"))), headerFilterLoading: false }))
      .catch(() => setState({ headerFilterLoading: false }));
  };
  const closeHeaderFilter = () => setState({ headerFilterCol: null, headerFilterValues: [], headerFilterAnchor: null });
  const toggleHeaderFilterValue = (v: string) =>
    setState((p) => {
      const next = new Set(p.headerFilterSelected);
      next.has(v) ? next.delete(v) : next.add(v);
      return { headerFilterSelected: next };
    });
  const applyHeaderFilter = () => {
    if (!st.openWorkspaceId || !st.activeWorkspace || !st.headerFilterCol) return;
    const allValues = st.headerFilterValues.map((v) => String(v.value ?? "∅"));
    const selected = Array.from(st.headerFilterSelected);
    if (selected.length === allValues.length) { closeHeaderFilter(); return; } // nothing excluded, no-op
    const id = "filter_" + Date.now();
    const config = { conditions: [{ column: st.headerFilterCol, op: "in_list", value: selected.join(",") }], combine: "and" };
    const step: Step = { id, type: "filter", summary: backendSummary("filter", config), config };
    const pipeline = [...st.activeWorkspace.pipeline, step];
    const idx = pipeline.length - 1;
    setState({ activeWorkspace: { ...st.activeWorkspace, pipeline }, selectedStepIndex: idx, headerFilterCol: null, headerFilterAnchor: null });
    refreshLivePipeline(st.openWorkspaceId, pipeline, idx);
  };

  // ---- Phase 9: manual cell editing (P9.15) ----
  const startCellEdit = (rowIdx: number, column: string, currentValue: unknown) => {
    if (!live || !st.openWorkspaceId || st.rowKey.length === 0) return;
    setState({ editingCell: { rowIdx, column }, editingCellValue: currentValue == null ? "" : String(currentValue) });
  };
  const cancelCellEdit = () => setState({ editingCell: null, editingCellValue: "" });
  const commitCellEdit = () => {
    if (!st.openWorkspaceId || !st.activeWorkspace || !st.editingCell) return;
    const ws = st.activeWorkspace;
    const { rowIdx, column } = st.editingCell;
    const row = ws.sourceRows?.[rowIdx];
    if (!row || st.rowKey.length === 0) { cancelCellEdit(); return; }
    const keyVals: Record<string, unknown> = {};
    st.rowKey.forEach((k) => (keyVals[k] = (row as any)[k]));
    const newValue = st.editingCellValue;

    // Find (or create, always at the end) a manual_edit step and append this edit.
    let pipeline = [...ws.pipeline];
    let idx = pipeline.findIndex((s) => s.type === "manual_edit");
    if (idx < 0) {
      const step: Step = { id: "manual_edit_" + Date.now(), type: "manual_edit", summary: "0 cells edited", config: { keys: st.rowKey, edits: [] } };
      pipeline = [...pipeline, step];
      idx = pipeline.length - 1;
    } else if (idx !== pipeline.length - 1) {
      // keep manual edits as the final step so they always apply last
      const [step] = pipeline.splice(idx, 1);
      pipeline.push(step);
      idx = pipeline.length - 1;
    }
    const step = pipeline[idx];
    const edits = [...(step.config?.edits ?? [])].filter((e: any) => !(e.column === column && JSON.stringify(e.key) === JSON.stringify(keyVals)));
    edits.push({ key: keyVals, column, value: newValue });
    const config = { keys: st.rowKey, edits };
    pipeline[idx] = { ...step, config, summary: `${edits.length} cell${edits.length === 1 ? "" : "s"} edited` };

    setState({ activeWorkspace: { ...ws, pipeline }, editingCell: null, editingCellValue: "", selectedStepIndex: idx });
    refreshLivePipeline(st.openWorkspaceId, pipeline, idx);
  };

  // ---- Phase 6: row identity, diff, validation ----
  const setRowKey = (col: string) => {
    if (!st.openWorkspaceId) return;
    const keys = col ? [col] : [];
    setState({ rowKey: keys, rowKeyUnique: null });
    if (keys.length) api.rowKeySet(st.openWorkspaceId, keys).then((r) => setState({ rowKeyUnique: r.unique })).catch(() => {});
  };

  const openReviewDiff = () => {
    if (live && st.openWorkspaceId && st.activeWorkspace) {
      if (st.rowKey.length === 0) {
        setState({ authError: "Pick a row identifier (top-right) before reviewing changes." });
        return;
      }
      setState({ modal: "reviewDiff", diffResult: null, diffError: null });
      api
        .diffCompute(st.openWorkspaceId, toBackendSteps(st.activeWorkspace.pipeline), st.rowKey)
        .then((d) => setState({ diffResult: d }))
        .catch((e) => setState({ diffError: String(e) }));
      return;
    }
    setState({ modal: "reviewDiff" });
  };

  const openValidate = () => {
    const cols = st.activeWorkspace?.sourceCols ?? [];
    const rules = st.validateRules.length ? st.validateRules : [{ id: "r_" + Date.now(), column: cols[0] ?? "", kind: "not_null", value: "", severity: "error" }];
    setState({ modal: "validate", validateRules: rules, validateResult: null });
  };
  const addValidateRule = () =>
    setState((p) => ({ validateRules: [...p.validateRules, { id: "r_" + Date.now(), column: (p.activeWorkspace?.sourceCols ?? [])[0] ?? "", kind: "not_null", value: "", severity: "error" }] }));
  const setRuleField = (idx: number, field: string, val: string) =>
    setState((p) => ({ validateRules: p.validateRules.map((r, i) => (i === idx ? { ...r, [field]: val } : r)) }));
  const removeRule = (idx: number) => setState((p) => ({ validateRules: p.validateRules.filter((_, i) => i !== idx) }));
  const runValidation = () => {
    if (!st.openWorkspaceId || !st.activeWorkspace) return;
    setState({ validateResult: null });
    api
      .validationRun(st.openWorkspaceId, toBackendSteps(st.activeWorkspace.pipeline), st.validateRules)
      .then((r) => setState({ validateResult: r }))
      .catch((e) => setState({ diffError: String(e) }));
  };

  // ---- Pipeline ----
  const selectStep = (i: number) => {
    setState({ selectedStepIndex: i, validateShowFailedOnly: false });
    if (live && st.openWorkspaceId && st.activeWorkspace) fetchStepPreview(st.openWorkspaceId, st.activeWorkspace.pipeline, i, []);
  };

  // Data tab always reflects the pipeline's final output; Workflow tab lets
  // the user pick any step. Switching to Data snaps back to the last step.
  const setWorkspaceTab = (tab: "data" | "explore" | "workflow") => {
    setState({ workspaceTab: tab });
    if (tab === "data" && st.activeWorkspace) selectStep(st.activeWorkspace.pipeline.length - 1);
  };

  // A step's input defaults to the step immediately before it in the list —
  // the original linear chain. Any step can instead point at any *earlier*
  // step's id, branching the pipeline into a tree (multiple steps can read
  // from the same ancestor).
  const defaultInputId = (pipeline: Step[], i: number): string | null => (i > 0 ? pipeline[i - 1].id : null);
  const resolvedInputId = (pipeline: Step[], i: number): string | null => pipeline[i].inputId ?? defaultInputId(pipeline, i);
  const stepDisplayLabel = (pipeline: Step[], id: string | null): string => {
    if (!id) return "Source";
    const s = pipeline.find((p) => p.id === id);
    if (!s) return id;
    return s.type === "source" ? "Source" : STEP_LABELS[s.type] || s.type;
  };
  const setStepInput = (stepId: string, inputId: string) => {
    if (!st.openWorkspaceId || !st.activeWorkspace) return;
    const ws = st.activeWorkspace;
    const pipeline = ws.pipeline.map((s) => (s.id === stepId ? { ...s, inputId: inputId || null } : s));
    const idx = pipeline.findIndex((s) => s.id === stepId);
    setState({ activeWorkspace: { ...ws, pipeline } });
    refreshLivePipeline(st.openWorkspaceId, pipeline, idx >= 0 ? idx : st.selectedStepIndex);
  };

  const removeStep = (id: string) => {
    if (live && st.openWorkspaceId && st.activeWorkspace) {
      const ws = st.activeWorkspace;
      const idx = ws.pipeline.findIndex((x) => x.id === id);
      if (idx <= 0) return;
      const pipeline = ws.pipeline.filter((x) => x.id !== id);
      const newSel = Math.min(st.selectedStepIndex, pipeline.length - 1);
      setState({ activeWorkspace: { ...ws, pipeline }, selectedStepIndex: newSel });
      refreshLivePipeline(st.openWorkspaceId, pipeline, newSel);
      return;
    }
    setSt((p) => {
      const ws = p.activeWorkspace;
      if (!ws) return p;
      const idx = ws.pipeline.findIndex((x) => x.id === id);
      if (idx <= 0) return p;
      const pipeline = ws.pipeline.filter((x) => x.id !== id);
      return { ...p, activeWorkspace: { ...ws, pipeline }, selectedStepIndex: Math.min(p.selectedStepIndex, pipeline.length - 1) };
    });
  };

  const toggleAddStep = () => setState((p) => ({ addStepOpen: !p.addStepOpen }));

  const addStepType = (type: string) => {
    if (live && st.openWorkspaceId && st.activeWorkspace) {
      const ws = st.activeWorkspace;
      const cols = ws.sourceCols ?? [];
      const id = type + "_" + Date.now();
      const config = defaultStepConfig(type, cols);
      const step: Step = { id, type, summary: backendSummary(type, config), config };
      const pipeline = [...ws.pipeline, step];
      const idx = pipeline.length - 1;
      setState({ activeWorkspace: { ...ws, pipeline }, selectedStepIndex: idx, addStepOpen: false, modal: LIVE_MODAL_FOR[type] ?? null, modalStepId: id, modalConfig: configToModal(type, config), modalPreview: null });
      if (type === "join_file") loadLocalSources();
      if (type === "replace") loadReplaceDistinct(config.column);
      refreshLivePipeline(st.openWorkspaceId, pipeline, idx);
      scheduleModalPreview();
      return;
    }
    setSt((p) => {
      const ws = p.activeWorkspace;
      if (!ws) return p;
      const id = type + "_" + Date.now();
      const step: Step = { id, type, summary: stepSummary(type) };
      const pipeline = [...ws.pipeline, step];
      const modal = (MODAL_FOR_TYPE[type] as ModalKind) || null;
      return { ...p, activeWorkspace: { ...ws, pipeline }, selectedStepIndex: pipeline.length - 1, addStepOpen: false, modal, modalStepId: id, modalConfig: modalConfigForType(type) };
    });
  };

  const openStepModal = (step: Step) => {
    if (live && st.openWorkspaceId) {
      const modal = LIVE_MODAL_FOR[step.type];
      if (!modal) return;
      setState({ modal, modalStepId: step.id, modalConfig: configToModal(step.type, step.config ?? {}), modalPreview: null });
      if (step.type === "join_file") loadLocalSources();
      if (step.type === "replace") loadReplaceDistinct((step.config ?? {}).column);
      scheduleModalPreview();
      return;
    }
    const modal = MODAL_FOR_TYPE[step.type] as ModalKind;
    if (!modal) return;
    setState({ modal, modalStepId: step.id, modalConfig: modalConfigForType(step.type) });
  };

  const loadReplaceDistinct = (column: string | undefined) => {
    if (!st.openWorkspaceId || !column) {
      setState({ replaceDistinct: [] });
      return;
    }
    setState({ replaceDistinctLoading: true });
    api
      .columnDistinctValues(st.openWorkspaceId, column, 30)
      .then((d) => setState({ replaceDistinct: d.values, replaceDistinctLoading: false }))
      .catch(() => setState({ replaceDistinct: [], replaceDistinctLoading: false }));
  };

  const closeModal = () => setState({ modal: null, modalStepId: null, modalPreview: null, replaceDistinct: [] });
  const backdropClick = () => {
    if (st.modal !== "committing") closeModal();
  };
  const modalPreviewTimer = useRef<number | null>(null);

  // Live before/after preview (P9.17): recompute on every modal edit, debounced.
  const scheduleModalPreview = () => {
    if (!(live && st.openWorkspaceId && st.activeWorkspace && st.modalStepId)) return;
    if (modalPreviewTimer.current) window.clearTimeout(modalPreviewTimer.current);
    modalPreviewTimer.current = window.setTimeout(() => {
      setSt((p) => {
        const ws = p.activeWorkspace;
        if (!ws || !p.modalStepId) return p;
        const idx = ws.pipeline.findIndex((x) => x.id === p.modalStepId);
        if (idx < 0) return p;
        const prevStep = ws.pipeline[idx];
        const draftConfig = modalToConfig(p.modal, p.modalConfig, prevStep.config);
        const draftPipeline = ws.pipeline.map((x, i) => (i === idx ? { ...x, config: draftConfig } : x));
        const steps = toBackendSteps(draftPipeline);
        const beforeTotal = idx > 0 ? p.stepCounts[ws.pipeline[idx - 1].id] ?? 0 : p.gridTotal;
        api
          .previewStep(st.openWorkspaceId!, steps, idx, 0, 5, [])
          .then((page) => {
            setState({
              modalPreview: { rows: pageToRows(page), cols: page.columns, totalBefore: beforeTotal ?? 0, totalAfter: page.total ?? 0 },
              modalPreviewLoading: false,
            });
          })
          .catch(() => setState({ modalPreviewLoading: false }));
        return { ...p, modalPreviewLoading: true };
      });
    }, 350);
  };

  const updateModalConfig = (patch: any) => {
    setState((p) => ({ modalConfig: { ...p.modalConfig, ...patch } }));
    scheduleModalPreview();
  };

  const addFilterCondition = () => {
    setState((p) => ({ modalConfig: { ...p.modalConfig, conditions: [...p.modalConfig.conditions, { column: "country", operator: "equals", value: "" }] } }));
    scheduleModalPreview();
  };
  const setCondField = (idx: number, field: string, val: string) => {
    setState((p) => ({
      modalConfig: { ...p.modalConfig, conditions: p.modalConfig.conditions.map((c: any, i: number) => (i === idx ? { ...c, [field]: val } : c)) },
    }));
    scheduleModalPreview();
  };
  const setMappingField = (idx: number, field: string, val: string) => {
    setState((p) => ({
      modalConfig: { ...p.modalConfig, mappings: (p.modalConfig.mappings || []).map((m: any, i: number) => (i === idx ? { ...m, [field]: val } : m)) },
    }));
    scheduleModalPreview();
  };
  const addMapping = () => setState((p) => ({ modalConfig: { ...p.modalConfig, mappings: [...(p.modalConfig.mappings || []), { from: "", to: "" }] } }));

  // Replace-values: pick real distinct values instead of typing them (P9.19).
  const toggleReplaceSelect = (value: string) =>
    setState((p) => {
      const sel: string[] = p.modalConfig._selected || [];
      const next = sel.includes(value) ? sel.filter((v) => v !== value) : [...sel, value];
      return { modalConfig: { ...p.modalConfig, _selected: next } };
    });
  const mergeSelectedInto = () => {
    const sel: string[] = st.modalConfig._selected || [];
    const target = st.modalConfig._mergeTarget || "";
    if (!sel.length || !target) return;
    setState((p) => ({
      modalConfig: {
        ...p.modalConfig,
        mappings: [...(p.modalConfig.mappings || []).filter((m: any) => m.from), ...sel.filter((v) => v !== target).map((v) => ({ from: v, to: target }))],
        _selected: [],
        _mergeTarget: "",
      },
    }));
    scheduleModalPreview();
  };

  const applyStepConfig = (summaryText: string) =>
    setSt((p) => {
      const ws = p.activeWorkspace;
      if (!ws) return { ...p, modal: null, modalStepId: null };
      const pipeline = ws.pipeline.map((x) => (x.id === p.modalStepId ? { ...x, summary: summaryText } : x));
      return { ...p, activeWorkspace: { ...ws, pipeline }, modal: null, modalStepId: null };
    });

  const applyModal = () => {
    if (live && st.openWorkspaceId && st.activeWorkspace) {
      const ws = st.activeWorkspace;
      const step = ws.pipeline.find((x) => x.id === st.modalStepId);
      const config = modalToConfig(st.modal, st.modalConfig, step?.config);
      const pipeline = ws.pipeline.map((x) => (x.id === st.modalStepId ? { ...x, config, summary: backendSummary(x.type, config) } : x));
      const idx = pipeline.findIndex((x) => x.id === st.modalStepId);
      setState({ activeWorkspace: { ...ws, pipeline }, modal: null, modalStepId: null });
      refreshLivePipeline(st.openWorkspaceId, pipeline, idx >= 0 ? idx : st.selectedStepIndex);
      return;
    }
    const c = st.modalConfig;
    if (st.modal === "filter") applyStepConfig(c.conditions.map((cn: any) => `${cn.column} ${cn.operator} ${cn.value}`).join(" AND "));
    else if (st.modal === "join") applyStepConfig(`${c.file} · ${c.leftKey}=${c.rightKey} · ${c.joinType} join`);
    else if (st.modal === "dedupe") applyStepConfig(`key: ${c.key} · keep ${c.keep}`);
    else if (st.modal === "replace") applyStepConfig(c.mappings.map((m: any) => `${m.from}→${m.to}`).join(", "));
    else if (st.modal === "validate") {
      const { validCount, invalidCount } = computeValidation(c.rules);
      applyStepConfig(`${validCount} valid · ${invalidCount} invalid`);
    }
  };

  const toggleFailedOnly = () => setState((p) => ({ validateShowFailedOnly: !p.validateShowFailedOnly }));

  const openCommitTarget = () => {
    if (live && st.openWorkspaceId && st.activeWorkspace) {
      const src = st.sourceTable || st.activeWorkspace.pipeline[0]?.summary || "";
      const parts = src.split(".");
      // Columns the pipeline added (join/formula) vs the source schema — these
      // can only be kept by writing a new table.
      const added = (st.activeWorkspace.sourceCols ?? []).filter((c) => !st.rowKeyColumns.includes(c));
      const target: "existing" | "new" = added.length ? "new" : "existing";
      setState({
        modal: "commitTarget",
        commitTarget: target,
        commitAddedCols: added,
        commitExistingTable: src,
        commitNewCatalog: parts[0] || "",
        commitNewSchema: parts[1] || "",
        commitNewTableName: (parts[2] || "table") + "_v2",
        commitChecks: null,
      });
      api.commitReadiness(st.openWorkspaceId, toBackendSteps(st.activeWorkspace.pipeline), st.rowKey).then((r) => setState({ commitChecks: r.checks })).catch(() => {});
      if (st.browse.catalogs.length === 0) loadCatalogs();
      if (parts[0]) ensureSchemasLoaded(parts[0]);
      return;
    }
    setSt((p) => {
      const ws = p.activeWorkspace;
      const sourceTable = (ws && ws.pipeline[0] && ws.pipeline[0].summary) || "main.crm.customers";
      return { ...p, modal: "commitTarget", commitTarget: "existing", commitExistingTable: sourceTable, commitNewCatalog: "main", commitNewSchema: "crm", commitNewTableName: "" };
    });
  };
  const fixSchema = () => setState({ modal: null });
  const proposeNewTable = () =>
    setState((p) => {
      const parts = (p.commitExistingTable || "main.crm.customers").split(".");
      return { commitTarget: "new", commitNewCatalog: parts[0] || "main", commitNewSchema: parts[1] || "crm", commitNewTableName: (parts[2] || "table") + "_v2" };
    });
  const continueToReadyCommit = () => {
    if (live && st.commitTarget === "new") {
      // Verify the destination catalog/schema exists before committing.
      setState({ commitNs: null, commitNsError: null, commitNsBusy: true });
      api
        .namespaceCheck(st.commitNewCatalog, st.commitNewSchema)
        .then((ns) => {
          if (ns.catalog_exists && ns.schema_exists) setState({ modal: "readyCommit", commitNsBusy: false });
          else setState({ commitNs: ns, commitNsBusy: false });
        })
        .catch((e) => setState({ commitNsError: String(e), commitNsBusy: false }));
      return;
    }
    setState({ modal: "readyCommit" });
  };

  const createNamespace = () => {
    setState({ commitNsBusy: true, commitNsError: null });
    api
      .namespaceCreate(st.commitNewCatalog, st.commitNewSchema, !st.commitNs?.catalog_exists, !st.commitNs?.schema_exists)
      .then(() => setState({ modal: "readyCommit", commitNs: null, commitNsBusy: false }))
      .catch((e) => setState({ commitNsError: String(e), commitNsBusy: false }));
  };

  const commit = () => {
    if (live && st.openWorkspaceId && st.activeWorkspace) {
      const createNew = st.commitTarget === "new";
      const target = createNew ? `${st.commitNewCatalog}.${st.commitNewSchema}.${st.commitNewTableName}` : st.commitExistingTable;
      setState({ modal: "committing", commitResult: null, commitConflict: null, commitMsg: "Building change set…" });
      api
        .commitStart(st.openWorkspaceId, toBackendSteps(st.activeWorkspace.pipeline), st.rowKey, target, createNew)
        .then(({ operation_id }) => {
          const poll = () => {
            api
              .commitStatus(operation_id)
              .then((stt) => {
                if (stt.state === "COMMITTED") {
                  setState((p) => ({ modal: "committed", commitResult: stt.result, activeWorkspace: p.activeWorkspace ? { ...p.activeWorkspace, committed: true } : p.activeWorkspace }));
                } else if (stt.state === "CONFLICT") {
                  setState({ modal: "committed", commitConflict: { base: stt.base_version ?? 0, current: stt.current_version ?? 0 } });
                } else if (stt.state === "FAILED") {
                  setState({ modal: "committed", commitMsg: stt.error || "commit failed" });
                } else {
                  setState({ commitMsg: stateLabel(stt.state) });
                  window.setTimeout(poll, 700);
                }
              })
              .catch((e) => setState({ modal: "committed", commitMsg: String(e) }));
          };
          poll();
        })
        .catch((e) => setState({ modal: "committed", commitMsg: String(e) }));
      return;
    }
    setState({ modal: "committing" });
    setTimeout(() => {
      setSt((p) => {
        const ws = p.activeWorkspace!;
        return { ...p, modal: "committed", activeWorkspace: { ...ws, committed: true, version: ws.version + 1, changes: 0 } };
      });
    }, 1400);
  };
  const stateLabel = (s: string) =>
    ({ "checking version": "Checking source version…", staging: "Staging change set to volume…", merging: "Running MERGE in Databricks…", PENDING: "Preparing…" } as Record<string, string>)[s] || s;
  const finishCommit = () => setState({ modal: null });

  const useDetectedFile = () =>
    setSt((p) => {
      const ws = p.activeWorkspace || buildExistingWorkspace();
      const id = "join_wf_" + Date.now();
      const step: Step = { id, type: "join_file", summary: stepSummary("join_file") };
      const pipeline = [...ws.pipeline, step];
      return {
        ...p,
        activeWorkspace: { ...ws, pipeline },
        selectedStepIndex: pipeline.length - 1,
        screen: "workspace",
        modal: "join",
        modalStepId: id,
        modalConfig: { file: "customer_mapping.xlsx", leftKey: "customer_id", rightKey: "customer_id", joinType: "left" },
      };
    });

  // ================= DERIVED =================
  const ws = st.activeWorkspace;
  const screen = st.screen;
  const nf = st.newFlow;
  const mc = st.modalConfig || {};
  const cp = st.checkoutProgress || 0;

  const ramLabel = st.ramMB / 1024 < 1 ? st.ramMB + " MB" : (st.ramMB / 1024).toFixed(1) + " GB";
  const windowTitle = "PyJama" + (ws ? " — " + ws.name : "");

  const checkedCount = Object.values(nf.columns).filter(Boolean).length;
  const estimatedSizeLabel = fmtNum(182 + Math.max(0, checkedCount - 5) * 15) + " MB";

  let checkoutStatusText = "Preparing…";
  if (cp < 30) checkoutStatusText = "☁ Preparing working set in Databricks";
  else if (cp < 100) checkoutStatusText = `Downloading • ${cp}%`;
  else checkoutStatusText = "Finalizing checkout locally";

  const selIdx = st.selectedStepIndex;
  const selectedStep = ws ? ws.pipeline[selIdx] : null;
  const selectedStepIsValidate = !!(selectedStep && selectedStep.type === "validate");

  const isLiveWs = live && !!st.openWorkspaceId && !!ws;
  const colOptions = isLiveWs ? ws!.sourceCols ?? [] : BASE_COLS;
  const slice = ws ? ws.pipeline.slice(0, selIdx + 1) : [];
  // Live: the grid shows the server-compiled window (ws.sourceRows/Cols already
  // reflect the selected step). Mock: compute transforms in JS.
  const { rows, cols } = isLiveWs
    ? { rows: ws!.sourceRows ?? [], cols: ws!.sourceCols ?? [] }
    : ws
    ? computeRows(slice, ws.sourceRows, ws.sourceCols)
    : { rows: [], cols: [] };
  let displayRows = rows;
  if (selectedStepIsValidate && st.validateShowFailedOnly) displayRows = rows.filter((r) => !r.email || !r.email.includes("@"));

  const finalCols = ws ? computeRows(ws.pipeline, ws.sourceRows, ws.sourceCols).cols : [];
  const targetSchema = TABLE_SCHEMAS[st.commitExistingTable] || null;
  const missingCols = targetSchema ? finalCols.filter((c) => !targetSchema.includes(c)) : [];
  const schemaIncompatible = !!(targetSchema && missingCols.length > 0);
  const commitNewFullName = `${st.commitNewCatalog}.${st.commitNewSchema}.${st.commitNewTableName || "new_table"}`;
  const commitTargetLabel = st.commitTarget === "new" ? commitNewFullName : st.commitExistingTable;

  const modalTitles: Record<string, string> = {
    filter: "Filter Rows", join: "Join Local File", dedupe: "Remove Duplicates", replace: "Replace Values", formula: "Formula",
    validate: "Validation Rules", reviewDiff: "Review Changes", commitTarget: isLiveWs ? "Publish" : "Commit Destination",
    readyCommit: isLiveWs ? "Ready to Publish" : "Ready to Commit", committing: "", committed: "",
  };

  let dedupeInput = RAW_ROWS.length, dedupeDup = 0, dedupeOutput = RAW_ROWS.length;
  if (st.modal === "dedupe" && mc.key) {
    const seen: Record<string, boolean> = {};
    let dup = 0;
    for (const r of RAW_ROWS) { if (seen[(r as any)[mc.key]]) dup++; else seen[(r as any)[mc.key]] = true; }
    dedupeDup = dup; dedupeOutput = RAW_ROWS.length - dup;
  }
  const validation = st.modal === "validate" ? computeValidation(mc.rules) : { validCount: 0, invalidCount: 0 };

  const modalWidth =
    st.modal === "reviewDiff" ? "640px" : st.modal === "validate" && isLiveWs ? "620px" : st.modal === "commitTarget" ? "500px" : st.modal === "committing" || st.modal === "committed" ? "380px" : "460px";
  const showModalFooter = ["filter", "join", "dedupe", "replace", "formula", "validate"].includes(st.modal || "") && !(isLiveWs && st.modal === "validate");

  // Shared between the Data tab (full-width, always the final step) and the
  // Workflow tab's step preview (whichever step is selected in the pipeline).
  const renderDataGrid = () => (
    <>
      {selectedStep && (
        <div style={s("border-bottom:1px solid #E5E5ED;background:#FBFAFF;padding:12px 20px;display:flex;align-items:center;justify-content:space-between;")}>
          <div style={s("font-size:12.5px;color:#6B6B7A;")}>
            {selectedStep.type === "source" ? `Source · ${selectedStep.summary}` : `${STEP_LABELS[selectedStep.type] || selectedStep.type} · ${selectedStep.summary}`}
          </div>
          {selectedStepIsValidate && (
            <div onClick={toggleFailedOnly} style={s("display:flex;align-items:center;gap:6px;font-size:12px;color:#6B6B7A;cursor:pointer;")}>
              <div style={s(box(st.validateShowFailedOnly))} /> Show only failed rows
            </div>
          )}
        </div>
      )}

      <div
        style={s("flex:1;min-height:0;overflow:auto;")}
        onScroll={
          live && st.openWorkspaceId
            ? (e) => {
                const el = e.currentTarget;
                if (el.scrollHeight - el.scrollTop - el.clientHeight < 240) loadMoreRows();
              }
            : undefined
        }
      >
        {(() => {
          const colW = (c: string) => st.gridColWidths[c] ?? DEFAULT_COL_W;
          const tableW = cols.reduce((sum, c) => sum + colW(c), 0);
          return (
        <table style={{ ...s("border-collapse:collapse;font-size:13px;table-layout:fixed;"), width: tableW, minWidth: "100%" }}>
          <colgroup>
            {cols.map((c) => (
              <col key={c} style={{ width: colW(c) }} />
            ))}
          </colgroup>
          <thead>
            <tr style={s("position:sticky;top:0;background:#FFFFFF;box-shadow:0 1px 0 #E5E5ED;")}>
              {cols.map((c) => {
                const sortable = live && !!st.openWorkspaceId;
                const active = st.gridSort[0]?.column === c;
                const arrow = active ? (st.gridSort[0].direction === "asc" ? " ▲" : " ▼") : "";
                const filterActive = isLiveWs && ws!.pipeline.some((p) => p.type === "filter" && (p.config?.conditions || []).some((cc: any) => cc.column === c && cc.op === "in_list"));
                return (
                  <th
                    key={c}
                    style={s(`position:relative;text-align:left;padding:10px 30px 10px 16px;font-weight:600;color:${active ? "#7A2BF5" : "#6B6B7A"};font-size:11.5px;letter-spacing:0.02em;white-space:normal;overflow-wrap:anywhere;vertical-align:top;`)}
                  >
                    <span onClick={sortable ? () => toggleSort(c) : undefined} style={s(sortable ? "cursor:pointer;user-select:none;" : "")}>{c}{arrow}</span>
                    {isLiveWs && (
                      <span
                        onClick={(e) => { e.stopPropagation(); openHeaderFilter(c, e.clientX, e.clientY); }}
                        title="Filter by value"
                        style={s(`margin-left:6px;cursor:pointer;color:${filterActive ? "#7A2BF5" : "#C4C4CE"};font-size:10px;`)}
                      >
                        ▾
                      </span>
                    )}
                    {isLiveWs && (
                      <span
                        onClick={(e) => { e.stopPropagation(); openColumnStats(c, e.clientX, e.clientY); }}
                        title="Column stats"
                        style={s("margin-left:4px;cursor:pointer;color:#C4C4CE;font-size:10px;")}
                      >
                        ⓘ
                      </span>
                    )}
                    <div
                      onMouseDown={(e) => startColResize(c, e)}
                      onClick={(e) => e.stopPropagation()}
                      title="Drag to resize"
                      style={s("position:absolute;top:0;right:0;width:7px;height:100%;cursor:col-resize;")}
                    />
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {displayRows.map((r, ri) => (
              <Hv key={ri} tag="tr" css="border-bottom:1px solid #F0F0F5;" hover="background:#FBFAFF;">
                {cols.map((c) => {
                  const val = (r as any)[c];
                  const isEmpty = val === undefined || val === null || val === "";
                  const text = isEmpty ? "—" : typeof val === "number" ? fmtNum(val) : String(val);
                  const isEditing = isLiveWs && st.editingCell && st.editingCell.rowIdx === ri && st.editingCell.column === c;
                  const editable = isLiveWs && st.rowKey.length > 0 && st.selectedStepIndex === (ws!.pipeline.length - 1);
                  if (isEditing) {
                    return (
                      <td key={c} style={s("padding:2px 6px;")}>
                        <input
                          autoFocus
                          value={st.editingCellValue}
                          onChange={(e) => setState({ editingCellValue: e.target.value })}
                          onBlur={commitCellEdit}
                          onKeyDown={(e) => { if (e.key === "Enter") commitCellEdit(); if (e.key === "Escape") cancelCellEdit(); }}
                          style={s(`width:100%;height:28px;border:1.5px solid #7A2BF5;border-radius:4px;padding:0 6px;font-size:13px;box-sizing:border-box;font-family:${typeof val === "number" ? MONO : "inherit"};`)}
                        />
                      </td>
                    );
                  }
                  return (
                    <td
                      key={c}
                      onDoubleClick={editable ? () => startCellEdit(ri, c, val) : undefined}
                      title={editable ? "Double-click to edit" : undefined}
                      style={s(`padding:8px 16px;white-space:normal;overflow-wrap:anywhere;vertical-align:top;color:${isEmpty ? "#C4C4CE" : "#17171F"};font-style:${isEmpty ? "italic" : "normal"};font-family:${typeof val === "number" ? MONO : "inherit"};${editable ? "cursor:text;" : ""}`)}
                    >
                      {text}
                    </td>
                  );
                })}
              </Hv>
            ))}
          </tbody>
        </table>
          );
        })()}
      </div>

      <div style={s("height:32px;min-height:32px;border-top:1px solid #E5E5ED;background:#FFFFFF;display:flex;align-items:center;gap:8px;padding:0 16px;font-size:12px;color:#6B6B7A;")}>
        {live && st.openWorkspaceId ? (
          <>
            <span>Showing {(ws?.sourceRows?.length ?? 0).toLocaleString()} of {st.gridTotal.toLocaleString()} rows</span>
            <span>·</span>
            <span style={s("color:#7A2BF5;")}>Encrypted local · windowed</span>
            {st.gridLoading && <span style={s("color:#9C9CAA;")}>· loading…</span>}
          </>
        ) : (
          <>
            <span>{ws?.rowCountLabel} rows</span><span>·</span><span>{ws?.sizeLabel}</span><span>·</span><span style={s("color:#7A2BF5;")}>Executing locally</span>
          </>
        )}
      </div>
    </>
  );

  // No-AI visual analysis builder (Phase 11, P11.8-P11.10) — group/measure/
  // filter/sort, rendered as editable tokens (§12), with a deterministic
  // visualization per the AnalysisSpec's visualization_hint (§23).
  const renderAnalysisBuilder = () => {
    const cols = st.activeWorkspace?.sourceCols ?? [];
    const spec = st.analysisSpec;
    const result = st.analysisResult;
    const hasSpec = spec.dimensions.length > 0 || spec.measures.length > 0 || spec.filters.length > 0;
    const drill = st.analysisDrillRow;
    const drillCol = spec.dimensions[0];
    const drillValue = drill && result ? result.rows[drill.rowIndex]?.[0] : undefined;
    const editingFilterIdx = st.analysisEditingFilter;
    return (
      <>
      <div style={s("border:1px solid #E4E1ED;border-radius:12px;padding:20px;background:#FFFFFF;")}>
        {/* Editable analytical tokens (§12) — one unified row, mixing filter
            (purple), group (green), and measure (orange) tokens, matching
            the design's [age > 60] [group: gender] [avg: claim_amount] row. */}
        <div style={s("display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:14px;")}>
          {spec.filters.map((f, i) => (
            <div key={`f${i}`} style={s("position:relative;")}>
              <div
                onClick={() => setState({ analysisEditingFilter: editingFilterIdx === i ? null : i })}
                style={s("display:flex;align-items:center;gap:6px;font-size:12px;background:#F1EDFF;color:#7A2BF5;border-radius:14px;padding:4px 6px 4px 10px;cursor:pointer;font-family:" + MONO + ";")}
              >
                {f.column} {f.operator} {["is_null", "is_not_null"].includes(f.operator) ? "" : String(f.value ?? "")}
                <span onClick={(e) => { e.stopPropagation(); removeAnalysisFilter(i); }} style={s("color:#7A2BF5;cursor:pointer;")}>✕</span>
              </div>
              {editingFilterIdx === i && (
                <>
                  <div style={s("position:fixed;inset:0;z-index:19;")} onClick={() => setState({ analysisEditingFilter: null })} />
                  <div style={s("position:absolute;top:30px;left:0;background:#FFFFFF;border:1px solid #E4E1ED;border-radius:10px;box-shadow:0 12px 32px -8px rgba(20,20,40,0.18);padding:10px;z-index:20;display:flex;gap:6px;align-items:center;")}>
                    <select value={f.column} onChange={(e) => setAnalysisFilterField(i, "column", e.target.value)} style={s("height:30px;border:1px solid #E5E5ED;border-radius:6px;padding:0 6px;font-size:12px;")}>
                      {cols.map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                    <select value={f.operator} onChange={(e) => setAnalysisFilterField(i, "operator", e.target.value)} style={s("height:30px;border:1px solid #E5E5ED;border-radius:6px;padding:0 6px;font-size:12px;")}>
                      {["=", "!=", ">", "<", ">=", "<=", "contains", "is_null", "is_not_null"].map((o) => <option key={o} value={o}>{o}</option>)}
                    </select>
                    {!["is_null", "is_not_null"].includes(f.operator) && (
                      <input autoFocus value={String(f.value ?? "")} onChange={(e) => setAnalysisFilterField(i, "value", e.target.value)} style={s("height:30px;border:1px solid #E5E5ED;border-radius:6px;padding:0 8px;font-size:12px;width:110px;")} />
                    )}
                  </div>
                </>
              )}
            </div>
          ))}
          {spec.dimensions.map((d) => (
            <div key={`d${d}`} onClick={() => toggleDimension(d)} style={s("display:flex;align-items:center;gap:6px;font-size:12px;background:#EAFBF3;color:#00925A;border-radius:14px;padding:4px 6px 4px 10px;cursor:pointer;font-family:" + MONO + ";")}>
              group: {d}<span style={s("color:#00925A;")}>✕</span>
            </div>
          ))}
          {spec.measures.map((m, i) => (
            <div key={`m${i}`} style={s("display:flex;align-items:center;gap:4px;font-size:12px;background:#FFF2E0;color:#B4740E;border-radius:14px;padding:2px 6px 2px 10px;font-family:" + MONO + ";")}>
              <select value={m.aggregation} onChange={(e) => setMeasureAgg(i, e.target.value)} style={s("background:transparent;border:none;color:#B4740E;font-size:12px;cursor:pointer;font-family:" + MONO + ";")}>
                {["count", "count_distinct", "sum", "avg", "min", "max"].map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
              : {m.column}
              <span onClick={() => removeMeasure(i)} style={s("cursor:pointer;width:16px;height:16px;border-radius:50%;display:flex;align-items:center;justify-content:center;")}>✕</span>
            </div>
          ))}
          {(spec.having ?? []).map((f, i) => (
            <div key={`h${i}`} style={s("position:relative;")}>
              <div
                onClick={() => setState({ analysisEditingHaving: st.analysisEditingHaving === i ? null : i })}
                style={s("display:flex;align-items:center;gap:6px;font-size:12px;background:#E8F4FF;color:#0B6BB8;border-radius:14px;padding:4px 6px 4px 10px;cursor:pointer;font-family:" + MONO + ";")}
              >
                having: {f.column} {f.operator} {["is_null", "is_not_null"].includes(f.operator) ? "" : String(f.value ?? "")}
                <span onClick={(e) => { e.stopPropagation(); removeHaving(i); }} style={s("color:#0B6BB8;cursor:pointer;")}>✕</span>
              </div>
              {st.analysisEditingHaving === i && (
                <>
                  <div style={s("position:fixed;inset:0;z-index:19;")} onClick={() => setState({ analysisEditingHaving: null })} />
                  <div style={s("position:absolute;top:30px;left:0;background:#FFFFFF;border:1px solid #E4E1ED;border-radius:10px;box-shadow:0 12px 32px -8px rgba(20,20,40,0.18);padding:10px;z-index:20;display:flex;gap:6px;align-items:center;")}>
                    <select value={f.column} onChange={(e) => setHavingField(i, "column", e.target.value)} style={s("height:30px;border:1px solid #E5E5ED;border-radius:6px;padding:0 6px;font-size:12px;")}>
                      {[...spec.dimensions, ...spec.measures.map(measureOutputName)].map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                    <select value={f.operator} onChange={(e) => setHavingField(i, "operator", e.target.value)} style={s("height:30px;border:1px solid #E5E5ED;border-radius:6px;padding:0 6px;font-size:12px;")}>
                      {["=", "!=", ">", "<", ">=", "<="].map((o) => <option key={o} value={o}>{o}</option>)}
                    </select>
                    <input autoFocus value={String(f.value ?? "")} onChange={(e) => setHavingField(i, "value", e.target.value)} style={s("height:30px;border:1px solid #E5E5ED;border-radius:6px;padding:0 8px;font-size:12px;width:110px;")} />
                  </div>
                </>
              )}
            </div>
          ))}
          {spec.join && (
            <div style={s("display:flex;align-items:center;gap:6px;font-size:12px;background:#F5F0FF;color:#6412E0;border-radius:14px;padding:4px 6px 4px 10px;font-family:" + MONO + ";")}>
              join: {st.localSources.find((s) => s.id === spec.join!.local_source_id)?.name ?? spec.join.local_source_id} ({spec.join.join_type})
              <span onClick={() => setJoin(null)} style={s("color:#6412E0;cursor:pointer;")}>✕</span>
            </div>
          )}
          {spec.distinct && (
            <div style={s("display:flex;align-items:center;gap:6px;font-size:12px;background:#F5F0FF;color:#6412E0;border-radius:14px;padding:4px 6px 4px 10px;font-family:" + MONO + ";")}>
              distinct
              <span onClick={toggleDistinct} style={s("color:#6412E0;cursor:pointer;")}>✕</span>
            </div>
          )}
          {!hasSpec && <div style={s("font-size:12px;color:#C4C4CE;")}>No filters, breakdowns, or measures yet</div>}
        </div>

        {/* + Filter / + Breakdown / + Measure / + Having / + Join / Distinct — single row (§11, §53) */}
        <div style={s("display:flex;gap:16px;position:relative;margin-bottom:16px;padding-bottom:14px;border-bottom:1px solid #F0F0F5;flex-wrap:wrap;align-items:center;")}>
          <Hv css="font-size:12.5px;color:#7A2BF5;cursor:pointer;padding:4px 0;" hover="text-decoration:underline;" onClick={() => addAnalysisFilter(cols[0] || "")}>+ Filter</Hv>
          <div style={s("position:relative;")}>
            <Hv css="font-size:12.5px;color:#7A2BF5;cursor:pointer;padding:4px 0;" hover="text-decoration:underline;" onClick={() => setState({ analysisDimPickerOpen: !st.analysisDimPickerOpen })}>+ Breakdown</Hv>
            {st.analysisDimPickerOpen && (
              <div style={s("position:absolute;top:26px;left:0;width:180px;background:#FFFFFF;border:1px solid #E4E1ED;border-radius:10px;box-shadow:0 12px 32px -8px rgba(20,20,40,0.18);padding:6px;z-index:20;max-height:220px;overflow:auto;")}>
                {cols.filter((c) => !spec.dimensions.includes(c)).map((c) => (
                  <Hv key={c} css="padding:7px 10px;border-radius:6px;font-size:12.5px;cursor:pointer;" hover="background:#F1EDFF;" onClick={() => { toggleDimension(c); setState({ analysisDimPickerOpen: false }); }}>{c}</Hv>
                ))}
              </div>
            )}
          </div>
          <div style={s("position:relative;")}>
            <Hv css="font-size:12.5px;color:#7A2BF5;cursor:pointer;padding:4px 0;" hover="text-decoration:underline;" onClick={() => setState({ analysisMeasurePickerOpen: !st.analysisMeasurePickerOpen })}>+ Measure</Hv>
            {st.analysisMeasurePickerOpen && (
              <div style={s("position:absolute;top:26px;left:0;width:180px;background:#FFFFFF;border:1px solid #E4E1ED;border-radius:10px;box-shadow:0 12px 32px -8px rgba(20,20,40,0.18);padding:6px;z-index:20;max-height:220px;overflow:auto;")}>
                {cols.map((c) => (
                  <Hv key={c} css="padding:7px 10px;border-radius:6px;font-size:12.5px;cursor:pointer;" hover="background:#F1EDFF;" onClick={() => { addMeasure(c); setState({ analysisMeasurePickerOpen: false }); }}>{c}</Hv>
                ))}
              </div>
            )}
          </div>
          {spec.measures.length > 0 && spec.dimensions.length > 0 && (
            <Hv css="font-size:12.5px;color:#0B6BB8;cursor:pointer;padding:4px 0;" hover="text-decoration:underline;" onClick={() => addHaving(spec.dimensions[0])}>+ Having</Hv>
          )}
          <div style={s("position:relative;")}>
            <Hv css="font-size:12.5px;color:#6412E0;cursor:pointer;padding:4px 0;" hover="text-decoration:underline;" onClick={() => { setState({ analysisJoinPickerOpen: !st.analysisJoinPickerOpen }); loadLocalSources(); }}>+ Join</Hv>
            {st.analysisJoinPickerOpen && (
              <>
                <div style={s("position:fixed;inset:0;z-index:19;")} onClick={() => setState({ analysisJoinPickerOpen: false })} />
                <div style={s("position:absolute;top:26px;left:0;width:260px;background:#FFFFFF;border:1px solid #E4E1ED;border-radius:10px;box-shadow:0 12px 32px -8px rgba(20,20,40,0.18);padding:10px;z-index:20;display:flex;flex-direction:column;gap:8px;")}>
                  <select
                    value={spec.join?.local_source_id ?? ""}
                    onChange={(e) => { setJoin({ local_source_id: e.target.value, join_type: spec.join?.join_type ?? "left", keys: spec.join?.keys ?? [] }); if (st.localSources.length === 0) loadLocalSources(); }}
                    style={s("height:30px;border:1px solid #E5E5ED;border-radius:6px;padding:0 6px;font-size:12px;")}
                  >
                    <option value="">— pick a source —</option>
                    {st.localSources.map((src) => <option key={src.id} value={src.id}>{src.name}</option>)}
                  </select>
                  {spec.join && (
                    <>
                      <select value={spec.join.join_type} onChange={(e) => setJoin({ ...spec.join!, join_type: e.target.value as AnalysisJoin["join_type"] })} style={s("height:30px;border:1px solid #E5E5ED;border-radius:6px;padding:0 6px;font-size:12px;")}>
                        {["inner", "left", "right", "full", "semi", "anti"].map((t) => <option key={t} value={t}>{t}</option>)}
                      </select>
                      {spec.join.keys.map((k, i) => {
                        const rightCols = st.localSources.find((s) => s.id === spec.join!.local_source_id)?.columns ?? [];
                        return (
                          <div key={i} style={s("display:flex;gap:4px;align-items:center;")}>
                            <select value={k[0]} onChange={(e) => setJoinKey(i, 0, e.target.value)} style={s("flex:1;height:28px;border:1px solid #E5E5ED;border-radius:6px;font-size:11.5px;")}>
                              {cols.map((c) => <option key={c} value={c}>{c}</option>)}
                            </select>
                            <span style={s("color:#9C9CAA;font-size:11px;")}>=</span>
                            <select value={k[1]} onChange={(e) => setJoinKey(i, 1, e.target.value)} style={s("flex:1;height:28px;border:1px solid #E5E5ED;border-radius:6px;font-size:11.5px;")}>
                              <option value="">—</option>
                              {rightCols.map((c) => <option key={c} value={c}>{c}</option>)}
                            </select>
                            <span onClick={() => removeJoinKey(i)} style={s("cursor:pointer;color:#9C9CAA;")}>✕</span>
                          </div>
                        );
                      })}
                      <Hv css="font-size:11.5px;color:#7A2BF5;cursor:pointer;" hover="text-decoration:underline;" onClick={addJoinKey}>+ key</Hv>
                    </>
                  )}
                </div>
              </>
            )}
          </div>
          <div onClick={toggleDistinct} style={s("display:flex;align-items:center;gap:6px;font-size:12.5px;color:#6412E0;cursor:pointer;")}>
            <div style={s(box(!!spec.distinct))} /> Distinct
          </div>
          {hasSpec && (
            <Hv css="font-size:12.5px;color:#9C9CAA;cursor:pointer;margin-left:auto;padding:4px 0;" hover="color:#FF3B5C;" onClick={resetAnalysis}>Clear analysis</Hv>
          )}
        </div>

        {st.analysisLoading && (
          <div style={s("display:flex;align-items:center;gap:8px;font-size:12.5px;color:#9C9CAA;margin-bottom:8px;")}>
            <div style={s("width:11px;height:11px;border:2px solid #E5E5ED;border-top-color:#7A2BF5;border-radius:50%;animation:ldw-spin 0.7s linear infinite;")} />
            Running…
            <Hv css="color:#7A2BF5;cursor:pointer;" hover="text-decoration:underline;" onClick={cancelAnalysis}>Cancel</Hv>
          </div>
        )}
        {st.analysisError && <div style={s("font-size:12.5px;color:#FF3B5C;")}>{st.analysisError}</div>}

        {!st.analysisLoading && result && (
          <div>
            {result.visualization_hint === "kpi" && result.rows[0] && (
              <div style={s("text-align:center;padding:16px 0;")}>
                <div style={s("font-size:36px;font-weight:700;font-family:'Space Grotesk',sans-serif;")}>{fmtNum(Number(result.rows[0][0]))}</div>
                <div style={s("font-size:12.5px;color:#6B6B7A;margin-top:4px;")}>{result.columns[0]}</div>
              </div>
            )}

            {(result.visualization_hint === "bar" || result.visualization_hint === "line") && (
              <div style={s("display:flex;flex-direction:column;gap:10px;")}>
                {(() => {
                  const max = Math.max(1, ...result.rows.map((r) => Number(r[1]) || 0));
                  const drillable = spec.dimensions.length === 1;
                  return result.rows.map((r, ri) => (
                    <div key={ri}>
                      <div style={s("display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px;")}>
                        <span
                          onClick={drillable ? (e) => openDrillMenu(ri, e.clientX, e.clientY) : undefined}
                          style={s(`font-weight:600;${drillable ? "cursor:pointer;" : ""}`)}
                        >
                          {String(r[0] ?? "—")}
                        </span>
                        <span style={s(`font-family:${MONO};`)}>{fmtNum(Number(r[1]) || 0)}</span>
                      </div>
                      <div style={s("height:6px;background:#F5F0FF;border-radius:4px;overflow:hidden;")}>
                        <div style={{ ...s("height:100%;background:#7A2BF5;border-radius:4px;"), width: `${(100 * (Number(r[1]) || 0)) / max}%` }} />
                      </div>
                    </div>
                  ));
                })()}
              </div>
            )}

            {(result.visualization_hint === "table" || result.visualization_hint === "grid") && (
              <div style={s("overflow:auto;max-height:340px;border:1px solid #F0F0F5;border-radius:6px;")}>
                <table style={s("border-collapse:collapse;font-size:12.5px;width:100%;")}>
                  <thead>
                    <tr style={s("background:#FBFAFF;position:sticky;top:0;")}>
                      {result.columns.map((c) => {
                        const active = spec.sort[0]?.column === c;
                        const arrow = active ? (spec.sort[0].direction === "asc" ? " ▲" : " ▼") : "";
                        return (
                          <th key={c} onClick={() => setAnalysisSort(c)} style={s(`text-align:left;padding:6px 10px;font-weight:600;color:${active ? "#7A2BF5" : "#6B6B7A"};font-size:11px;cursor:pointer;white-space:nowrap;`)}>{c}{arrow}</th>
                        );
                      })}
                    </tr>
                  </thead>
                  <tbody>
                    {result.rows.map((r, ri) => {
                      const drillable = spec.dimensions.length === 1;
                      return (
                        <tr key={ri} style={s("border-top:1px solid #F0F0F5;")}>
                          {r.map((v, ci) => (
                            <td
                              key={ci}
                              onClick={drillable && ci === 0 ? (e) => openDrillMenu(ri, e.clientX, e.clientY) : undefined}
                              style={s(`padding:6px 10px;white-space:nowrap;${drillable && ci === 0 ? "cursor:pointer;color:#7A2BF5;" : ""}`)}
                            >
                              {v === null || v === undefined ? "—" : String(v)}
                            </td>
                          ))}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            <div style={s("display:flex;justify-content:space-between;align-items:center;margin-top:14px;padding-top:12px;border-top:1px solid #F0F0F5;")}>
              <span style={s("font-size:11.5px;color:#9C9CAA;")}>{result.row_count.toLocaleString()} row{result.row_count === 1 ? "" : "s"}</span>
              <div style={s("display:flex;gap:14px;")}>
                {!result.visualization_hint.match(/^(grid)$/) && (
                  <Hv css="font-size:11.5px;color:#7A2BF5;cursor:pointer;" hover="text-decoration:underline;" onClick={() => updateAnalysisSpec({ dimensions: [], measures: [] })}>View rows</Hv>
                )}
                <Hv css="font-size:11.5px;color:#7A2BF5;cursor:pointer;" hover="text-decoration:underline;" onClick={() => setState({ analysisShowSql: !st.analysisShowSql })}>{st.analysisShowSql ? "Hide SQL" : "View SQL"}</Hv>
                <Hv
                  css={`font-size:11.5px;font-weight:600;cursor:${hasSpec ? "pointer" : "default"};color:${hasSpec ? "#7A2BF5" : "#C4C4CE"};`}
                  hover={hasSpec ? "text-decoration:underline;" : ""}
                  onClick={hasSpec && !st.analysisPromoting ? promoteAnalysis : undefined}
                >
                  {st.analysisPromoting ? "Saving…" : "Keep as workflow"}
                </Hv>
              </div>
            </div>
            {st.analysisShowSql && (
              <div style={s(`font-family:${MONO};font-size:11px;color:#D9D6F5;background:#14131C;border-radius:8px;padding:10px 12px;white-space:pre-wrap;overflow-wrap:anywhere;margin-top:8px;`)}>{result.generated_sql}</div>
            )}
          </div>
        )}

        {!st.analysisLoading && !result && !st.analysisError && (
          <div style={s("font-size:12.5px;color:#9C9CAA;")}>Group, measure, or filter above — results update live, nothing here touches the workflow.</div>
        )}
      </div>

      {drill && drillCol && (
        <>
          <div style={s("position:fixed;inset:0;z-index:150;")} onClick={closeDrillMenu} />
          <div
            style={{
              ...s("position:fixed;z-index:151;width:200px;background:#FFFFFF;border:1px solid #E4E1ED;border-radius:10px;box-shadow:0 12px 32px -8px rgba(20,20,40,0.22);padding:6px;"),
              left: Math.min(drill.x, window.innerWidth - 220),
              top: Math.min(drill.y + 8, window.innerHeight - 260),
            }}
          >
            <Hv css="padding:8px 10px;border-radius:6px;font-size:12.5px;cursor:pointer;" hover="background:#F1EDFF;" onClick={() => drillFilterTo(drillCol, drillValue, false)}>Filter to {String(drillValue ?? "—")}</Hv>
            <Hv css="padding:8px 10px;border-radius:6px;font-size:12.5px;cursor:pointer;" hover="background:#F1EDFF;" onClick={() => drillFilterTo(drillCol, drillValue, true)}>Exclude {String(drillValue ?? "—")}</Hv>
            <div style={s("position:relative;")}>
              <Hv css="padding:8px 10px;border-radius:6px;font-size:12.5px;cursor:pointer;display:flex;justify-content:space-between;" hover="background:#F1EDFF;" onClick={toggleDrillBreakdown}>Break down by <span>›</span></Hv>
              {drill.breakdownOpen && (
                <div style={s("position:absolute;top:0;left:100%;width:180px;background:#FFFFFF;border:1px solid #E4E1ED;border-radius:10px;box-shadow:0 12px 32px -8px rgba(20,20,40,0.18);padding:6px;max-height:220px;overflow:auto;")}>
                  {cols.filter((c) => c !== drillCol).map((c) => (
                    <Hv key={c} css="padding:7px 10px;border-radius:6px;font-size:12.5px;cursor:pointer;" hover="background:#F1EDFF;" onClick={() => drillBreakdownBy(drillCol, drillValue, c)}>{c}</Hv>
                  ))}
                </div>
              )}
            </div>
            <div style={s("border-top:1px solid #F0F0F5;margin:4px 0;")} />
            <Hv css="padding:8px 10px;border-radius:6px;font-size:12.5px;cursor:pointer;color:#7A2BF5;" hover="background:#F1EDFF;" onClick={() => drillViewRows(drillCol, drillValue)}>View underlying rows</Hv>
          </div>
        </>
      )}
      </>
    );
  };

  // Intent recipes (Phase 12, P12.9) — deterministic, parameterized shortcuts
  // to Window/Qualify/Derive-powered analyses (trend, top-N, period compare,
  // running total, moving average, contribution, duplicates, distribution)
  // without building a raw expression/window UI. Backend builds the
  // AnalysisSpec; this only collects params.
  const RECIPE_LABELS: Record<RecipeName, string> = {
    summarize: "Summarize",
    trend: "Trend over time",
    top_bottom_n: "Top / Bottom N",
    compare_periods: "Compare periods",
    running_total: "Running total",
    moving_average: "Moving average",
    contribution: "Contribution / share",
    duplicates: "Find duplicates",
    missing_values: "Missing values",
    distribution: "Distribution (histogram)",
  };
  const renderRecipes = () => {
    const cols = st.activeWorkspace?.sourceCols ?? [];
    const p = st.recipeParams;
    const measureField = (key: string, label = "Measure") => (
      <div style={s("display:flex;flex-direction:column;gap:4px;")}>
        <span style={s("font-size:11px;color:#9C9CAA;")}>{label}</span>
        <div style={s("display:flex;gap:6px;")}>
          <select value={p[key]?.aggregation ?? "count"} onChange={(e) => setRecipeParam(key, { ...(p[key] ?? {}), aggregation: e.target.value })} style={s("height:30px;border:1px solid #E5E5ED;border-radius:6px;font-size:12px;")}>
            {["count", "count_distinct", "sum", "avg", "min", "max"].map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
          <select value={p[key]?.column ?? ""} onChange={(e) => setRecipeParam(key, { ...(p[key] ?? {}), column: e.target.value })} style={s("height:30px;border:1px solid #E5E5ED;border-radius:6px;font-size:12px;flex:1;")}>
            <option value="">— column —</option>
            {cols.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
      </div>
    );
    const columnField = (key: string, label: string, optional = false) => (
      <div style={s("display:flex;flex-direction:column;gap:4px;")}>
        <span style={s("font-size:11px;color:#9C9CAA;")}>{label}</span>
        <select value={p[key] ?? ""} onChange={(e) => setRecipeParam(key, e.target.value || undefined)} style={s("height:30px;border:1px solid #E5E5ED;border-radius:6px;font-size:12px;")}>
          {optional && <option value="">— none —</option>}
          {!optional && <option value="">— column —</option>}
          {cols.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
    );
    const numberField = (key: string, label: string, defaultVal?: number) => (
      <div style={s("display:flex;flex-direction:column;gap:4px;")}>
        <span style={s("font-size:11px;color:#9C9CAA;")}>{label}</span>
        <input type="number" value={p[key] ?? defaultVal ?? ""} onChange={(e) => setRecipeParam(key, Number(e.target.value))} style={s("height:30px;border:1px solid #E5E5ED;border-radius:6px;font-size:12px;padding:0 8px;width:90px;")} />
      </div>
    );
    const grainField = () => (
      <div style={s("display:flex;flex-direction:column;gap:4px;")}>
        <span style={s("font-size:11px;color:#9C9CAA;")}>Grain</span>
        <select value={p.grain ?? "month"} onChange={(e) => setRecipeParam("grain", e.target.value)} style={s("height:30px;border:1px solid #E5E5ED;border-radius:6px;font-size:12px;")}>
          {["day", "week", "month", "quarter", "year"].map((g) => <option key={g} value={g}>{g}</option>)}
        </select>
      </div>
    );
    const multiColumnField = (key: string, label: string) => {
      const selected: string[] = p[key] ?? [];
      return (
        <div style={s("display:flex;flex-direction:column;gap:4px;")}>
          <span style={s("font-size:11px;color:#9C9CAA;")}>{label}</span>
          <div style={s("display:flex;flex-wrap:wrap;gap:4px;max-width:280px;")}>
            {cols.map((c) => {
              const on = selected.includes(c);
              return (
                <div key={c} onClick={() => setRecipeParam(key, on ? selected.filter((x) => x !== c) : [...selected, c])} style={s(`font-size:11px;padding:3px 8px;border-radius:12px;cursor:pointer;border:1px solid ${on ? "#DCD3FF" : "#E5E5ED"};background:${on ? "#F1EDFF" : "#FFFFFF"};color:${on ? "#7A2BF5" : "#9C9CAA"};`)}>{c}</div>
              );
            })}
          </div>
        </div>
      );
    };

    const fields: Record<RecipeName, () => any> = {
      summarize: () => <>{multiColumnField("dimensions", "Group by")}{measureField("measures0", "Measure")}</>,
      trend: () => <>{columnField("date_column", "Date column")}{grainField()}{measureField("measure", "Measure")}{columnField("group", "Group by (optional)", true)}</>,
      top_bottom_n: () => (
        <>
          {columnField("dimension", "Dimension")}
          {measureField("measure", "Measure")}
          {numberField("n", "N", 5)}
          {columnField("partition", "Partition by (optional)", true)}
          <div style={s("display:flex;flex-direction:column;gap:4px;")}>
            <span style={s("font-size:11px;color:#9C9CAA;")}>Mode</span>
            <select value={p.mode ?? "top"} onChange={(e) => setRecipeParam("mode", e.target.value)} style={s("height:30px;border:1px solid #E5E5ED;border-radius:6px;font-size:12px;")}>
              <option value="top">Top</option>
              <option value="bottom">Bottom</option>
            </select>
          </div>
        </>
      ),
      compare_periods: () => <>{columnField("date_column", "Date column")}{grainField()}{measureField("measure", "Measure")}{columnField("group", "Group by (optional)", true)}</>,
      running_total: () => <>{columnField("order_column", "Order by")}{measureField("measure", "Measure")}{columnField("partition", "Partition by (optional)", true)}</>,
      moving_average: () => <>{columnField("order_column", "Order by")}{measureField("measure", "Measure")}{numberField("window_width", "Window width", 3)}{columnField("partition", "Partition by (optional)", true)}</>,
      contribution: () => <>{columnField("dimension", "Dimension")}{measureField("measure", "Measure")}{columnField("partition", "Partition by (optional)", true)}</>,
      duplicates: () => <>{multiColumnField("keys", "Key columns")}{columnField("tie_breaker_column", "Tie-breaker (optional)", true)}</>,
      missing_values: () => <>{columnField("field", "Field")}</>,
      distribution: () => <>{columnField("field", "Field")}{numberField("bucket_width", "Bucket width", 10)}</>,
    };

    // summarize's single measure uses key "measures0" in the form but the API
    // wants a `measures` array — and duplicates' tie-breaker is a plain
    // column picker in the UI but a {column,direction} object for the API.
    // Normalize right before sending.
    const buildParams = (): Record<string, unknown> => {
      if (!st.recipeName) return {};
      if (st.recipeName === "summarize") {
        return { dimensions: p.dimensions ?? [], measures: p.measures0 ? [p.measures0] : [], limit: p.limit };
      }
      if (st.recipeName === "duplicates") {
        return { keys: p.keys ?? [], tie_breaker: p.tie_breaker_column ? { column: p.tie_breaker_column, direction: "asc" } : undefined, limit: p.limit };
      }
      return p;
    };

    const result = st.recipeResult;
    return (
      <div style={s("border:1px solid #E4E1ED;border-radius:12px;padding:20px;background:#FFFFFF;margin-top:16px;")}>
        <div style={s("font-size:11px;color:#9C9CAA;font-weight:600;letter-spacing:0.03em;margin-bottom:10px;")}>RECIPES</div>
        <select
          value={st.recipeName ?? ""}
          onChange={(e) => setRecipeName((e.target.value || null) as RecipeName | null)}
          style={s("height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 8px;font-size:13px;margin-bottom:14px;")}
        >
          <option value="">— choose a recipe —</option>
          {(Object.keys(RECIPE_LABELS) as RecipeName[]).map((r) => <option key={r} value={r}>{RECIPE_LABELS[r]}</option>)}
        </select>

        {st.recipeName && (
          <>
            <div style={s("display:flex;flex-wrap:wrap;gap:14px;margin-bottom:14px;")}>{fields[st.recipeName]()}</div>
            <div style={s("display:flex;gap:8px;margin-bottom:14px;")}>
              <Hv tag="button" css="height:32px;padding:0 14px;border-radius:7px;border:none;background:#7A2BF5;color:#fff;font-size:12.5px;font-weight:600;cursor:pointer;" hover="background:#6412E0;" onClick={() => runRecipe(buildParams())}>{st.recipeLoading ? "Running…" : "Run recipe"}</Hv>
              {result && (
                <Hv tag="button" css="height:32px;padding:0 14px;border-radius:7px;border:1px solid #7A2BF5;background:#FFFFFF;color:#7A2BF5;font-size:12.5px;font-weight:600;cursor:pointer;" hover="background:#F1EDFF;" onClick={promoteRecipe}>{st.recipePromoting ? "Saving…" : "Keep as workflow"}</Hv>
              )}
            </div>

            {st.recipeError && <div style={s("font-size:12.5px;color:#FF3B5C;margin-bottom:10px;")}>{st.recipeError}</div>}

            {result && (
              <div>
                {result.visualization_hint === "kpi" && result.rows[0] && (
                  <div style={s("text-align:center;padding:16px 0;")}>
                    <div style={s("font-size:32px;font-weight:700;font-family:'Space Grotesk',sans-serif;")}>{fmtNum(Number(result.rows[0][0]))}</div>
                    <div style={s("font-size:12px;color:#6B6B7A;margin-top:4px;")}>{result.columns[0]}</div>
                  </div>
                )}
                {result.visualization_hint !== "kpi" && (
                  <div style={s("overflow:auto;max-height:340px;border:1px solid #F0F0F5;border-radius:6px;")}>
                    <table style={s("border-collapse:collapse;font-size:12.5px;width:100%;")}>
                      <thead>
                        <tr style={s("background:#FBFAFF;position:sticky;top:0;")}>
                          {result.columns.map((c) => <th key={c} style={s("text-align:left;padding:6px 10px;font-weight:600;color:#6B6B7A;font-size:11px;white-space:nowrap;")}>{c}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {result.rows.map((r, ri) => (
                          <tr key={ri} style={s("border-top:1px solid #F0F0F5;")}>
                            {r.map((v, ci) => <td key={ci} style={s("padding:6px 10px;white-space:nowrap;")}>{v === null || v === undefined ? "—" : String(v)}</td>)}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <div style={s("display:flex;justify-content:space-between;align-items:center;margin-top:10px;")}>
                  <span style={s("font-size:11.5px;color:#9C9CAA;")}>{result.row_count.toLocaleString()} row{result.row_count === 1 ? "" : "s"}</span>
                  <Hv css="font-size:11.5px;color:#7A2BF5;cursor:pointer;" hover="text-decoration:underline;" onClick={() => setState({ recipeShowSql: !st.recipeShowSql })}>{st.recipeShowSql ? "Hide SQL" : "View SQL"}</Hv>
                </div>
                {st.recipeShowSql && (
                  <div style={s(`font-family:${MONO};font-size:11px;color:#D9D6F5;background:#14131C;border-radius:8px;padding:10px 12px;white-space:pre-wrap;overflow-wrap:anywhere;margin-top:8px;`)}>{result.generated_sql}</div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    );
  };

  // Dedicated Explore tab — was previously a bottom strip on the Data tab;
  // now its own full page, matching the design mock's Explore tab.
  const renderAskBar = () => (
    <>
      {!st.aiStatus?.installed ? (
        <div style={s("display:flex;align-items:center;justify-content:space-between;background:#FBFAFF;border:1px solid #E4E1ED;border-radius:10px;padding:16px 18px;")}>
          <div style={s("font-size:12.5px;color:#6B6B7A;")}>
            {st.aiInstalling
              ? `Installing local AI model… ${st.aiInstallProgress ? Math.round((100 * st.aiInstallProgress.bytes) / (st.aiInstallProgress.total_bytes || 1)) : 0}%`
              : "Ask plain-English questions against this data — nothing leaves your device."}
          </div>
          {!st.aiInstalling && (
            <Hv tag="button" css="height:30px;padding:0 14px;border-radius:7px;border:1px solid #7A2BF5;background:#FFFFFF;color:#7A2BF5;font-size:12px;font-weight:600;cursor:pointer;" hover="background:#F1EDFF;" onClick={enableLocalAi}>Enable Local AI</Hv>
          )}
        </div>
      ) : (
        <>
          {st.aiEditingStepId && (
            <div style={s("font-size:11.5px;color:#7A2BF5;")}>Editing saved SQL step — run to preview, then save.</div>
          )}
          {(st.aiResult || st.aiEditingStepId) && (
            <div style={s("background:#FFFFFF;border:1px solid #E4E1ED;border-radius:10px;padding:12px 14px;display:flex;flex-direction:column;gap:8px;")}>
              {st.aiResult?.status === "SUCCESS" && (
                <>
                  <div style={s("font-size:11.5px;color:#9C9CAA;")}>
                    {st.aiResult.rows_returned.toLocaleString()} row{st.aiResult.rows_returned === 1 ? "" : "s"} · {st.aiResult.inference_ms + st.aiResult.execution_ms}ms{st.aiResult.attempt_count > 1 ? " · repaired once" : ""}
                  </div>
                  <div style={s("overflow:auto;max-height:300px;border:1px solid #F0F0F5;border-radius:6px;")}>
                    <table style={s("border-collapse:collapse;font-size:12.5px;width:100%;")}>
                      <thead>
                        <tr style={s("background:#FBFAFF;")}>
                          {st.aiResult.columns.map((c) => (
                            <th key={c} style={s("text-align:left;padding:6px 10px;font-weight:600;color:#6B6B7A;font-size:11px;")}>{c}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {st.aiResult.preview_rows.slice(0, 20).map((r, ri) => (
                          <tr key={ri} style={s("border-top:1px solid #F0F0F5;")}>
                            {r.map((v, ci) => (
                              <td key={ci} style={s("padding:6px 10px;")}>{v === null || v === undefined ? "—" : String(v)}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
              {st.aiResult && st.aiResult.status !== "SUCCESS" && (
                <div style={s("font-size:12.5px;color:#FF3B5C;")}>
                  {st.aiResult.status === "BLOCKED" ? "This request cannot run in Local AI exploration." : st.aiResult.error || "Couldn't answer that question."}
                </div>
              )}
              <textarea
                value={st.aiSqlDraft}
                onChange={(e) => setState({ aiSqlDraft: e.target.value })}
                spellCheck={false}
                rows={4}
                style={s(`font-family:${MONO};font-size:11.5px;color:#17171F;background:#F5F0FF;border:1px solid #E5E5ED;border-radius:6px;padding:8px 10px;resize:vertical;width:100%;box-sizing:border-box;`)}
              />
              <div style={s("display:flex;gap:8px;justify-content:flex-end;")}>
                <Hv tag="button" css="height:28px;padding:0 12px;border-radius:6px;border:1px solid #E5E5ED;background:#FFFFFF;color:#6B6B7A;font-size:12px;font-weight:600;cursor:pointer;" hover="background:#F5F0FF;" onClick={cancelSqlEdit}>{st.aiEditingStepId ? "Cancel" : "Discard"}</Hv>
                <Hv tag="button" css="height:28px;padding:0 12px;border-radius:6px;border:1px solid #7A2BF5;background:#FFFFFF;color:#7A2BF5;font-size:12px;font-weight:600;cursor:pointer;" hover="background:#F1EDFF;" onClick={runSqlDraft}>{st.aiSqlRunning ? "Running…" : "Run"}</Hv>
                <Hv tag="button" css="height:28px;padding:0 12px;border-radius:6px;border:none;background:#7A2BF5;color:#fff;font-size:12px;font-weight:600;cursor:pointer;" hover="background:#6412E0;" onClick={promoteAiResult}>{st.aiPromoting ? "Saving…" : st.aiEditingStepId ? "Save changes" : "Keep as transformation"}</Hv>
              </div>
            </div>
          )}
          <div style={s("display:flex;gap:8px;align-items:center;")}>
            <input
              value={st.aiQuestion}
              onChange={(e) => setState({ aiQuestion: e.target.value })}
              onKeyDown={(e) => { if (e.key === "Enter" && !st.aiAsking && st.aiQuestion.trim()) askAi(); }}
              placeholder="Ask this data — e.g. Which countries have the highest average margin?"
              style={s("flex:1;height:36px;border:1px solid #E5E5ED;border-radius:8px;padding:0 12px;font-size:13px;")}
            />
            <Hv tag="button" css="height:36px;padding:0 16px;border-radius:8px;border:none;background:#7A2BF5;color:#fff;font-size:12.5px;font-weight:600;cursor:pointer;" hover="background:#6412E0;" onClick={askAi}>{st.aiAsking ? "Asking…" : "Ask"}</Hv>
          </div>
        </>
      )}
    </>
  );

  // ================= RENDER =================
  return (
    <div style={s("width:100%;height:100vh;display:flex;flex-direction:column;background:#F5F0FF;color:#17171F;overflow:hidden;font-size:14px;")}>
      {/* Title bar */}
      <div style={s("height:38px;min-height:38px;background:#FFFFFF;border-bottom:1px solid #E5E5ED;display:flex;align-items:center;justify-content:space-between;padding:0 0 0 10px;")}>
        <div style={s("display:flex;align-items:center;gap:8px;")}>
          <div style={s("width:18px;height:18px;border-radius:5px;background:#7A2BF5;")} />
          <div style={s("font-size:12.5px;color:#6B6B7A;")}>{windowTitle}</div>
        </div>
        <div style={s("display:flex;align-items:center;gap:14px;")}>
          <div style={s(`display:flex;align-items:center;gap:5px;font-size:11px;color:#9C9CAA;font-family:${MONO};`)}>
            <div style={s("width:6px;height:6px;border-radius:50%;background:#7A2BF5;")} />CPU {st.cpu}%
          </div>
          <div style={s(`display:flex;align-items:center;gap:5px;font-size:11px;color:#9C9CAA;font-family:${MONO};`)}>RAM {ramLabel}</div>
          {coreConnected && (
            <div style={s(`display:flex;align-items:center;gap:5px;font-size:11px;color:#00B36B;font-family:${MONO};`)} title="Rust core connected">
              <div style={s("width:6px;height:6px;border-radius:50%;background:#00B36B;")} />core
            </div>
          )}
          <div style={s("width:1px;height:16px;background:#E5E5ED;")} />
        </div>
        <div style={s("display:flex;height:100%;")}>
          <Hv css="width:44px;height:100%;display:flex;align-items:center;justify-content:center;cursor:default;" hover="background:#EFEFF5;"><div style={s("width:10px;height:1px;background:#6B6B7A;")} /></Hv>
          <Hv css="width:44px;height:100%;display:flex;align-items:center;justify-content:center;cursor:default;" hover="background:#EFEFF5;"><div style={s("width:10px;height:10px;border:1px solid #6B6B7A;")} /></Hv>
          <Hv css="width:44px;height:100%;display:flex;align-items:center;justify-content:center;cursor:default;color:#6B6B7A;" hover="background:#FF3B5C;color:#fff;">✕</Hv>
        </div>
      </div>

      {/* Content */}
      <div style={s("flex:1;min-height:0;position:relative;overflow:hidden;")}>
        {screen === "signin" && (
          <div style={s("width:100%;height:100%;display:flex;align-items:center;justify-content:center;")}>
            <div style={s("width:380px;background:#FFFFFF;border:1px solid #E4E1ED;border-radius:16px;padding:40px 36px;display:flex;flex-direction:column;align-items:center;gap:18px;box-shadow:0 12px 40px -12px rgba(20,20,40,0.14);")}>
              <div style={s("width:52px;height:52px;border-radius:14px;background:#7A2BF5;")} />
              <div style={s("text-align:center;")}>
                <div style={s("font-size:24px;font-weight:700;font-family:'Space Grotesk',sans-serif;letter-spacing:-0.01em;")}>PyJama</div>
                <div style={s("font-size:13px;color:#6B6B7A;margin-top:4px;")}>Governed local transformation for Unity Catalog</div>
              </div>
              <Hv tag="button" css="width:100%;margin-top:8px;height:44px;border-radius:10px;border:none;background:#7A2BF5;color:#fff;font-size:14px;font-weight:600;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:8px;" hover="background:#6412E0;" onClick={signIn}>
                {st.authing ? (
                  <>
                    <div style={s("width:14px;height:14px;border:2px solid rgba(255,255,255,0.4);border-top-color:#fff;border-radius:50%;animation:ldw-spin 0.7s linear infinite;")} />
                    <span>Signing in…</span>
                  </>
                ) : (
                  <span>Sign in with Databricks</span>
                )}
              </Hv>
              <div style={s("font-size:11.5px;color:#9C9CAA;text-align:center;line-height:1.5;")}>Credentials are stored using OS-provided secure credential storage. Sessions refresh automatically.</div>
              {st.authError && (
                <div style={s("font-size:11.5px;color:#FF3B5C;text-align:center;line-height:1.5;background:#FFE9EE;border-radius:8px;padding:8px 10px;width:100%;box-sizing:border-box;")}>{st.authError}</div>
              )}
            </div>
          </div>
        )}

        {(screen === "home" || screen === "watched" || screen === "sources" || screen === "newWorkspace") && (
          <div style={s("width:100%;height:100%;display:flex;")}>
            <div style={s("width:200px;min-width:200px;background:#FFFFFF;border-right:1px solid #E5E5ED;padding:20px 12px;display:flex;flex-direction:column;gap:2px;")}>
              <div style={s("font-size:13px;color:#17171F;font-weight:700;letter-spacing:0.02em;padding:0 10px 12px;font-family:'Space Grotesk',sans-serif;")}>PyJama</div>
              <div onClick={goHome} style={s(`padding:10px 10px;border-radius:8px;font-size:13.5px;cursor:pointer;font-weight:${screen === "home" ? 600 : 500};background:${screen === "home" ? "#F1EDFF" : "transparent"};color:${screen === "home" ? "#7A2BF5" : "#17171F"};`)}>Workspaces</div>
              {live && (
                <div onClick={openSources} style={s(`padding:10px 10px;border-radius:8px;font-size:13.5px;cursor:pointer;font-weight:${screen === "sources" ? 600 : 500};background:${screen === "sources" ? "#F1EDFF" : "transparent"};color:${screen === "sources" ? "#7A2BF5" : "#17171F"};`)}>Sources</div>
              )}
              <div onClick={openWatchedFolder} style={s(`padding:10px 10px;border-radius:8px;font-size:13.5px;cursor:pointer;font-weight:${screen === "watched" ? 600 : 500};background:${screen === "watched" ? "#F1EDFF" : "transparent"};color:${screen === "watched" ? "#7A2BF5" : "#17171F"};`)}>Watched Folder</div>
              <div style={s("flex:1;")} />
              <Hv css="padding:10px 10px;border-radius:8px;font-size:13.5px;color:#6B6B7A;cursor:pointer;" hover="background:#F1EDFF;color:#17171F;" onClick={signOut}>Sign out</Hv>
            </div>

            <div style={s("flex:1;min-width:0;overflow:auto;padding:36px 44px;")}>
              {screen === "newWorkspace" && live && (
                <>
                  <div style={s("display:flex;align-items:center;gap:14px;margin-bottom:24px;")}>
                    <Hv css="font-size:12.5px;color:#9C9CAA;cursor:pointer;" hover="color:#7A2BF5;" onClick={goHome}>← Home</Hv>
                    <div style={s("font-size:22px;font-weight:600;font-family:'Space Grotesk',sans-serif;")}>New Workspace</div>
                  </div>
                  <div style={s("font-size:13px;color:#6B6B7A;margin-bottom:24px;")}>Every workspace is a notebook over one source. Pick an existing one, checkout a new table, or import a file.</div>

                  {st.checkoutError && (
                    <div style={s("font-size:12.5px;color:#FF3B5C;background:#FFE9EE;border-radius:8px;padding:10px 12px;margin-bottom:20px;max-width:640px;")}>{st.checkoutError}</div>
                  )}

                  <div style={s("display:flex;gap:16px;margin-bottom:32px;flex-wrap:wrap;")}>
                    <Hv css="width:220px;background:#FFFFFF;border:1px solid #E4E1ED;border-radius:12px;padding:18px;cursor:pointer;display:flex;flex-direction:column;gap:6px;" hover="border-color:#7A2BF5;box-shadow:0 8px 24px -12px rgba(122,43,245,0.25);" onClick={chooseCheckoutNewTable}>
                      <div style={s("font-size:20px;")}>☁</div>
                      <div style={s("font-size:13.5px;font-weight:600;")}>Checkout from Unity Catalog</div>
                      <div style={s("font-size:11.5px;color:#9C9CAA;")}>Browse and filter a governed table.</div>
                    </Hv>
                    <label style={s("width:220px;background:#FFFFFF;border:1px solid #E4E1ED;border-radius:12px;padding:18px;cursor:pointer;display:flex;flex-direction:column;gap:6px;")}>
                      <input type="file" accept=".csv,.xlsx,.parquet" style={{ display: "none" }} onChange={(e) => { const f = e.target.files?.[0]; if (f) chooseImportFileForNewWorkspace(f); }} />
                      <div style={s("font-size:20px;")}>📄</div>
                      <div style={s("font-size:13.5px;font-weight:600;")}>Import a local file</div>
                      <div style={s("font-size:11.5px;color:#9C9CAA;")}>CSV, XLSX, or Parquet from your computer.</div>
                    </label>
                  </div>

                  <div style={s("font-size:12px;color:#9C9CAA;font-weight:600;letter-spacing:0.03em;margin-bottom:10px;")}>OR USE AN EXISTING SOURCE</div>
                  {st.sourcesLoading && st.sourcesList.length === 0 && <div style={s("font-size:13px;color:#9C9CAA;")}>Loading…</div>}
                  {!st.sourcesLoading && st.sourcesList.length === 0 && <div style={s("font-size:13px;color:#9C9CAA;")}>No sources yet — checkout or import one above.</div>}
                  <div style={s("display:flex;flex-direction:column;gap:8px;max-width:640px;")}>
                    {st.sourcesList.map((src) => (
                      <Hv key={src.source_id} css="background:#FFFFFF;border:1px solid #E4E1ED;border-radius:10px;padding:12px 14px;cursor:pointer;display:flex;align-items:center;justify-content:space-between;" hover="border-color:#7A2BF5;" onClick={() => startNotebookFromSource(src.source_id, src.name)}>
                        <div>
                          <div style={s("font-size:13.5px;font-weight:600;")}>{src.name}</div>
                          <div style={s("font-size:11.5px;color:#9C9CAA;margin-top:2px;")}>{src.kind === "uc_table" ? "Unity Catalog" : src.kind.toUpperCase()} · {src.row_count.toLocaleString()} rows</div>
                        </div>
                        <div style={s("font-size:12px;color:#7A2BF5;")}>{st.sourceActionBusy === src.source_id ? "Opening…" : "Use →"}</div>
                      </Hv>
                    ))}
                  </div>
                </>
              )}

              {screen === "sources" && live && (
                <>
                  <div style={s("display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;")}>
                    <div style={s("font-size:22px;font-weight:600;font-family:'Space Grotesk',sans-serif;")}>Sources</div>
                    <div style={s("display:flex;gap:8px;")}>
                      <Hv tag="button" css="height:34px;padding:0 14px;border-radius:7px;border:1px solid #E5E5ED;background:#FFFFFF;color:#6B6B7A;font-size:12.5px;font-weight:600;cursor:pointer;" hover="background:#F5F0FF;" onClick={loadSources}>Refresh list</Hv>
                      <label style={s("height:34px;padding:0 14px;border-radius:7px;border:1px solid #7A2BF5;background:#FFFFFF;color:#7A2BF5;font-size:12.5px;font-weight:600;cursor:pointer;display:flex;align-items:center;")}>
                        <input type="file" accept=".csv,.xlsx,.parquet" style={{ display: "none" }} onChange={(e) => { const f = e.target.files?.[0]; if (f) { setState({ sourceActionMsg: "Importing…" }); api.localSourceImport(f).then(() => { setState({ sourceActionMsg: `Imported ${f.name}.` }); loadSources(); }).catch((err) => setState({ sourceActionMsg: String(err) })); } }} />
                        + Import file
                      </label>
                      <Hv tag="button" css="height:34px;padding:0 14px;border-radius:7px;border:none;background:#7A2BF5;color:#fff;font-size:12.5px;font-weight:600;cursor:pointer;" hover="background:#6412E0;" onClick={startCheckoutSourceOnly}>+ Checkout new table</Hv>
                    </div>
                  </div>
                  <div style={s("font-size:12.5px;color:#9C9CAA;margin-bottom:20px;")}>Every table you've checked out or file you've imported, in one place. Any source here can be the primary table or a join input in any workspace.</div>
                  {st.sourceActionMsg && (
                    <div style={s("font-size:12.5px;color:#17171F;background:#F1EDFF;border-radius:8px;padding:10px 12px;margin-bottom:16px;")}>{st.sourceActionMsg}</div>
                  )}
                  {st.sourcesLoading && st.sourcesList.length === 0 && <div style={s("font-size:13px;color:#9C9CAA;")}>Loading…</div>}
                  {!st.sourcesLoading && st.sourcesList.length === 0 && (
                    <div style={s("font-size:13px;color:#9C9CAA;")}>No sources yet. Checkout a table or import a file to see it here.</div>
                  )}
                  <div style={s("display:flex;flex-direction:column;gap:10px;max-width:760px;")}>
                    {st.sourcesList.map((src) => {
                      const busy = st.sourceActionBusy === src.source_id;
                      const kindLabel = src.kind === "uc_table" ? "Unity Catalog" : src.kind.toUpperCase();
                      return (
                        <div key={src.source_id} style={s("background:#FFFFFF;border:1px solid #E4E1ED;border-radius:10px;padding:14px 16px;display:flex;align-items:center;justify-content:space-between;gap:12px;")}>
                          <div style={s("min-width:0;")}>
                            <div style={s("font-size:14px;font-weight:600;font-family:'Space Grotesk',sans-serif;")}>{src.name}</div>
                            <div style={s("font-size:11.5px;color:#9C9CAA;margin-top:2px;")}>
                              {kindLabel} · {src.columns.length} columns · {src.row_count.toLocaleString()} rows · {(src.logical_bytes / 1024 / 1024).toFixed(1)} MB
                            </div>
                            <div style={s("font-size:11px;color:#C4C4CE;margin-top:2px;")}>
                              {src.uc_table ? <span style={s(`font-family:${MONO};`)}>{src.uc_table} · </span> : null}
                              refreshed {new Date(src.refreshed_at).toLocaleString()}
                            </div>
                          </div>
                          <div style={s("display:flex;gap:8px;flex-shrink:0;")}>
                            <Hv tag="button" css="height:30px;padding:0 12px;border-radius:6px;border:1px solid #7A2BF5;background:#FFFFFF;color:#7A2BF5;font-size:12px;font-weight:600;cursor:pointer;" hover="background:#F1EDFF;" onClick={() => startNotebookFromSource(src.source_id, src.name)}>
                              {busy ? "…" : "Use in new workspace"}
                            </Hv>
                            {src.refreshable && (
                              <Hv tag="button" css="height:30px;padding:0 12px;border-radius:6px;border:1px solid #E5E5ED;background:#FFFFFF;color:#6B6B7A;font-size:12px;font-weight:600;cursor:pointer;" hover="background:#F5F0FF;" onClick={() => refreshSourceHandler(src.source_id, src.name)}>
                                {busy ? "…" : "Refresh"}
                              </Hv>
                            )}
                            <Hv css="height:30px;width:30px;border-radius:6px;display:flex;align-items:center;justify-content:center;color:#9C9CAA;font-size:13px;cursor:pointer;" hover="background:#FFE3E9;color:#FF3B5C;" onClick={() => deleteSourceHandler(src.source_id, src.name)}>✕</Hv>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </>
              )}
              {screen === "home" && (
                <>
                  <div style={s("display:flex;align-items:center;justify-content:space-between;margin-bottom:28px;")}>
                    <div style={s("font-size:22px;font-weight:600;font-family:'Space Grotesk',sans-serif;")}>Workspaces</div>
                    <Hv tag="button" css="height:38px;padding:0 16px;border-radius:8px;border:1px solid #7A2BF5;background:#FFFFFF;color:#7A2BF5;font-size:13.5px;font-weight:600;cursor:pointer;" hover="background:#F1EDFF;" onClick={startNewWorkspace}>+ New Workspace</Hv>
                  </div>
                  <div style={s("display:flex;flex-wrap:wrap;gap:16px;")}>
                    {!live && (
                      <Hv css="width:300px;background:#FFFFFF;border:1px solid #E4E1ED;border-radius:12px;padding:20px;cursor:pointer;display:flex;flex-direction:column;gap:10px;" hover="border-color:#7A2BF5;box-shadow:0 8px 24px -12px rgba(122,43,245,0.25);" onClick={openExistingWorkspace}>
                        <div style={s("font-size:15px;font-weight:600;font-family:'Space Grotesk',sans-serif;")}>Customers Cleanup</div>
                        <div style={s("font-size:12px;color:#9C9CAA;")}>Last opened 15 Aug 2026 17:20</div>
                        <div style={s("display:flex;flex-direction:column;gap:4px;font-size:12.5px;color:#6B6B7A;margin-top:4px;")}>
                          <div>Source: <span style={s("color:#17171F;")}>main.crm.customers</span></div>
                          <div>Checkout: <span style={s("color:#17171F;")}>v481</span></div>
                          <div>Steps: <span style={s("color:#17171F;")}>6</span> &nbsp;·&nbsp; Local data: <span style={s("color:#17171F;")}>482 MB</span></div>
                        </div>
                      </Hv>
                    )}
                    {live &&
                      st.savedWorkspaces.map((w) => (
                        <Hv key={w.workspace_id} css="width:300px;background:#FFFFFF;border:1px solid #E4E1ED;border-radius:12px;padding:20px;cursor:pointer;display:flex;flex-direction:column;gap:10px;position:relative;" hover="border-color:#7A2BF5;box-shadow:0 8px 24px -12px rgba(122,43,245,0.25);" onClick={() => openLiveWorkspace(w.workspace_id, w.name, w.source_table)}>
                          <Hv
                            css="position:absolute;top:12px;right:12px;width:26px;height:26px;border-radius:6px;display:flex;align-items:center;justify-content:center;color:#9C9CAA;font-size:12px;cursor:pointer;"
                            hover="background:#FFE3E9;color:#FF3B5C;"
                            onClick={(e: any) => {
                              e.stopPropagation();
                              if (confirm(`Delete workspace "${w.name}"? This does not delete the underlying source data.`)) deleteWorkspaceHandler(w.workspace_id, w.name);
                            }}
                          >
                            {st.wsActionBusy === w.workspace_id ? "…" : "✕"}
                          </Hv>
                          <div style={s("font-size:15px;font-weight:600;font-family:'Space Grotesk',sans-serif;")}>{w.name}</div>
                          <div style={s("font-size:12px;color:#9C9CAA;")}>{new Date(w.created_at).toLocaleString()}</div>
                          <div style={s("display:flex;flex-direction:column;gap:4px;font-size:12.5px;color:#6B6B7A;margin-top:4px;")}>
                            <div>Source: <span style={s(`color:#17171F;font-family:${MONO};`)}>{w.source_table || "—"}</span></div>
                            <div>Rows: <span style={s("color:#17171F;")}>{w.row_count.toLocaleString()}</span> &nbsp;·&nbsp; Local: <span style={s("color:#17171F;")}>{(w.logical_bytes / 1024 / 1024).toFixed(1)} MB</span></div>
                          </div>
                        </Hv>
                      ))}
                    <Hv css="width:300px;border:1.5px dashed #D4D4E0;border-radius:12px;padding:20px;cursor:pointer;display:flex;align-items:center;justify-content:center;color:#9C9CAA;font-size:13.5px;min-height:112px;" hover="border-color:#7A2BF5;color:#7A2BF5;" onClick={startNewWorkspace}>+ New Workspace</Hv>
                  </div>
                </>
              )}

              {screen === "watched" && !live && (
                <>
                  <div style={s("font-size:22px;font-weight:600;font-family:'Space Grotesk',sans-serif;margin-bottom:24px;")}>Watched Folder</div>
                  <div style={s("background:#FFFFFF;border:1px solid #E4E1ED;border-radius:12px;padding:24px;max-width:520px;display:flex;flex-direction:column;gap:16px;")}>
                    <div style={s("display:flex;justify-content:space-between;align-items:center;")}>
                      <div style={s(`font-family:${MONO};font-size:13.5px;`)}>~/Company Data/Inbox</div>
                      <div style={s("display:flex;align-items:center;gap:6px;font-size:12.5px;color:#00B36B;font-weight:600;")}>
                        <div style={s("width:7px;height:7px;border-radius:50%;background:#00B36B;animation:ldw-pulse 1.6s ease-in-out infinite;")} />Watching
                      </div>
                    </div>
                    <div style={s("display:flex;gap:6px;")}>
                      {["CSV", "XLSX", "Parquet"].map((t) => (
                        <div key={t} style={s("font-size:11.5px;color:#6B6B7A;background:#F5F0FF;border:1px solid #E5E5ED;border-radius:6px;padding:3px 8px;")}>{t}</div>
                      ))}
                    </div>
                    <div style={s("border-top:1px solid #E5E5ED;padding-top:16px;display:flex;flex-direction:column;gap:10px;")}>
                      <div style={s("font-size:11.5px;color:#9C9CAA;font-weight:600;letter-spacing:0.03em;")}>DETECTED</div>
                      <div style={s("display:flex;align-items:center;justify-content:space-between;background:#F5F0FF;border:1px solid #E5E5ED;border-radius:8px;padding:12px 14px;")}>
                        <div>
                          <div style={s("font-size:13.5px;font-weight:600;")}>customer_mapping.xlsx</div>
                          <div style={s("font-size:12px;color:#6B6B7A;margin-top:2px;")}>42,812 rows · 6 columns</div>
                        </div>
                        <Hv tag="button" css="height:32px;padding:0 12px;border-radius:7px;border:none;background:#7A2BF5;color:#fff;font-size:12.5px;font-weight:600;cursor:pointer;" hover="background:#6412E0;" onClick={useDetectedFile}>Use in workspace</Hv>
                      </div>
                    </div>
                  </div>
                </>
              )}

              {screen === "watched" && live && (
                <>
                  <div style={s("font-size:22px;font-weight:600;font-family:'Space Grotesk',sans-serif;margin-bottom:24px;")}>Watched Folder</div>
                  <div style={s("background:#FFFFFF;border:1px solid #E4E1ED;border-radius:12px;padding:24px;max-width:640px;display:flex;flex-direction:column;gap:16px;")}>
                    <div>
                      <div style={s("font-size:11.5px;color:#9C9CAA;font-weight:600;margin-bottom:6px;")}>FOLDER</div>
                      <div style={s("display:flex;gap:8px;")}>
                        <input value={st.watchFolder} onChange={(e) => setState({ watchFolder: e.target.value })} style={s(`flex:1;height:36px;border:1px solid #E5E5ED;border-radius:7px;padding:0 10px;font-size:13px;font-family:${MONO};`)} />
                        <Hv tag="button" css="height:36px;padding:0 16px;border-radius:7px;border:none;background:#7A2BF5;color:#fff;font-size:13px;font-weight:600;cursor:pointer;" hover="background:#6412E0;" onClick={scanWatch}>Scan</Hv>
                      </div>
                      <div style={s("font-size:11.5px;color:#9C9CAA;margin-top:6px;")}>Supported: CSV · XLSX · Parquet</div>
                    </div>

                    <div style={s("font-size:11.5px;color:#9C9CAA;")}>Imported files land in your shared <span onClick={openSources} style={s("color:#7A2BF5;cursor:pointer;text-decoration:underline;")}>Sources</span> — usable in any workspace's Join File step, or to start a new one.</div>

                    {st.watchMsg && <div style={s("font-size:12.5px;color:#6B6B7A;background:#F5F0FF;border-radius:8px;padding:10px 12px;")}>{st.watchMsg}</div>}

                    <div style={s("border-top:1px solid #E5E5ED;padding-top:16px;display:flex;flex-direction:column;gap:10px;")}>
                      <div style={s("font-size:11.5px;color:#9C9CAA;font-weight:600;letter-spacing:0.03em;")}>DETECTED FILES</div>
                      {st.watchFiles.length === 0 && <div style={s("font-size:12.5px;color:#9C9CAA;")}>Nothing scanned yet. Set a folder and press Scan.</div>}
                      {st.watchFiles.map((f) => (
                        <div key={f.path} style={s("display:flex;align-items:center;justify-content:space-between;background:#F5F0FF;border:1px solid #E5E5ED;border-radius:8px;padding:12px 14px;")}>
                          <div>
                            <div style={s("font-size:13.5px;font-weight:600;")}>{f.name}</div>
                            <div style={s("font-size:12px;color:#6B6B7A;margin-top:2px;")}>{f.format.toUpperCase()} · {(f.size / 1024).toFixed(1)} KB · {f.stable ? "ready" : "writing…"}</div>
                          </div>
                          <Hv tag="button" css={`height:32px;padding:0 12px;border-radius:7px;border:none;font-size:12.5px;font-weight:600;cursor:pointer;background:${f.stable ? "#7A2BF5" : "#C4C4CE"};color:#fff;`} hover={f.stable ? "background:#6412E0;" : ""} onClick={() => f.stable && useWatchedFile(f.path)}>Use in workspace</Hv>
                        </div>
                      ))}
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        )}

        {screen === "browser" && (
          <div style={s("width:100%;height:100%;display:flex;align-items:center;justify-content:center;")}>
            <div style={s("width:440px;background:#FFFFFF;border:1px solid #E4E1ED;border-radius:12px;padding:24px;box-shadow:0 12px 40px -12px rgba(20,20,40,0.12);")}>
              <div style={s("display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;")}>
                <div style={s("font-size:16px;font-weight:600;font-family:'Space Grotesk',sans-serif;")}>Unity Catalog</div>
                <div onClick={cancelFlow} style={s("font-size:12.5px;color:#6B6B7A;cursor:pointer;")}>Cancel</div>
              </div>
              {!live && (
                <>
                  <div style={s("font-size:13px;font-weight:600;color:#17171F;padding:6px 4px;")}>▾ main</div>
                  <div style={s("font-size:13px;font-weight:500;color:#6B6B7A;padding:4px 4px 4px 18px;")}>▾ crm</div>
                  <div onClick={() => selectTable("main.crm.customers")} style={s("padding:7px 4px 7px 34px;font-size:13px;border-radius:6px;cursor:pointer;color:#17171F;")}>customers</div>
                  <div style={s("padding:7px 4px 7px 34px;font-size:13px;border-radius:6px;color:#C4C4CE;cursor:default;")}>accounts</div>
                  <div style={s("font-size:13px;font-weight:500;color:#6B6B7A;padding:4px 4px 4px 18px;")}>▾ finance</div>
                  <div style={s("padding:7px 4px 7px 34px;font-size:13px;border-radius:6px;color:#C4C4CE;cursor:default;")}>transactions</div>
                  <div style={s("padding:7px 4px 7px 34px;font-size:13px;border-radius:6px;color:#C4C4CE;cursor:default;")}>forecast</div>
                </>
              )}
              {live && (
                <div style={s("max-height:360px;overflow:auto;")}>
                  {st.browse.loading && st.browse.catalogs.length === 0 && (
                    <div style={s("font-size:12.5px;color:#9C9CAA;padding:8px 4px;")}>Loading catalogs…</div>
                  )}
                  {st.browse.catalogs.map((cat) => {
                    const catOpen = st.browse.openCat === cat.name;
                    return (
                      <div key={cat.name}>
                        <Hv css="font-size:13px;font-weight:600;color:#17171F;padding:6px 4px;border-radius:6px;cursor:pointer;" hover="background:#F1EDFF;" onClick={() => openCatalog(cat.name)}>
                          {catOpen ? "▾" : "▸"} {cat.name}
                        </Hv>
                        {catOpen &&
                          (st.browse.schemas[cat.name] ?? []).map((sch) => {
                            const key = `${cat.name}.${sch.name}`;
                            const schOpen = st.browse.openSch === key;
                            return (
                              <div key={key}>
                                <Hv css="font-size:13px;font-weight:500;color:#6B6B7A;padding:4px 4px 4px 18px;border-radius:6px;cursor:pointer;" hover="background:#F1EDFF;" onClick={() => openSchema(cat.name, sch.name)}>
                                  {schOpen ? "▾" : "▸"} {sch.name}
                                </Hv>
                                {schOpen &&
                                  (st.browse.tables[key] ?? []).map((tbl) => (
                                    <Hv key={tbl.full_name} css="padding:7px 4px 7px 34px;font-size:13px;border-radius:6px;cursor:pointer;color:#17171F;" hover="background:#F1EDFF;color:#7A2BF5;" onClick={() => selectTable(tbl.full_name)}>
                                      {tbl.name}
                                    </Hv>
                                  ))}
                                {schOpen && (st.browse.tables[key] ?? []).length === 0 && (
                                  <div style={s("padding:6px 4px 6px 34px;font-size:12px;color:#C4C4CE;")}>no tables</div>
                                )}
                              </div>
                            );
                          })}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        )}

        {screen === "workingset" && (
          <div style={s("width:100%;height:100%;display:flex;align-items:center;justify-content:center;overflow:auto;")}>
            <div style={s(`width:${live ? 640 : 480}px;background:#FFFFFF;border:1px solid #E4E1ED;border-radius:12px;padding:28px;margin:20px;box-shadow:0 12px 40px -12px rgba(20,20,40,0.12);`)}>
              <div style={s("font-size:16px;font-weight:600;font-family:'Space Grotesk',sans-serif;margin-bottom:2px;")}>
                {live && st.importMode ? "Import Table as Join Source" : live && st.checkoutOnly ? "Checkout Table (add to Sources)" : "Define Working Set"}
              </div>
              <div style={s("display:flex;align-items:baseline;gap:10px;margin-bottom:20px;")}>
                <div style={s(`font-size:12.5px;color:#6B6B7A;font-family:${MONO};`)}>{nf.tableName || ""}</div>
                {live && (
                  <div style={s("font-size:11.5px;color:#9C9CAA;")}>
                    {st.tableTotalRowCount === null ? "loading row count…" : <>{st.tableTotalRowCount.toLocaleString()} rows in table (unfiltered)</>}
                  </div>
                )}
              </div>

              <div style={s("font-size:12px;color:#9C9CAA;font-weight:600;letter-spacing:0.03em;margin-bottom:8px;")}>COLUMNS</div>
              <div style={s("display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px;")}>
                {Object.keys(nf.columns).map((key) => (
                  <div key={key} onClick={() => toggleColumn(key)} style={s(`font-size:12.5px;padding:6px 10px;border-radius:16px;cursor:pointer;border:1px solid ${nf.columns[key] ? "#DCD3FF" : "#E5E5ED"};background:${nf.columns[key] ? "#F1EDFF" : "#FFFFFF"};color:${nf.columns[key] ? "#7A2BF5" : "#9C9CAA"};`)}>
                    {nf.columns[key] ? "✓" : "✕"} {key}
                  </div>
                ))}
              </div>

              {live && (
                <>
                  <div style={s("font-size:12px;color:#9C9CAA;font-weight:600;letter-spacing:0.03em;margin-bottom:8px;")}>SAMPLE DATA {st.checkoutSampleLoading ? "· loading…" : st.checkoutSample ? `· first ${st.checkoutSample.rows.length} rows` : ""}</div>
                  {st.checkoutSampleError ? (
                    <div style={s("font-size:12px;color:#FF3B5C;margin-bottom:20px;")}>{st.checkoutSampleError}</div>
                  ) : (
                    <div style={s("overflow:auto;max-height:220px;border:1px solid #E5E5ED;border-radius:8px;margin-bottom:20px;")}>
                      <table style={s("border-collapse:collapse;font-size:12px;width:100%;")}>
                        <thead>
                          <tr style={s("position:sticky;top:0;background:#FBFAFF;box-shadow:0 1px 0 #E5E5ED;")}>
                            {(st.checkoutSample?.columns ?? []).map((c) => (
                              <th key={c} style={s("text-align:left;padding:6px 10px;font-weight:600;color:#6B6B7A;font-size:10.5px;white-space:nowrap;")}>{c}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {(st.checkoutSample?.rows ?? []).map((r, ri) => (
                            <tr key={ri} style={s("border-top:1px solid #F0F0F5;")}>
                              {r.map((v, ci) => (
                                <td key={ci} style={s(`padding:6px 10px;white-space:nowrap;color:${v === null || v === undefined ? "#C4C4CE" : "#17171F"};font-family:${typeof v === "number" ? MONO : "inherit"};`)}>{v === null || v === undefined ? "—" : String(v)}</td>
                              ))}
                            </tr>
                          ))}
                          {!st.checkoutSampleLoading && st.checkoutSample && st.checkoutSample.rows.length === 0 && (
                            <tr><td style={s("padding:10px;color:#9C9CAA;")} colSpan={Math.max(1, st.checkoutSample.columns.length)}>No rows match the current filters.</td></tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              )}

              {!live && (
                <>
                  <div style={s("font-size:12px;color:#9C9CAA;font-weight:600;letter-spacing:0.03em;margin-bottom:8px;")}>FILTERS</div>
                  <div style={s(`background:#F5F0FF;border:1px solid #E5E5ED;border-radius:8px;padding:10px 12px;font-size:12.5px;font-family:${MONO};color:#17171F;margin-bottom:20px;`)}>
                    <div>country = Netherlands</div>
                    <div>updated_at &gt;= 2026-01-01</div>
                  </div>
                </>
              )}

              {live && (
                <>
                  <div style={s("font-size:12px;color:#9C9CAA;font-weight:600;letter-spacing:0.03em;margin-bottom:8px;")}>FILTERS</div>
                  <div style={s("display:flex;flex-direction:column;gap:8px;margin-bottom:12px;")}>
                    {nf.filters.length === 0 && (
                      <div style={s("font-size:12px;color:#9C9CAA;")}>No filters — the full table is checked out. Add a condition to reduce it in Databricks before download.</div>
                    )}
                    {nf.filters.map((f, i) => (
                      <div key={i} style={s("display:flex;gap:8px;align-items:center;")}>
                        <select value={f.column} onChange={(e) => setFilterField(i, "column", e.target.value)} style={s("flex:1;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 8px;font-size:13px;")}>
                          {Object.keys(nf.columns).map((c) => <option key={c} value={c}>{c}</option>)}
                        </select>
                        <select value={f.op} onChange={(e) => setFilterField(i, "op", e.target.value)} style={s("flex:1;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 8px;font-size:13px;")}>
                          {FILTER_OPERATORS.map((o) => <option key={o} value={o}>{o}</option>)}
                        </select>
                        <input
                          value={f.value}
                          disabled={VALUELESS_OPS.has(f.op)}
                          placeholder={VALUELESS_OPS.has(f.op) ? "—" : "value"}
                          onChange={(e) => setFilterField(i, "value", e.target.value)}
                          style={s(`flex:1;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 10px;font-size:13px;background:${VALUELESS_OPS.has(f.op) ? "#F5F0FF" : "#fff"};`)}
                        />
                        <Hv css="flex-shrink:0;width:28px;height:28px;border-radius:6px;display:flex;align-items:center;justify-content:center;color:#9C9CAA;font-size:14px;cursor:pointer;" hover="background:#FFE3E9;color:#FF3B5C;" onClick={() => removeFilter(i)}>✕</Hv>
                      </div>
                    ))}
                  </div>
                  <div onClick={addFilter} style={s("font-size:12.5px;color:#7A2BF5;cursor:pointer;margin-bottom:12px;")}>+ Add filter</div>
                  {(st.checkoutEstimating || st.checkoutEstimate !== null) && (
                    <div style={s("font-size:12.5px;color:#6B6B7A;margin-bottom:20px;display:flex;align-items:center;gap:6px;")}>
                      {st.checkoutEstimating ? (
                        <>
                          <div style={s("width:11px;height:11px;border:2px solid #E5E5ED;border-top-color:#7A2BF5;border-radius:50%;animation:ldw-spin 0.7s linear infinite;")} /> Estimating rows…
                        </>
                      ) : (
                        <>≈ <b style={s("color:#17171F;")}>{st.checkoutEstimate!.toLocaleString()}</b> rows will be checked out{st.checkoutEstimate! > 500000 ? " — this may take a minute" : ""}</>
                      )}
                    </div>
                  )}
                </>
              )}

              {live && st.checkoutOnly && (
                <div style={s("font-size:12px;color:#9C9CAA;background:#F5F0FF;border-radius:8px;padding:10px 12px;margin-bottom:20px;")}>This checkout only creates a Source — pick a row identifier later when you open it in a workspace.</div>
              )}
              {live && !st.importMode && !st.checkoutOnly ? (
                <>
                  <div style={s("font-size:12px;color:#9C9CAA;font-weight:600;letter-spacing:0.03em;margin-bottom:4px;")}>ROW IDENTIFIER</div>
                  <div style={s("font-size:11.5px;color:#9C9CAA;margin-bottom:8px;")}>Pick one or more columns that uniquely identify a row — needed to track changes and publish later.</div>
                  <div style={s("display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px;")}>
                    {Object.keys(nf.columns).filter((k) => nf.columns[k]).map((key) => {
                      const checked = nf.rowKeyCols.includes(key);
                      return (
                        <div
                          key={key}
                          onClick={() => setState((p) => ({ newFlow: { ...p.newFlow, rowKeyCols: checked ? p.newFlow.rowKeyCols.filter((k) => k !== key) : [...p.newFlow.rowKeyCols, key] } }))}
                          style={s(`font-size:12.5px;padding:6px 10px;border-radius:16px;cursor:pointer;border:1px solid ${checked ? "#DCD3FF" : "#E5E5ED"};background:${checked ? "#F1EDFF" : "#FFFFFF"};color:${checked ? "#7A2BF5" : "#9C9CAA"};`)}
                        >
                          {checked ? "✓" : "○"} {key}
                        </div>
                      );
                    })}
                  </div>
                  {nf.rowKeyCols.length === 0 && (
                    <div style={s("font-size:12px;color:#FF9F1C;background:#FFF3E0;border-radius:8px;padding:8px 10px;margin-bottom:20px;margin-top:-12px;")}>No row identifier picked yet — you can add one later, but you won't be able to publish changes until you do.</div>
                  )}
                </>
              ) : !live && !st.importMode ? (
                <>
                  <div style={s("font-size:12px;color:#9C9CAA;font-weight:600;letter-spacing:0.03em;margin-bottom:8px;")}>ROW IDENTIFIER</div>
                  <div style={s("display:flex;gap:16px;margin-bottom:20px;")}>
                    <div onClick={setRowIdSingle} style={s("display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;")}><div style={s(dot(nf.rowId === "single"))} /> customer_id</div>
                    <div onClick={setRowIdComposite} style={s("display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;color:#6B6B7A;")}><div style={s(dot(nf.rowId === "composite"))} /> Composite key</div>
                  </div>
                </>
              ) : null}

              <div style={s("display:flex;justify-content:space-between;align-items:center;background:#F1EDFF;border-radius:8px;padding:14px 16px;margin-bottom:20px;")}>
                <div style={s("font-size:12.5px;color:#6B6B7A;")}>{live ? "Governed SELECT" : "Estimated output"}</div>
                <div style={s("font-size:14px;font-weight:600;color:#17171F;")}>
                  {live ? `${checkedCount} columns · ${nf.filters.length} filter${nf.filters.length === 1 ? "" : "s"} · warehouse ${st.warehouseId ? "ready" : "not set"}` : `428,221 rows · ${estimatedSizeLabel}`}
                </div>
              </div>

              {st.checkoutError && (
                <div style={s("font-size:12px;color:#FF3B5C;background:#FFE9EE;border-radius:8px;padding:10px 12px;margin-bottom:16px;")}>{st.checkoutError}</div>
              )}

              <div style={s("display:flex;justify-content:flex-end;gap:10px;")}>
                <div onClick={cancelFlow} style={s("height:38px;padding:0 14px;display:flex;align-items:center;font-size:13px;color:#6B6B7A;cursor:pointer;")}>Cancel</div>
                <Hv tag="button" css="height:38px;padding:0 20px;border-radius:8px;border:none;background:#7A2BF5;color:#fff;font-size:13.5px;font-weight:600;cursor:pointer;" hover="background:#6412E0;" onClick={runCheckout}>{live && st.importMode ? "Import" : "Checkout"}</Hv>
              </div>
            </div>
          </div>
        )}

        {screen === "checkout" && (
          <div style={s("width:100%;height:100%;display:flex;align-items:center;justify-content:center;")}>
            <div style={s("width:360px;display:flex;flex-direction:column;align-items:center;gap:16px;")}>
              <div style={s("width:100%;height:8px;background:#E5E5ED;border-radius:5px;overflow:hidden;")}>
                <div style={{ ...s("height:100%;background:#7A2BF5;border-radius:5px;transition:width 0.25s ease;"), width: cp + "%" }} />
              </div>
              <div style={s("font-size:13px;color:#6B6B7A;")}>{live && st.checkoutMsg ? st.checkoutMsg : checkoutStatusText}</div>
            </div>
          </div>
        )}

        {screen === "workspace" && ws && (
          <div style={s("width:100%;height:100%;display:flex;flex-direction:column;")}>
            <div style={s("height:56px;min-height:56px;border-bottom:1px solid #E5E5ED;background:#FFFFFF;display:flex;align-items:center;justify-content:space-between;padding:0 20px;")}>
              <div style={s("display:flex;align-items:center;gap:14px;")}>
                <Hv css="font-size:12.5px;color:#9C9CAA;cursor:pointer;" hover="color:#7A2BF5;" onClick={goHome}>← Home</Hv>
                <div style={s("width:1px;height:18px;background:#E5E5ED;")} />
                <div style={s("font-size:15px;font-weight:600;font-family:'Space Grotesk',sans-serif;")}>{ws.name}</div>
                <div style={s("display:flex;align-items:center;gap:5px;font-size:11.5px;color:#6B6B7A;background:#F5F0FF;border:1px solid #E4E1ED;border-radius:6px;padding:3px 8px;")}>
                  <div style={s("width:6px;height:6px;border-radius:50%;background:#7A2BF5;")} /> Local
                </div>
                <div style={s("font-size:11.5px;color:#00B36B;background:#DFFFF0;border-radius:6px;padding:3px 8px;")}>{ws.committed ? "Committed ✓" : "Saved ✓"}</div>
              </div>
              <div style={s("display:flex;align-items:center;gap:10px;")}>
                {isLiveWs ? (
                  <>
                    <div style={s("display:flex;align-items:center;gap:6px;")}>
                      <span style={s("font-size:11.5px;color:#9C9CAA;")}>Row key</span>
                      <select value={st.rowKey[0] || ""} onChange={(e) => setRowKey(e.target.value)} style={s(`height:30px;border:1px solid ${st.rowKeyUnique === false ? "#FF3B5C" : "#E5E5ED"};border-radius:6px;padding:0 6px;font-size:12.5px;`)}>
                        <option value="">— none —</option>
                        {st.rowKeyColumns.map((c) => <option key={c} value={c}>{c}</option>)}
                      </select>
                      {st.rowKeyUnique === true && <span style={s("font-size:12px;color:#00B36B;")}>✓ unique</span>}
                      {st.rowKeyUnique === false && <span style={s("font-size:12px;color:#FF3B5C;")}>✕ not unique</span>}
                    </div>
                    <Hv tag="button" css="height:34px;padding:0 12px;border-radius:7px;border:1px solid #E5E5ED;background:#FFFFFF;color:#6B6B7A;font-size:12.5px;font-weight:600;cursor:pointer;" hover="background:#F1EDFF;color:#7A2BF5;" onClick={openValidate}>Validate</Hv>
                    <Hv tag="button" css="height:34px;padding:0 14px;border-radius:7px;border:1px solid #7A2BF5;background:#FFFFFF;color:#7A2BF5;font-size:12.5px;font-weight:600;cursor:pointer;" hover="background:#F1EDFF;" onClick={openReviewDiff}>Review changes</Hv>
                  </>
                ) : (
                  <>
                    <div style={s("font-size:12px;color:#9C9CAA;")}>Source: Unity Catalog v{ws.version}</div>
                    {ws.changes > 0 && (
                      <Hv tag="button" css="height:34px;padding:0 14px;border-radius:7px;border:1px solid #7A2BF5;background:#FFFFFF;color:#7A2BF5;font-size:12.5px;font-weight:600;cursor:pointer;" hover="background:#F1EDFF;" onClick={openReviewDiff}>{ws.changes} changes · Review</Hv>
                    )}
                  </>
                )}
              </div>
            </div>

            {isLiveWs && (
              <div style={s("height:44px;min-height:44px;background:#FFFFFF;border-bottom:1px solid #E5E5ED;display:flex;align-items:center;gap:4px;padding:0 20px;")}>
                {(["data", "explore", "workflow"] as const).map((tab) => (
                  <Hv
                    key={tab}
                    css={`height:44px;padding:0 14px;display:flex;align-items:center;font-size:13px;font-weight:600;cursor:pointer;color:${st.workspaceTab === tab ? "#7A2BF5" : "#6B6B7A"};border-bottom:2px solid ${st.workspaceTab === tab ? "#7A2BF5" : "transparent"};`}
                    hover="color:#7A2BF5;"
                    onClick={() => setWorkspaceTab(tab)}
                  >
                    {tab === "data" ? "Data" : tab === "explore" ? "Explore" : "Workflow"}
                  </Hv>
                ))}
              </div>
            )}

            {isLiveWs && st.workspaceTab === "data" && (
              <div style={s("flex:1;min-height:0;display:flex;flex-direction:column;")}>
                {renderDataGrid()}
              </div>
            )}

            {isLiveWs && st.workspaceTab === "explore" && (
              <div style={s("flex:1;min-height:0;overflow:auto;padding:24px 28px;")}>
                <div style={s("max-width:820px;display:flex;flex-direction:column;gap:16px;")}>
                  {renderAskBar()}
                  {renderAnalysisBuilder()}
                  {renderRecipes()}
                </div>
              </div>
            )}

            {(!isLiveWs || st.workspaceTab === "workflow") && (
            <div style={s("flex:1;min-height:0;display:flex;")}>
              {/* Pipeline panel */}
              <div style={s("width:270px;min-width:270px;border-right:1px solid #E5E5ED;background:#FFFFFF;display:flex;flex-direction:column;padding:16px 12px;overflow:auto;")}>
                <div style={s("font-size:11.5px;font-weight:600;color:#9C9CAA;letter-spacing:0.03em;padding:0 8px 10px;")}>TRANSFORM</div>
                {ws.pipeline.map((step, i) => {
                  const isSelected = i === selIdx;
                  const done = i < selIdx;
                  const stepErr = isLiveWs ? st.stepErrors[step.id] : null;
                  const icon = stepErr ? "✕" : done ? "✓" : isSelected ? "●" : "○";
                  const iconColor = stepErr ? "#FF3B5C" : done ? "#00B36B" : isSelected ? "#7A2BF5" : "#C4C4CE";
                  const rowsAtStep = isLiveWs
                    ? st.stepCounts[step.id]
                    : computeRows(ws.pipeline.slice(0, i + 1), ws.sourceRows, ws.sourceCols).rows.length;
                  const configurable = isLiveWs ? !!LIVE_MODAL_FOR[step.type] : !!MODAL_FOR_TYPE[step.type];
                  const inputId = i > 0 ? resolvedInputId(ws.pipeline, i) : null;
                  const isBranch = i > 0 && inputId !== defaultInputId(ws.pipeline, i);
                  const earlierSteps = ws.pipeline.slice(0, i);
                  return (
                    <div key={step.id}>
                      {isBranch && (
                        <div style={s("font-size:10.5px;color:#7A2BF5;padding:0 8px 2px 34px;")}>
                          ⤷ branches from {stepDisplayLabel(ws.pipeline, inputId)}
                        </div>
                      )}
                      <Hv
                        css={`display:flex;align-items:flex-start;gap:8px;padding:9px 8px;border-radius:8px;cursor:pointer;background:${isSelected ? "#F1EDFF" : "transparent"};border-left:3px solid ${isBranch ? "#FF9F1C" : isSelected ? "#7A2BF5" : "transparent"};`}
                        onClick={() => { selectStep(i); if (step.type === "sql_transform") editSqlStep(step, i); else if (configurable) openStepModal(step); }}
                      >
                        <div style={s(`font-size:12px;color:${iconColor};width:16px;flex-shrink:0;padding-top:2px;`)}>{icon}</div>
                        <div style={s("flex:1;min-width:0;")}>
                          <div style={s(`font-size:13px;font-weight:${isSelected ? 600 : 500};color:#17171F;`)}>{STEP_LABELS[step.type] || step.type}</div>
                          <div style={s("font-size:11.5px;color:#9C9CAA;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;")} title={stepErr || undefined}>
                            {stepErr ? <span style={s("color:#FF3B5C;")}>{stepErr}</span> : <>{step.summary}{rowsAtStep != null ? ` · ${fmtNum(rowsAtStep)} rows` : ""}</>}
                          </div>
                          {isLiveWs && i > 0 && earlierSteps.length > 0 && (
                            <select
                              value={inputId ?? ""}
                              onClick={(e) => e.stopPropagation()}
                              onChange={(e) => setStepInput(step.id, e.target.value)}
                              style={s("margin-top:5px;height:22px;font-size:10.5px;border:1px solid #E5E5ED;border-radius:5px;padding:0 4px;color:#6B6B7A;max-width:100%;")}
                            >
                              {earlierSteps.map((es) => (
                                <option key={es.id} value={es.id}>Input: {stepDisplayLabel(ws.pipeline, es.id)}</option>
                              ))}
                            </select>
                          )}
                        </div>
                        {i > 0 && (
                          <Hv css="flex-shrink:0;width:20px;height:20px;border-radius:5px;display:flex;align-items:center;justify-content:center;color:#9C9CAA;font-size:13px;cursor:pointer;" hover="background:#FFE3E9;color:#FF3B5C;" onClick={(e) => { e.stopPropagation(); removeStep(step.id); }}>✕</Hv>
                        )}
                      </Hv>
                    </div>
                  );
                })}
                <div style={s("position:relative;margin-top:4px;")}>
                  <Hv css="padding:10px 8px;border-radius:8px;font-size:13px;color:#7A2BF5;cursor:pointer;font-weight:500;" hover="background:#F1EDFF;" onClick={toggleAddStep}>+ Add step</Hv>
                  {st.addStepOpen && (
                    <div style={s("position:absolute;top:36px;left:8px;width:200px;background:#FFFFFF;border:1px solid #E4E1ED;border-radius:10px;box-shadow:0 12px 32px -8px rgba(20,20,40,0.18);padding:6px;z-index:20;")}>
                      {(isLiveWs ? LIVE_STEP_TYPES : ADD_STEP_ORDER).map((type) => (
                        <Hv key={type} css="padding:8px 10px;border-radius:6px;font-size:13px;cursor:pointer;" hover="background:#F1EDFF;" onClick={() => addStepType(type)}>{STEP_LABELS[type]}</Hv>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* Data preview */}
              <div style={s("flex:1;min-width:0;display:flex;flex-direction:column;")}>
                {renderDataGrid()}
              </div>
            </div>
            )}
          </div>
        )}
      </div>

      {/* Column stats popover (P9.12) */}
      {st.statsCol && st.statsAnchor && (
        <>
          <div style={s("position:fixed;inset:0;z-index:150;")} onClick={closeColumnStats} />
          <div
            style={{
              ...s("position:fixed;z-index:151;width:260px;background:#FFFFFF;border:1px solid #E4E1ED;border-radius:10px;box-shadow:0 12px 32px -8px rgba(20,20,40,0.22);padding:14px 16px;"),
              left: Math.min(st.statsAnchor.x, window.innerWidth - 280),
              top: Math.min(st.statsAnchor.y + 8, window.innerHeight - 260),
            }}
          >
            <div style={s("font-size:13px;font-weight:600;margin-bottom:8px;")}>{st.statsCol}</div>
            {st.statsLoading || !st.statsData ? (
              <div style={s("font-size:12px;color:#9C9CAA;")}>Loading…</div>
            ) : (
              <>
                <div style={s("display:grid;grid-template-columns:1fr 1fr;gap:6px 10px;font-size:12px;margin-bottom:10px;")}>
                  <div style={s("color:#9C9CAA;")}>Type</div><div>{st.statsData.type}</div>
                  <div style={s("color:#9C9CAA;")}>Nulls</div><div>{st.statsData.nulls.toLocaleString()} ({st.statsData.null_pct}%)</div>
                  <div style={s("color:#9C9CAA;")}>Distinct</div><div>{st.statsData.distinct.toLocaleString()}</div>
                  {st.statsData.min != null && (<><div style={s("color:#9C9CAA;")}>Min</div><div>{String(st.statsData.min)}</div></>)}
                  {st.statsData.max != null && (<><div style={s("color:#9C9CAA;")}>Max</div><div>{String(st.statsData.max)}</div></>)}
                </div>
                <div style={s("font-size:11px;color:#9C9CAA;font-weight:600;margin-bottom:4px;")}>TOP VALUES</div>
                <div style={s("display:flex;flex-direction:column;gap:3px;")}>
                  {st.statsData.top_values.map((v, i) => (
                    <div key={i} style={s("display:flex;justify-content:space-between;font-size:12px;")}>
                      <span style={s("color:#17171F;")}>{String(v.value ?? "∅")}</span>
                      <span style={s("color:#9C9CAA;")}>{v.count.toLocaleString()}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </>
      )}

      {/* Header AutoFilter popover (P9.11) */}
      {st.headerFilterCol && st.headerFilterAnchor && (
        <>
          <div style={s("position:fixed;inset:0;z-index:150;")} onClick={closeHeaderFilter} />
          <div
            style={{
              ...s("position:fixed;z-index:151;width:240px;background:#FFFFFF;border:1px solid #E4E1ED;border-radius:10px;box-shadow:0 12px 32px -8px rgba(20,20,40,0.22);padding:12px;display:flex;flex-direction:column;gap:8px;"),
              left: Math.min(st.headerFilterAnchor.x, window.innerWidth - 260),
              top: Math.min(st.headerFilterAnchor.y + 8, window.innerHeight - 320),
            }}
          >
            <div style={s("font-size:12.5px;font-weight:600;")}>Filter: {st.headerFilterCol}</div>
            {st.headerFilterLoading ? (
              <div style={s("font-size:12px;color:#9C9CAA;")}>Loading values…</div>
            ) : (
              <>
                <div style={s("display:flex;gap:10px;font-size:11.5px;color:#7A2BF5;")}>
                  <span style={s("cursor:pointer;")} onClick={() => setState({ headerFilterSelected: new Set(st.headerFilterValues.map((v) => String(v.value ?? "∅"))) })}>Select all</span>
                  <span style={s("cursor:pointer;")} onClick={() => setState({ headerFilterSelected: new Set() })}>Clear</span>
                </div>
                <div style={s("max-height:220px;overflow:auto;display:flex;flex-direction:column;gap:2px;")}>
                  {st.headerFilterValues.map((v) => {
                    const val = String(v.value ?? "∅");
                    const checked = st.headerFilterSelected.has(val);
                    return (
                      <div key={val} onClick={() => toggleHeaderFilterValue(val)} style={s("display:flex;align-items:center;gap:6px;cursor:pointer;padding:3px 4px;border-radius:4px;")}>
                        <div style={s(box(checked))} />
                        <span style={s("font-size:12px;color:#17171F;flex:1;")}>{val}</span>
                        <span style={s("font-size:11px;color:#9C9CAA;")}>{v.count}</span>
                      </div>
                    );
                  })}
                </div>
                <Hv tag="button" css="height:30px;border-radius:6px;border:none;background:#7A2BF5;color:#fff;font-size:12.5px;font-weight:600;cursor:pointer;margin-top:4px;" hover="background:#6412E0;" onClick={applyHeaderFilter}>
                  Apply ({st.headerFilterSelected.size}/{st.headerFilterValues.length})
                </Hv>
              </>
            )}
          </div>
        </>
      )}

      {/* Modal overlay */}
      {st.modal && (
        <div style={s("position:fixed;inset:0;background:rgba(20,20,35,0.42);display:flex;align-items:center;justify-content:center;z-index:100;")} onClick={backdropClick}>
          <div style={{ ...s("max-height:82vh;overflow:auto;background:#FFFFFF;border-radius:14px;box-shadow:0 24px 64px -12px rgba(10,10,25,0.35);"), width: modalWidth }} onClick={(e) => e.stopPropagation()}>
            {st.modal !== "committing" && st.modal !== "committed" && (
              <div style={s("display:flex;align-items:center;justify-content:space-between;padding:18px 22px;border-bottom:1px solid #E5E5ED;")}>
                <div style={s("font-size:15px;font-weight:600;")}>{modalTitles[st.modal] || ""}</div>
                <div onClick={closeModal} style={s("cursor:pointer;color:#9C9CAA;font-size:16px;")}>✕</div>
              </div>
            )}

            {st.modal === "filter" && (
              <div style={s("padding:20px 22px;display:flex;flex-direction:column;gap:10px;")}>
                {(mc.conditions || []).map((cond: any, i: number) => (
                  <div key={i} style={s("display:flex;gap:8px;")}>
                    <select value={cond.column} onChange={(e) => setCondField(i, "column", e.target.value)} style={s("flex:1;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 8px;font-size:13px;")}>
                      {colOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                    </select>
                    <select value={cond.operator} onChange={(e) => setCondField(i, "operator", e.target.value)} style={s("flex:1;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 8px;font-size:13px;")}>
                      {OPERATORS.map((o) => <option key={o} value={o}>{o}</option>)}
                    </select>
                    <input value={cond.value} onChange={(e) => setCondField(i, "value", e.target.value)} style={s("flex:1;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 10px;font-size:13px;")} />
                  </div>
                ))}
                <div onClick={addFilterCondition} style={s("font-size:12.5px;color:#7A2BF5;cursor:pointer;padding-top:2px;")}>+ Add condition</div>
                <div style={s("background:#F5F0FF;border-radius:8px;padding:10px 12px;font-size:12.5px;color:#6B6B7A;margin-top:6px;")}>Result: <span style={s("color:#17171F;font-weight:600;")}>284,192 rows</span></div>
              </div>
            )}

            {st.modal === "join" && (
              <div style={s("padding:20px 22px;display:flex;flex-direction:column;gap:14px;")}>
                {isLiveWs && st.localSources.length > 0 && (
                  <div>
                    <div style={s("font-size:11.5px;color:#9C9CAA;font-weight:600;margin-bottom:6px;")}>USE AN IMPORTED SOURCE (files &amp; UC tables)</div>
                    <select value={mc.localSourceId || ""} onChange={(e) => pickImportedSource(e.target.value)} style={s("width:100%;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 8px;font-size:13px;")}>
                      <option value="">— choose imported source —</option>
                      {st.localSources.map((sr) => <option key={sr.id} value={sr.id}>{sr.name} ({sr.row_count} rows)</option>)}
                    </select>
                  </div>
                )}
                {isLiveWs && (
                  <Hv css="height:36px;padding:0 14px;display:flex;align-items:center;justify-content:center;gap:6px;border-radius:7px;border:1px dashed #7A2BF5;color:#7A2BF5;font-size:12.5px;font-weight:600;cursor:pointer;" hover="background:#F1EDFF;" onClick={startImportUcTable}>
                    ＋ Import a Unity Catalog table to join
                  </Hv>
                )}
                <div>
                  <div style={s("font-size:11.5px;color:#9C9CAA;font-weight:600;margin-bottom:6px;")}>{isLiveWs && st.localSources.length > 0 ? "…OR IMPORT A NEW FILE (CSV / XLSX / Parquet)" : "LOCAL FILE (CSV / XLSX / Parquet)"}</div>
                  {isLiveWs ? (
                    <div>
                      <label style={s("display:inline-flex;align-items:center;gap:8px;cursor:pointer;")}>
                        <input
                          type="file"
                          accept=".csv,.xlsx,.parquet"
                          style={{ display: "none" }}
                          onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadJoinFile(f); }}
                        />
                        <span style={s("height:34px;padding:0 14px;display:inline-flex;align-items:center;border-radius:7px;border:1px solid #7A2BF5;color:#7A2BF5;font-size:13px;font-weight:600;background:#FFFFFF;")}>Choose file…</span>
                        <span style={s(`font-family:${MONO};font-size:12.5px;color:${mc.fileName ? "#17171F" : "#9C9CAA"};`)}>{mc.fileName || "no file selected"}</span>
                      </label>
                      {mc.uploadError && <div style={s("font-size:12px;color:#FF3B5C;margin-top:6px;")}>{mc.uploadError}</div>}
                      {mc.rightCols && mc.rightCols.length > 0 && <div style={s("font-size:12px;color:#00B36B;margin-top:6px;")}>Imported · {mc.rightCols.length} columns</div>}
                    </div>
                  ) : (
                    <div style={s(`font-family:${MONO};font-size:12.5px;background:#F5F0FF;border:1px solid #E5E5ED;border-radius:6px;padding:8px 10px;`)}>{mc.file || "customer_mapping.xlsx"}</div>
                  )}
                </div>
                <div>
                  <div style={s("font-size:11.5px;color:#9C9CAA;font-weight:600;margin-bottom:6px;")}>MATCH (left = right)</div>
                  <div style={s("display:flex;align-items:center;gap:8px;font-size:13px;")}>
                    <select value={mc.leftKey || ""} onChange={(e) => updateModalConfig({ leftKey: e.target.value })} style={s("flex:1;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 8px;")}>
                      {colOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                    </select>
                    <span style={s("color:#9C9CAA;")}>=</span>
                    {isLiveWs && mc.rightCols && mc.rightCols.length > 0 ? (
                      <select value={mc.rightKey || ""} onChange={(e) => updateModalConfig({ rightKey: e.target.value })} style={s("flex:1;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 8px;")}>
                        {mc.rightCols.map((o: string) => <option key={o} value={o}>{o}</option>)}
                      </select>
                    ) : (
                      <input value={mc.rightKey || ""} placeholder="right key" onChange={(e) => updateModalConfig({ rightKey: e.target.value })} style={s("flex:1;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 10px;")} />
                    )}
                  </div>
                </div>
                <div>
                  <div style={s("font-size:11.5px;color:#9C9CAA;font-weight:600;margin-bottom:6px;")}>JOIN TYPE</div>
                  <div style={s("display:flex;gap:16px;")}>
                    <div onClick={() => updateModalConfig({ joinType: "left" })} style={s("display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;")}><div style={s(dot((mc.joinType || "left") === "left"))} /> Left</div>
                    <div onClick={() => updateModalConfig({ joinType: "inner" })} style={s("display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;")}><div style={s(dot(mc.joinType === "inner"))} /> Inner</div>
                  </div>
                </div>
              </div>
            )}

            {st.modal === "dedupe" && (
              <div style={s("padding:20px 22px;display:flex;flex-direction:column;gap:14px;")}>
                <div>
                  <div style={s("font-size:11.5px;color:#9C9CAA;font-weight:600;margin-bottom:6px;")}>KEY</div>
                  <select value={mc.key || "email"} onChange={(e) => updateModalConfig({ key: e.target.value })} style={s("width:100%;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 8px;font-size:13px;")}>
                    {colOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                </div>
                <div>
                  <div style={s("font-size:11.5px;color:#9C9CAA;font-weight:600;margin-bottom:6px;")}>WHEN DUPLICATES EXIST</div>
                  <div style={s("display:flex;flex-direction:column;gap:8px;")}>
                    <div onClick={() => updateModalConfig({ keep: "latest" })} style={s("display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;")}><div style={s(dot(mc.keep === "latest"))} /> Keep latest updated_at</div>
                    <div onClick={() => updateModalConfig({ keep: "first" })} style={s("display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;")}><div style={s(dot(mc.keep === "first"))} /> Keep first</div>
                    <div onClick={() => updateModalConfig({ keep: "last" })} style={s("display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;")}><div style={s(dot(mc.keep === "last"))} /> Keep last</div>
                  </div>
                </div>
                <div style={s("display:flex;gap:20px;background:#F5F0FF;border-radius:8px;padding:12px 14px;")}>
                  <div><div style={s("font-size:11px;color:#9C9CAA;")}>Input rows</div><div style={s("font-size:14px;font-weight:600;")}>{fmtNum(dedupeInput)}</div></div>
                  <div><div style={s("font-size:11px;color:#9C9CAA;")}>Duplicates</div><div style={s("font-size:14px;font-weight:600;color:#FF9F1C;")}>{fmtNum(dedupeDup)}</div></div>
                  <div><div style={s("font-size:11px;color:#9C9CAA;")}>Output rows</div><div style={s("font-size:14px;font-weight:600;color:#00B36B;")}>{fmtNum(dedupeOutput)}</div></div>
                </div>
              </div>
            )}

            {st.modal === "replace" && (
              <div style={s("padding:20px 22px;display:flex;flex-direction:column;gap:8px;")}>
                {isLiveWs ? (
                  <div style={s("margin-bottom:6px;")}>
                    <div style={s("font-size:11.5px;color:#9C9CAA;font-weight:600;margin-bottom:6px;")}>COLUMN</div>
                    <select
                      value={mc.column || ""}
                      onChange={(e) => {
                        updateModalConfig({ column: e.target.value, _selected: [], _mergeTarget: "" });
                        loadReplaceDistinct(e.target.value);
                      }}
                      style={s("width:100%;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 8px;font-size:13px;")}
                    >
                      {colOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                    </select>
                  </div>
                ) : (
                  <div style={s("font-size:11.5px;color:#9C9CAA;font-weight:600;margin-bottom:2px;")}>COLUMN: country</div>
                )}

                {isLiveWs && (
                  <div style={s("background:#FBFAFF;border:1px solid #E5E5ED;border-radius:8px;padding:12px;display:flex;flex-direction:column;gap:8px;margin-bottom:4px;")}>
                    <div style={s("font-size:11.5px;color:#9C9CAA;font-weight:600;")}>PICK VALUES TO STANDARDIZE</div>
                    {st.replaceDistinctLoading ? (
                      <div style={s("font-size:12.5px;color:#9C9CAA;")}>Loading values…</div>
                    ) : st.replaceDistinct.length === 0 ? (
                      <div style={s("font-size:12.5px;color:#9C9CAA;")}>No values found for this column.</div>
                    ) : (
                      <div style={s("display:flex;flex-wrap:wrap;gap:6px;max-height:140px;overflow:auto;")}>
                        {st.replaceDistinct.map((v) => {
                          const val = String(v.value ?? "∅");
                          const checked = (mc._selected || []).includes(val);
                          return (
                            <div
                              key={val}
                              onClick={() => toggleReplaceSelect(val)}
                              style={s(`font-size:12px;padding:5px 10px;border-radius:14px;cursor:pointer;border:1px solid ${checked ? "#7A2BF5" : "#E5E5ED"};background:${checked ? "#F1EDFF" : "#fff"};color:${checked ? "#7A2BF5" : "#17171F"};`)}
                            >
                              {checked ? "✓ " : ""}{val} <span style={s("color:#9C9CAA;")}>({v.count})</span>
                            </div>
                          );
                        })}
                      </div>
                    )}
                    <div style={s("display:flex;gap:8px;align-items:center;margin-top:2px;")}>
                      <span style={s("font-size:12px;color:#6B6B7A;white-space:nowrap;")}>Change selected to</span>
                      <input value={mc._mergeTarget || ""} onChange={(e) => updateModalConfig({ _mergeTarget: e.target.value })} placeholder="e.g. Netherlands" style={s(`flex:1;height:30px;border:1px solid #E5E5ED;border-radius:6px;padding:0 8px;font-size:12.5px;font-family:${MONO};`)} />
                      <Hv css="height:30px;padding:0 12px;border-radius:6px;border:none;background:#7A2BF5;color:#fff;font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap;" hover="background:#6412E0;" onClick={mergeSelectedInto}>Add</Hv>
                    </div>
                  </div>
                )}

                {(mc.mappings || []).filter((m: any) => m.from || m.to).length > 0 && (
                  <div style={s("font-size:11.5px;color:#9C9CAA;font-weight:600;")}>MAPPINGS</div>
                )}
                {(mc.mappings || []).map((m: any, i: number) =>
                  isLiveWs ? (
                    <div key={i} style={s("display:flex;align-items:center;gap:10px;")}>
                      <input value={m.from} placeholder="from" onChange={(e) => setMappingField(i, "from", e.target.value)} style={s(`flex:1;font-family:${MONO};font-size:13px;border:1px solid #E5E5ED;border-radius:6px;padding:8px 10px;`)} />
                      <span style={s("color:#9C9CAA;")}>→</span>
                      <input value={m.to} placeholder="to" onChange={(e) => setMappingField(i, "to", e.target.value)} style={s(`flex:1;font-family:${MONO};font-size:13px;border:1px solid #E5E5ED;border-radius:6px;padding:8px 10px;`)} />
                    </div>
                  ) : (
                    <div key={i} style={s("display:flex;align-items:center;gap:10px;")}>
                      <div style={s(`flex:1;font-family:${MONO};font-size:13px;background:#F5F0FF;border:1px solid #E5E5ED;border-radius:6px;padding:8px 10px;`)}>{m.from}</div>
                      <span style={s("color:#9C9CAA;")}>→</span>
                      <div style={s(`flex:1;font-family:${MONO};font-size:13px;background:#F5F0FF;border:1px solid #E5E5ED;border-radius:6px;padding:8px 10px;`)}>{m.to}</div>
                    </div>
                  )
                )}
                {isLiveWs && <div onClick={addMapping} style={s("font-size:12.5px;color:#7A2BF5;cursor:pointer;padding-top:2px;")}>+ Add mapping manually</div>}
              </div>
            )}

            {st.modal === "formula" && (
              <div style={s("padding:20px 22px;display:flex;flex-direction:column;gap:12px;")}>
                <div>
                  <div style={s("font-size:11.5px;color:#9C9CAA;font-weight:600;margin-bottom:6px;")}>NEW COLUMN NAME</div>
                  <input value={mc.name || ""} onChange={(e) => updateModalConfig({ name: e.target.value })} style={s("width:100%;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 10px;font-size:13px;")} />
                </div>
                <div>
                  <div style={s("font-size:11.5px;color:#9C9CAA;font-weight:600;margin-bottom:6px;")}>EXPRESSION</div>
                  <input value={mc.expression || ""} placeholder="e.g. revenue - cost" onChange={(e) => updateModalConfig({ expression: e.target.value })} style={s(`width:100%;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 10px;font-size:13px;font-family:${MONO};`)} />
                </div>
                <div style={s("background:#F5F0FF;border-radius:8px;padding:10px 12px;font-size:12px;color:#6B6B7A;")}>
                  Columns: <span style={s(`font-family:${MONO};color:#17171F;`)}>{colOptions.join(", ")}</span>
                  <div style={s("margin-top:4px;")}>Allowed: + − * / ||, coalesce, nullif, upper, lower, trim, concat, round, abs, length.</div>
                </div>
              </div>
            )}

            {st.modal === "validate" && isLiveWs && (
              <div style={s("padding:20px 22px;display:flex;flex-direction:column;gap:10px;")}>
                {st.validateRules.map((r, i) => (
                  <div key={r.id} style={s("display:flex;gap:6px;align-items:center;")}>
                    <select value={r.column} onChange={(e) => setRuleField(i, "column", e.target.value)} style={s("flex:1;height:32px;border:1px solid #E5E5ED;border-radius:6px;padding:0 6px;font-size:12.5px;")}>
                      {(ws.sourceCols ?? []).map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                    <select value={r.kind} onChange={(e) => setRuleField(i, "kind", e.target.value)} style={s("width:120px;height:32px;border:1px solid #E5E5ED;border-radius:6px;padding:0 6px;font-size:12.5px;")}>
                      {["not_null", "not_empty", "contains", "gt", "lt", "gte", "lte", "eq", "ne", "in_list"].map((k) => <option key={k} value={k}>{k}</option>)}
                    </select>
                    <input value={r.value} disabled={["not_null", "not_empty"].includes(r.kind)} placeholder="value" onChange={(e) => setRuleField(i, "value", e.target.value)} style={s(`width:110px;height:32px;border:1px solid #E5E5ED;border-radius:6px;padding:0 8px;font-size:12.5px;background:${["not_null", "not_empty"].includes(r.kind) ? "#F5F0FF" : "#fff"};`)} />
                    <select value={r.severity} onChange={(e) => setRuleField(i, "severity", e.target.value)} style={s("width:90px;height:32px;border:1px solid #E5E5ED;border-radius:6px;padding:0 6px;font-size:12.5px;")}>
                      <option value="error">error</option>
                      <option value="warning">warning</option>
                    </select>
                    <Hv css="width:24px;height:24px;border-radius:5px;display:flex;align-items:center;justify-content:center;color:#9C9CAA;font-size:13px;cursor:pointer;" hover="background:#FFE3E9;color:#FF3B5C;" onClick={() => removeRule(i)}>✕</Hv>
                  </div>
                ))}
                <div style={s("display:flex;justify-content:space-between;align-items:center;")}>
                  <div onClick={addValidateRule} style={s("font-size:12.5px;color:#7A2BF5;cursor:pointer;")}>+ Add rule</div>
                  <Hv tag="button" css="height:32px;padding:0 16px;border-radius:7px;border:none;background:#7A2BF5;color:#fff;font-size:12.5px;font-weight:600;cursor:pointer;" hover="background:#6412E0;" onClick={runValidation}>Run validation</Hv>
                </div>
                {st.validateResult && (
                  <>
                    <div style={s("display:flex;gap:20px;background:#F5F0FF;border-radius:8px;padding:12px 14px;margin-top:6px;")}>
                      <div><div style={s("font-size:11px;color:#9C9CAA;")}>Valid rows</div><div style={s("font-size:14px;font-weight:600;color:#00B36B;")}>{st.validateResult.valid.toLocaleString()}</div></div>
                      <div><div style={s("font-size:11px;color:#9C9CAA;")}>Invalid rows</div><div style={s("font-size:14px;font-weight:600;color:#FF3B5C;")}>{st.validateResult.invalid.toLocaleString()}</div></div>
                      <div><div style={s("font-size:11px;color:#9C9CAA;")}>Commit</div><div style={s(`font-size:14px;font-weight:600;color:${st.validateResult.blocking ? "#FF3B5C" : "#00B36B"};`)}>{st.validateResult.blocking ? "blocked" : "allowed"}</div></div>
                    </div>
                    <div style={s("display:flex;flex-direction:column;gap:3px;")}>
                      {st.validateResult.per_rule.map((pr, i) => (
                        <div key={i} style={s("font-size:12px;color:#6B6B7A;display:flex;justify-content:space-between;")}>
                          <span>{st.validateRules[i]?.column} · {st.validateRules[i]?.kind} <span style={s(`color:${pr.severity === "error" ? "#FF3B5C" : "#FF9F1C"};`)}>({pr.severity})</span></span>
                          <span>{pr.error ? <span style={s("color:#FF3B5C;")}>{pr.error}</span> : `${pr.invalid} failing`}</span>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>
            )}

            {st.modal === "validate" && !isLiveWs && (
              <div style={s("padding:20px 22px;display:flex;flex-direction:column;gap:10px;")}>
                {mc.rules && ([
                  { key: "not_null", text: "customer_id must not be null" },
                  { key: "email_at", text: 'email must contain "@"' },
                  { key: "country_list", text: "country must belong to approved list" },
                  { key: "revenue_pos", text: "revenue >= 0" },
                ] as const).map((rule) => (
                  <div key={rule.key} onClick={() => updateModalConfig({ rules: { ...mc.rules, [rule.key]: !mc.rules[rule.key] } })} style={s("display:flex;align-items:center;gap:8px;cursor:pointer;padding:6px 4px;")}>
                    <div style={s(box(mc.rules[rule.key]))} />
                    <div style={s(`font-family:${MONO};font-size:12.5px;`)}>{rule.text}</div>
                  </div>
                ))}
                <div style={s("display:flex;gap:20px;background:#F5F0FF;border-radius:8px;padding:12px 14px;margin-top:6px;")}>
                  <div><div style={s("font-size:11px;color:#9C9CAA;")}>Valid rows</div><div style={s("font-size:14px;font-weight:600;color:#00B36B;")}>{validation.validCount}</div></div>
                  <div><div style={s("font-size:11px;color:#9C9CAA;")}>Invalid rows</div><div style={s("font-size:14px;font-weight:600;color:#FF3B5C;")}>{validation.invalidCount}</div></div>
                </div>
              </div>
            )}

            {st.modal === "reviewDiff" && (
              <div style={s("padding:20px 22px;display:flex;flex-direction:column;gap:16px;")}>
                {isLiveWs ? (
                  st.diffError ? (
                    <div style={s("font-size:13px;color:#FF3B5C;")}>{st.diffError}</div>
                  ) : !st.diffResult ? (
                    <div style={s("font-size:13px;color:#6B6B7A;display:flex;align-items:center;gap:10px;")}><div style={s("width:16px;height:16px;border:2px solid #E5E5ED;border-top-color:#7A2BF5;border-radius:50%;animation:ldw-spin 0.7s linear infinite;")} /> Diffing base vs transformed…</div>
                  ) : (
                    <>
                      <div style={s("font-size:12px;color:#6B6B7A;")}>Base checkout vs transformed output · key: <span style={s(`font-family:${MONO};`)}>{st.rowKey.join(", ")}</span></div>
                      <div style={s("display:flex;gap:12px;")}>
                        {[["Added", st.diffResult.added, "#00B36B"], ["Modified", st.diffResult.modified, "#FF9F1C"], ["Deleted", st.diffResult.deleted, "#FF3B5C"], ["Unchanged", st.diffResult.unchanged, "#17171F"]].map(([label, val, color]) => (
                          <div key={label as string} style={s("flex:1;background:#F5F0FF;border-radius:8px;padding:12px;text-align:center;")}>
                            <div style={s("font-size:11px;color:#9C9CAA;")}>{label as string}</div>
                            <div style={s(`font-size:16px;font-weight:600;color:${color};`)}>{(val as number).toLocaleString()}</div>
                          </div>
                        ))}
                      </div>
                      <div>
                        <div style={s("font-size:11.5px;color:#9C9CAA;font-weight:600;margin-bottom:8px;")}>SAMPLE MODIFIED RECORDS</div>
                        <div style={s("display:flex;flex-direction:column;gap:8px;max-height:280px;overflow:auto;")}>
                          {st.diffResult.samples.filter((d) => d.changes.length).length === 0 && <div style={s("font-size:12.5px;color:#9C9CAA;")}>No modified rows.</div>}
                          {st.diffResult.samples.filter((d) => d.changes.length).map((d, i) => (
                            <div key={i} style={s("border:1px solid #E5E5ED;border-radius:8px;padding:10px 12px;display:flex;flex-direction:column;gap:4px;")}>
                              <div style={s(`font-family:${MONO};font-size:12px;color:#6B6B7A;`)}>{Object.entries(d.key).map(([k, v]) => `${k}: ${v}`).join(" · ")}</div>
                              {d.changes.map((ch) => (
                                <div key={ch.column} style={s("font-size:12.5px;color:#6B6B7A;")}>{ch.column}: <span style={s("color:#FF3B5C;text-decoration:line-through;")}>{String(ch.before ?? "∅")}</span> → <span style={s("color:#00B36B;")}>{String(ch.after ?? "∅")}</span></div>
                              ))}
                            </div>
                          ))}
                        </div>
                      </div>
                      <div style={s("display:flex;justify-content:flex-end;")}>
                        <Hv tag="button" css="height:38px;padding:0 20px;border-radius:8px;border:none;background:#7A2BF5;color:#fff;font-size:13.5px;font-weight:600;cursor:pointer;" hover="background:#6412E0;" onClick={openCommitTarget}>{isLiveWs ? "Proceed to Publish" : "Proceed to Commit"}</Hv>
                      </div>
                    </>
                  )
                ) : (
                  <>
                    <div style={s("font-size:12px;color:#6B6B7A;")}>Source version: {ws ? ws.version : ""}</div>
                    <div style={s("display:flex;gap:12px;")}>
                      {[["Added", "281", "#00B36B"], ["Modified", "8,921", "#FF9F1C"], ["Deleted", "17", "#FF3B5C"], ["Unchanged", "1,390,781", "#17171F"]].map(([label, val, color]) => (
                        <div key={label} style={s("flex:1;background:#F5F0FF;border-radius:8px;padding:12px;text-align:center;")}>
                          <div style={s("font-size:11px;color:#9C9CAA;")}>{label}</div>
                          <div style={s(`font-size:16px;font-weight:600;color:${color};`)}>{val}</div>
                        </div>
                      ))}
                    </div>
                    <div style={s("display:flex;justify-content:flex-end;")}>
                      <Hv tag="button" css="height:38px;padding:0 20px;border-radius:8px;border:none;background:#7A2BF5;color:#fff;font-size:13.5px;font-weight:600;cursor:pointer;" hover="background:#6412E0;" onClick={openCommitTarget}>{isLiveWs ? "Proceed to Publish" : "Proceed to Commit"}</Hv>
                    </div>
                  </>
                )}
              </div>
            )}

            {st.modal === "commitTarget" && (
              <div style={s("padding:20px 22px;display:flex;flex-direction:column;gap:16px;")}>
                <div>
                  <div style={s("font-size:14px;font-weight:600;color:#17171F;margin-bottom:4px;")}>How do you want to publish this?</div>
                  <div style={s("display:flex;flex-direction:column;gap:8px;margin-top:10px;")}>
                    <div onClick={() => setState({ commitTarget: "existing" })} style={s(`display:flex;align-items:flex-start;gap:10px;cursor:pointer;padding:10px 12px;border-radius:8px;border:1px solid ${st.commitTarget === "existing" ? "#7A2BF5" : "#E5E5ED"};background:${st.commitTarget === "existing" ? "#F1EDFF" : "#fff"};`)}>
                      <div style={s(dot(st.commitTarget === "existing") + "margin-top:2px;")} />
                      <div>
                        <div style={s("font-size:13px;font-weight:600;color:#17171F;")}>Update the original table</div>
                        <div style={s("font-size:12px;color:#6B6B7A;margin-top:2px;")}>Only sends the rows whose values changed back to the source table.</div>
                      </div>
                    </div>
                    <div onClick={() => setState({ commitTarget: "new" })} style={s(`display:flex;align-items:flex-start;gap:10px;cursor:pointer;padding:10px 12px;border-radius:8px;border:1px solid ${st.commitTarget === "new" ? "#7A2BF5" : "#E5E5ED"};background:${st.commitTarget === "new" ? "#F1EDFF" : "#fff"};`)}>
                      <div style={s(dot(st.commitTarget === "new") + "margin-top:2px;")} />
                      <div>
                        <div style={s("font-size:13px;font-weight:600;color:#17171F;")}>Save as a new table</div>
                        <div style={s("font-size:12px;color:#6B6B7A;margin-top:2px;")}>Writes everything, including any columns you added (joins, formulas).</div>
                      </div>
                    </div>
                  </div>
                  {isLiveWs && st.commitAddedCols.length > 0 && st.commitTarget === "existing" && (
                    <div style={s("font-size:12px;color:#B26A00;background:#FFF3E0;border-radius:8px;padding:10px 12px;margin-top:10px;")}>
                      Heads up: you added columns <span style={s(`font-family:${MONO};color:#17171F;`)}>{st.commitAddedCols.join(", ")}</span>. "Update the original table" won't keep them — pick "Save as a new table" instead.
                    </div>
                  )}
                </div>

                {st.commitTarget === "existing" && (
                  <>
                    <div>
                      <div style={s("font-size:11.5px;color:#9C9CAA;font-weight:600;margin-bottom:6px;")}>TABLE TO UPDATE</div>
                      {isLiveWs ? (
                        <input value={st.commitExistingTable} onChange={(e) => setState({ commitExistingTable: e.target.value })} style={s(`width:100%;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 10px;font-size:13px;font-family:${MONO};box-sizing:border-box;`)} />
                      ) : (
                        <select value={st.commitExistingTable} onChange={(e) => setState({ commitExistingTable: e.target.value })} style={s(`width:100%;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 8px;font-size:13px;font-family:${MONO};`)}>
                          {Object.keys(TABLE_SCHEMAS).map((o) => <option key={o} value={o}>{o}</option>)}
                        </select>
                      )}
                    </div>
                    {!isLiveWs && (
                      <div style={{ ...s("border:1px solid #E5E5ED;border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;gap:6px;"), background: schemaIncompatible ? "#FFE9EE" : "#DFFFF0" }}>
                        <div style={s(`font-size:12.5px;font-weight:700;color:${schemaIncompatible ? "#FF3B5C" : "#00B36B"};`)}>{schemaIncompatible ? "✕ Schema incompatible" : "✓ Schema compatible"}</div>
                        {schemaIncompatible && (
                          <div style={s("font-size:12px;color:#6B6B7A;")}>Output columns not in target: <span style={s(`font-family:${MONO};color:#FF3B5C;`)}>{missingCols.join(", ")}</span></div>
                        )}
                      </div>
                    )}
                  </>
                )}

                {st.commitTarget === "new" && (
                  <div>
                    <div style={s("font-size:11.5px;color:#9C9CAA;font-weight:600;margin-bottom:6px;")}>WHERE TO SAVE IT</div>
                    <div style={s("display:flex;gap:8px;")}>
                      {isLiveWs ? (
                        <>
                          <div style={s("flex:1;")}>
                            <input
                              list="commit-catalog-list"
                              placeholder="catalog"
                              value={st.commitNewCatalog}
                              onChange={(e) => { setState({ commitNewCatalog: e.target.value, commitNs: null }); ensureSchemasLoaded(e.target.value); }}
                              style={s("width:100%;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 10px;font-size:13px;box-sizing:border-box;")}
                            />
                            <datalist id="commit-catalog-list">
                              {st.browse.catalogs.filter((c) => !READONLY_CATALOG_HINTS.has(c.name)).map((c) => <option key={c.name} value={c.name} />)}
                            </datalist>
                          </div>
                          <div style={s("flex:1;")}>
                            <input
                              list="commit-schema-list"
                              placeholder="schema"
                              value={st.commitNewSchema}
                              onChange={(e) => setState({ commitNewSchema: e.target.value, commitNs: null })}
                              style={s("width:100%;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 10px;font-size:13px;box-sizing:border-box;")}
                            />
                            <datalist id="commit-schema-list">
                              {(st.browse.schemas[st.commitNewCatalog] || []).map((sc) => <option key={sc.name} value={sc.name} />)}
                            </datalist>
                          </div>
                          <input placeholder="table name" value={st.commitNewTableName} onChange={(e) => setState({ commitNewTableName: e.target.value })} style={s("flex:1;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 10px;font-size:13px;box-sizing:border-box;")} />
                        </>
                      ) : (
                        <>
                          <input placeholder="catalog" value={st.commitNewCatalog} onChange={(e) => setState({ commitNewCatalog: e.target.value, commitNs: null })} style={s("flex:1;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 10px;font-size:13px;")} />
                          <input placeholder="schema" value={st.commitNewSchema} onChange={(e) => setState({ commitNewSchema: e.target.value, commitNs: null })} style={s("flex:1;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 10px;font-size:13px;")} />
                          <input placeholder="table" value={st.commitNewTableName} onChange={(e) => setState({ commitNewTableName: e.target.value })} style={s("flex:1;height:34px;border:1px solid #E5E5ED;border-radius:6px;padding:0 10px;font-size:13px;")} />
                        </>
                      )}
                    </div>
                    {isLiveWs && <div style={s("font-size:11px;color:#9C9CAA;margin-top:4px;")}>Type to search your catalogs/schemas, or type a new name to create one.</div>}
                    <div style={s(`font-size:12px;color:#9C9CAA;font-family:${MONO};margin-top:6px;`)}>{commitNewFullName}</div>
                    {isLiveWs && st.commitNs && !(st.commitNs.catalog_exists && st.commitNs.schema_exists) && (
                      <div style={s("margin-top:12px;background:#FFF3E0;border:1px solid #FFE0B2;border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;gap:8px;")}>
                        <div style={s("font-size:12.5px;color:#B26A00;font-weight:600;")}>Destination doesn't exist yet</div>
                        <div style={s("font-size:12px;color:#6B6B7A;")}>
                          {!st.commitNs.catalog_exists && <>Catalog <span style={s(`font-family:${MONO};`)}>{st.commitNewCatalog}</span> — missing<br /></>}
                          {st.commitNs.catalog_exists && !st.commitNs.schema_exists && <>Schema <span style={s(`font-family:${MONO};`)}>{st.commitNewCatalog}.{st.commitNewSchema}</span> — missing</>}
                        </div>
                        <Hv tag="button" css="height:32px;padding:0 14px;border-radius:7px;border:none;background:#7A2BF5;color:#fff;font-size:12.5px;font-weight:600;cursor:pointer;align-self:flex-start;" hover="background:#6412E0;" onClick={createNamespace}>
                          {st.commitNsBusy ? "Creating…" : `Create ${!st.commitNs.catalog_exists ? "catalog + schema" : "schema"}`}
                        </Hv>
                      </div>
                    )}
                    {isLiveWs && st.commitNsError && <div style={s("margin-top:8px;font-size:12px;color:#FF3B5C;")}>{st.commitNsError}</div>}
                  </div>
                )}

                <div style={s("display:flex;justify-content:flex-end;gap:10px;")}>
                  {st.commitTarget === "existing" && schemaIncompatible ? (
                    <>
                      <div onClick={fixSchema} style={s("height:38px;padding:0 16px;display:flex;align-items:center;font-size:13px;color:#6B6B7A;cursor:pointer;")}>Fix Schema</div>
                      <Hv tag="button" css="height:38px;padding:0 18px;border-radius:8px;border:1px solid #7A2BF5;background:#FFFFFF;color:#7A2BF5;font-size:13px;font-weight:600;cursor:pointer;" hover="background:#F1EDFF;" onClick={proposeNewTable}>Create New Table Instead</Hv>
                    </>
                  ) : (
                    <Hv tag="button" css="height:38px;padding:0 20px;border-radius:8px;border:none;background:#7A2BF5;color:#fff;font-size:13.5px;font-weight:600;cursor:pointer;" hover="background:#6412E0;" onClick={continueToReadyCommit}>Continue</Hv>
                  )}
                </div>
              </div>
            )}

            {st.modal === "readyCommit" && (
              <div style={s("padding:20px 22px;display:flex;flex-direction:column;gap:12px;")}>
                {isLiveWs && (
                  <div style={s("font-size:13.5px;color:#17171F;background:#F1EDFF;border-radius:8px;padding:12px 14px;line-height:1.5;")}>
                    {st.commitTarget === "new" ? (
                      <>You're about to save <b>{(ws && st.stepCounts[ws.pipeline[ws.pipeline.length - 1]?.id] != null ? st.stepCounts[ws.pipeline[ws.pipeline.length - 1].id] : st.gridTotal)?.toLocaleString() ?? "—"} rows</b> as a new table <span style={s(`font-family:${MONO};`)}>{commitTargetLabel}</span>.</>
                    ) : st.diffResult ? (
                      <>You're about to update <b>{st.diffResult.modified.toLocaleString()} row{st.diffResult.modified === 1 ? "" : "s"}</b>{st.diffResult.added > 0 ? <> and add <b>{st.diffResult.added.toLocaleString()}</b></> : ""} in <span style={s(`font-family:${MONO};`)}>{commitTargetLabel}</span>. {st.diffResult.unchanged.toLocaleString()} rows unchanged.</>
                    ) : (
                      <>You're about to update <span style={s(`font-family:${MONO};`)}>{commitTargetLabel}</span> with your changed rows.</>
                    )}
                  </div>
                )}
                <div style={s(`font-size:12px;color:#6B6B7A;background:#F5F0FF;border:1px solid #E5E5ED;border-radius:6px;padding:8px 10px;font-family:${MONO};`)}>Target: {commitTargetLabel}</div>
                {isLiveWs && st.commitChecks
                  ? ([
                      ["authenticated", "Databricks authenticated"],
                      ["row_key", `Unique row key (${st.rowKey.join(", ") || "none"})`],
                      ["warehouse", "SQL warehouse configured"],
                    ] as const).map(([k, label]) => {
                      const ok = st.commitChecks![k];
                      return (
                        <div key={k} style={s("display:flex;align-items:center;gap:8px;font-size:13px;")}>
                          <span style={s(`color:${ok ? "#00B36B" : "#FF3B5C"};`)}>{ok ? "✓" : "✕"}</span> {label}
                        </div>
                      );
                    })
                  : ["Pipeline completed", "Unique customer_id", "0 blocking validation errors", "Databricks authorization confirmed"].map((t) => (
                      <div key={t} style={s("display:flex;align-items:center;gap:8px;font-size:13px;")}><span style={s("color:#00B36B;")}>✓</span> {t}</div>
                    ))}
                {isLiveWs && st.validateResult?.blocking && (
                  <div style={s("font-size:12px;color:#FF3B5C;background:#FFE9EE;border-radius:6px;padding:8px 10px;")}>Validation has {st.validateResult.invalid} blocking failures — resolve or downgrade to warning before committing.</div>
                )}
                <div style={s("display:flex;justify-content:flex-end;margin-top:8px;")}>
                  <Hv tag="button" css={`height:38px;padding:0 22px;border-radius:8px;border:none;color:#fff;font-size:13.5px;font-weight:600;cursor:pointer;background:${isLiveWs && (!st.commitChecks?.ready && st.commitChecks && !Object.values(st.commitChecks).every(Boolean)) ? "#C4C4CE" : "#7A2BF5"};`} hover="background:#6412E0;" onClick={commit}>{isLiveWs ? "Publish" : "Commit"}</Hv>
                </div>
              </div>
            )}

            {st.modal === "committing" && (
              <div style={s("padding:36px 30px;display:flex;flex-direction:column;align-items:center;gap:16px;")}>
                <div style={s("width:28px;height:28px;border:3px solid #E5E5ED;border-top-color:#7A2BF5;border-radius:50%;animation:ldw-spin 0.8s linear infinite;")} />
                <div style={s("font-size:13.5px;color:#6B6B7A;")}>{isLiveWs && st.commitMsg ? st.commitMsg : "Committing to Unity Catalog…"}</div>
                <div style={{ ...s(`font-family:${MONO};font-size:12px;background:#14131C;color:#D9D6F5;border-radius:8px;padding:12px 16px;width:100%;`), boxSizing: "border-box" }}>
                  {st.commitTarget === "new" ? `CREATE TABLE ${isLiveWs ? commitNewFullName : commitNewFullName} AS` : `MERGE INTO ${st.commitExistingTable} AS t`}
                  <br />
                  {st.commitTarget === "new" ? "SELECT … FROM staged_changes" : `USING staged_changes ON ${st.rowKey.join(", ") || "key"}`}
                </div>
              </div>
            )}

            {st.modal === "committed" && (
              isLiveWs ? (
                st.commitConflict ? (
                  <div style={s("padding:32px 30px;display:flex;flex-direction:column;align-items:center;gap:14px;")}>
                    <div style={s("width:44px;height:44px;border-radius:50%;background:#FFF3E0;color:#FF9F1C;font-size:20px;display:flex;align-items:center;justify-content:center;")}>!</div>
                    <div style={s("font-size:15px;font-weight:600;")}>Source changed since checkout</div>
                    <div style={s("font-size:13px;color:#6B6B7A;text-align:center;")}>Checkout v{st.commitConflict.base} → current v{st.commitConflict.current}. Your work is safe. Re-check out the source and re-run the pipeline before committing.</div>
                    <Hv tag="button" css="margin-top:4px;height:36px;padding:0 20px;border-radius:8px;border:none;background:#7A2BF5;color:#fff;font-size:13px;font-weight:600;cursor:pointer;" hover="background:#6412E0;" onClick={finishCommit}>Close</Hv>
                  </div>
                ) : st.commitResult ? (
                  <div style={s("padding:36px 30px;display:flex;flex-direction:column;align-items:center;gap:14px;")}>
                    <div style={s("width:44px;height:44px;border-radius:50%;background:#DFFFF0;color:#00B36B;font-size:20px;display:flex;align-items:center;justify-content:center;")}>✓</div>
                    <div style={s("font-size:15px;font-weight:600;")}>{st.commitResult.created ? "Table created" : "Commit succeeded"}</div>
                    <div style={s(`font-size:13px;color:#6B6B7A;text-align:center;font-family:${MONO};`)}>{st.commitResult.target_table}<br />{st.commitResult.new_version != null ? `Delta version v${st.commitResult.new_version} · ` : ""}{st.commitResult.row_count} rows {st.commitResult.created ? "written" : "merged"}</div>
                    <Hv tag="button" css="margin-top:6px;height:36px;padding:0 20px;border-radius:8px;border:none;background:#7A2BF5;color:#fff;font-size:13px;font-weight:600;cursor:pointer;" hover="background:#6412E0;" onClick={finishCommit}>Done</Hv>
                  </div>
                ) : (
                  <div style={s("padding:32px 30px;display:flex;flex-direction:column;align-items:center;gap:14px;")}>
                    <div style={s("width:44px;height:44px;border-radius:50%;background:#FFE9EE;color:#FF3B5C;font-size:20px;display:flex;align-items:center;justify-content:center;")}>✕</div>
                    <div style={s("font-size:15px;font-weight:600;")}>Commit failed</div>
                    <div style={s("font-size:12.5px;color:#6B6B7A;text-align:center;")}>{st.commitMsg || "Unknown error"}</div>
                    <Hv tag="button" css="margin-top:4px;height:36px;padding:0 20px;border-radius:8px;border:none;background:#7A2BF5;color:#fff;font-size:13px;font-weight:600;cursor:pointer;" hover="background:#6412E0;" onClick={finishCommit}>Close</Hv>
                  </div>
                )
              ) : (
                <div style={s("padding:36px 30px;display:flex;flex-direction:column;align-items:center;gap:14px;")}>
                  <div style={s("width:44px;height:44px;border-radius:50%;background:#DFFFF0;color:#00B36B;font-size:20px;display:flex;align-items:center;justify-content:center;")}>✓</div>
                  <div style={s("font-size:15px;font-weight:600;")}>Commit succeeded</div>
                  <div style={s("font-size:13px;color:#6B6B7A;text-align:center;")}>{commitTargetLabel}<br />New Delta version v{ws ? ws.version : ""} · {ws ? ws.changes || 32 : 0} changes merged</div>
                  <Hv tag="button" css="margin-top:6px;height:36px;padding:0 20px;border-radius:8px;border:none;background:#7A2BF5;color:#fff;font-size:13px;font-weight:600;cursor:pointer;" hover="background:#6412E0;" onClick={finishCommit}>Done</Hv>
                </div>
              )
            )}

            {isLiveWs && ["filter", "dedupe", "formula", "replace", "join"].includes(st.modal || "") && (
              <div style={s("padding:14px 22px;border-top:1px solid #E5E5ED;background:#FBFAFF;")}>
                <div style={s("font-size:11px;color:#9C9CAA;font-weight:600;letter-spacing:0.03em;margin-bottom:8px;")}>PREVIEW</div>
                {st.modalPreviewLoading && !st.modalPreview ? (
                  <div style={s("font-size:12.5px;color:#9C9CAA;display:flex;align-items:center;gap:8px;")}>
                    <div style={s("width:12px;height:12px;border:2px solid #E5E5ED;border-top-color:#7A2BF5;border-radius:50%;animation:ldw-spin 0.7s linear infinite;")} /> Computing…
                  </div>
                ) : st.modalPreview ? (
                  <>
                    <div style={s("font-size:13px;color:#17171F;margin-bottom:8px;")}>
                      {st.modalPreview.totalBefore.toLocaleString()} rows → <b>{st.modalPreview.totalAfter.toLocaleString()} rows</b>
                      {st.modalPreview.totalAfter !== st.modalPreview.totalBefore && (
                        <span style={s(`color:${st.modalPreview.totalAfter < st.modalPreview.totalBefore ? "#FF9F1C" : "#00B36B"};`)}> ({st.modalPreview.totalAfter < st.modalPreview.totalBefore ? "−" : "+"}{Math.abs(st.modalPreview.totalAfter - st.modalPreview.totalBefore).toLocaleString()})</span>
                      )}
                      {st.modalPreviewLoading && <span style={s("color:#9C9CAA;")}> · updating…</span>}
                    </div>
                    {st.modalPreview.rows.length > 0 && (
                      <div style={s("overflow-x:auto;border:1px solid #E5E5ED;border-radius:6px;")}>
                        <table style={s("border-collapse:collapse;font-size:11.5px;width:100%;")}>
                          <thead>
                            <tr>
                              {st.modalPreview.cols.map((c) => (
                                <th key={c} style={s("text-align:left;padding:5px 8px;background:#FFFFFF;color:#9C9CAA;font-weight:600;white-space:nowrap;border-bottom:1px solid #E5E5ED;")}>{c}</th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {st.modalPreview.rows.map((r, ri) => (
                              <tr key={ri}>
                                {st.modalPreview!.cols.map((c) => (
                                  <td key={c} style={s("padding:5px 8px;white-space:nowrap;color:#17171F;border-bottom:1px solid #F0F0F5;")}>{String((r as any)[c] ?? "—")}</td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </>
                ) : (
                  <div style={s("font-size:12.5px;color:#9C9CAA;")}>Configure the step to see a preview.</div>
                )}
              </div>
            )}

            {showModalFooter && (
              <div style={s("display:flex;justify-content:flex-end;gap:10px;padding:16px 22px;border-top:1px solid #E5E5ED;")}>
                <div onClick={closeModal} style={s("height:36px;padding:0 14px;display:flex;align-items:center;font-size:13px;color:#6B6B7A;cursor:pointer;")}>Cancel</div>
                <Hv tag="button" css="height:36px;padding:0 20px;border-radius:8px;border:none;background:#7A2BF5;color:#fff;font-size:13px;font-weight:600;cursor:pointer;" hover="background:#6412E0;" onClick={applyModal}>Apply</Hv>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
