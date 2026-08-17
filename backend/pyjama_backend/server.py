"""FastAPI command surface — the localhost API the UI calls.

Mirrors the command contract (IMPLEMENTATION_PLAN §17). Command endpoints are
plain ``def`` so FastAPI runs them in a threadpool: the blocking OAuth flow and
statement polling are fine there. Auth/read-path commands hit real Databricks;
the rest are Phase-2+ stubs.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import os
import threading
import time

from . import ai_model, ai_sql, analysis_spec as aspec, expression, localsource, recipes, sources as sourcesmod, watchfolder, workspace
from .ai_explore import exploration_service, promote_sql
from .ai_runtime import runtime as ai_runtime
from .localsource import LocalSourceError
from .auth import AuthService, NotAuthenticated, SessionExpired
from .checkout import CheckoutEngine
from .config import DatabricksConfig
from .databricks import DatabricksClient, RestError, is_terminal, statement_columns, statement_rows
from .dbsql import FilterOp, Predicate, SqlError, build_working_set_select
from .formula import FormulaError
from .keystore import FallbackKeyStore, FileKeyStore
from .logging_setup import log, new_operation_id
from .pipeline import PipelineError, Step as PStep
from .query import SessionCache


def _to_steps(models) -> list[PStep]:
    return [PStep(id=m.id, type=m.type, config=m.config, enabled=m.enabled, input_id=m.input_id) for m in models]


def _short_db_error(e: Exception) -> str:
    """First meaningful line of a DuckDB error, for surfacing to the UI."""
    msg = str(e).strip().splitlines()
    return (msg[0] if msg else "query error")[:200]


class AppState:
    def __init__(self) -> None:
        # One keystore shared by auth (refresh token) and crypto (workspace WDEKs).
        # A frozen (PyInstaller) app is unsigned, so macOS Keychain prompts on
        # every launch — use the 0600 file store there to avoid the prompt. A
        # code-signed build (or dev from source) uses Keychain with file fallback.
        # Force either with PYJAMA_KEYSTORE=file|os.
        import sys

        mode = os.environ.get("PYJAMA_KEYSTORE", "file" if getattr(sys, "frozen", False) else "auto")
        self.keystore = FileKeyStore() if mode == "file" else FallbackKeyStore()
        self.auth = AuthService(DatabricksConfig.load(), self.keystore)
        self.checkouts: dict[str, dict] = {}
        self.ai_ops: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.sessions = SessionCache(self.keystore)
        # Stay signed in across restarts if a refresh token is cached.
        try:
            self.auth.try_restore()
        except Exception:  # noqa: BLE001
            pass


def _client(state: AppState) -> DatabricksClient:
    try:
        ctx = state.auth.access_context()
    except SessionExpired:
        raise HTTPException(status_code=401, detail="session expired; please sign in again")
    except NotAuthenticated:
        raise HTTPException(status_code=401, detail="not authenticated")
    return DatabricksClient(ctx.base, ctx.token)


# ---- request bodies ----
class PingBody(BaseModel):
    message: str = ""


class CatalogBody(BaseModel):
    catalog: str


class TablesBody(BaseModel):
    catalog: str
    schema: str


class FullNameBody(BaseModel):
    fullName: str


class IdBody(BaseModel):
    id: str


class NameBody(BaseModel):
    name: str


class WorkspaceIdBody(BaseModel):
    workspaceId: str


class FilterSpec(BaseModel):
    column: str
    op: str
    value: str = ""


class SelectSpikeBody(BaseModel):
    warehouseId: str
    table: str
    columns: list[str]
    filters: list[FilterSpec] = []


class CheckoutBody(BaseModel):
    table: str
    columns: list[str]
    filters: list[FilterSpec] = []
    rowKey: list[str] = []
    createWorkspace: bool = True


class OpBody(BaseModel):
    operationId: str


class PreviewBody(BaseModel):
    workspaceId: str
    limit: int = 500


class SortSpec(BaseModel):
    column: str
    direction: str = "asc"


class PreviewQueryBody(BaseModel):
    workspaceId: str
    offset: int = 0
    limit: int = 500
    sort: list[SortSpec] = []


class StepModel(BaseModel):
    id: str
    type: str
    config: dict = {}
    enabled: bool = True
    input_id: str | None = None


class PipelinePreviewBody(BaseModel):
    workspaceId: str
    steps: list[StepModel] = []
    stepIndex: int | None = None
    offset: int = 0
    limit: int = 500
    sort: list[SortSpec] = []


class PipelineSaveBody(BaseModel):
    workspaceId: str
    steps: list[StepModel] = []


class PipelineCountsBody(BaseModel):
    workspaceId: str
    steps: list[StepModel] = []


class UcSourceBody(BaseModel):
    table: str
    columns: list[str]
    filters: list[FilterSpec] = []


class SourceIdBody(BaseModel):
    sourceId: str


class CreateFromSourceBody(BaseModel):
    sourceId: str
    name: str = ""


class RowKeyBody(BaseModel):
    workspaceId: str
    keys: list[str] = []


class DiffBody(BaseModel):
    workspaceId: str
    steps: list[StepModel] = []
    keys: list[str] = []


class RuleModel(BaseModel):
    id: str
    column: str
    kind: str
    value: str = ""
    severity: str = "error"


class ValidateBody(BaseModel):
    workspaceId: str
    steps: list[StepModel] = []
    rules: list[RuleModel] = []


class CommitBody(BaseModel):
    workspaceId: str
    steps: list[StepModel] = []
    keys: list[str] = []
    targetTable: str
    createNew: bool = False


class NamespaceBody(BaseModel):
    catalog: str
    schema: str = ""


class DistinctValuesBody(BaseModel):
    workspaceId: str
    column: str
    limit: int = 50


class NamespaceCreateBody(BaseModel):
    catalog: str
    schema: str = ""
    createCatalog: bool = False
    createSchema: bool = False


class WatchScanBody(BaseModel):
    folder: str


class WatchImportBody(BaseModel):
    path: str


class MeasureModel(BaseModel):
    column: str
    aggregation: str = "count"
    alias: str | None = None


class AnalysisFilterModel(BaseModel):
    column: str
    operator: str
    value: str | float | int | bool | None = None


class DeriveColumnModel(BaseModel):
    name: str
    expr: dict  # wire-format Expression node — see expression.expr_from_dict
    result_type: str = "VARCHAR"


class WindowFrameModel(BaseModel):
    unit: str = "rows"
    preceding: int | None = None
    following: int = 0


class WindowExprModel(BaseModel):
    function_id: str
    args: list[dict] = []  # wire-format Expression nodes
    partition_by: list[str] = []
    order_by: list[SortSpec] = []
    frame: WindowFrameModel | None = None
    alias: str = "window_result"


class JoinSpecModel(BaseModel):
    local_source_id: str
    join_type: str = "left"
    keys: list[list[str]] = []  # each [left_column, right_column]


class AnalysisSpecModel(BaseModel):
    dimensions: list[str] = []
    measures: list[MeasureModel] = []
    filters: list[AnalysisFilterModel] = []
    sort: list[SortSpec] = []
    derive: list[DeriveColumnModel] = []
    join: JoinSpecModel | None = None
    distinct: bool = False
    having: list[AnalysisFilterModel] = []
    window: list[WindowExprModel] = []
    window_derive: list[DeriveColumnModel] = []
    qualify: list[AnalysisFilterModel] = []
    limit: int = 500


class ExploreRunBody(BaseModel):
    workspaceId: str
    steps: list[StepModel] = []
    stepIndex: int | None = None
    spec: AnalysisSpecModel


class ExplorePromoteBody(BaseModel):
    workspaceId: str
    steps: list[StepModel] = []
    stepIndex: int | None = None
    spec: AnalysisSpecModel
    name: str = ""


class RecipeBody(BaseModel):
    workspaceId: str
    steps: list[StepModel] = []
    stepIndex: int | None = None
    recipe: str
    params: dict


class AiAskBody(BaseModel):
    workspaceId: str
    steps: list[StepModel] = []
    stepIndex: int | None = None
    question: str


class AiPromoteBody(BaseModel):
    workspaceId: str
    sql: str
    sourceStepIndex: int | None = None
    question: str = ""
    stepId: str | None = None  # set to edit an existing sql_transform step in place


class AiRunSqlBody(BaseModel):
    workspaceId: str
    steps: list[StepModel] = []
    stepIndex: int | None = None
    sql: str


def create_app() -> FastAPI:
    app = FastAPI(title="PyJama backend")
    state = AppState()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    api = FastAPI()

    @api.post("/ping")
    def ping(body: PingBody):
        op = new_operation_id()
        log("round-trip smoke", operation_id=op, cmd="ping")
        return {"message": "pong", "echoed": body.message, "operation_id": op}

    # ---- Auth ----
    @api.post("/auth_connect")
    def auth_connect():
        try:
            return state.auth.connect()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(e))

    @api.post("/auth_logout")
    def auth_logout():
        state.auth.logout()
        return {}

    @api.post("/auth_status")
    def auth_status():
        return state.auth.is_authenticated()

    @api.post("/config_get")
    def config_get():
        c = state.auth.config
        return {
            "workspace_url": c.workspace_url,
            "client_id": c.client_id,
            "warehouse_id": c.warehouse_id,
            "staging_volume": c.staging_volume,
            "configured": c.is_configured(),
        }

    # ---- Unity Catalog ----
    @api.post("/catalog_list")
    def catalog_list():
        return [{"name": c["name"]} for c in _client(state).list_catalogs()]

    @api.post("/schema_list")
    def schema_list(body: CatalogBody):
        rows = _client(state).list_schemas(body.catalog)
        return [{"catalog": s.get("catalog_name", body.catalog), "name": s["name"]} for s in rows]

    @api.post("/table_list")
    def table_list(body: TablesBody):
        rows = _client(state).list_tables(body.catalog, body.schema)
        return [
            {
                "full_name": t.get("full_name") or f"{body.catalog}.{body.schema}.{t['name']}",
                "name": t["name"],
                "kind": t.get("table_type", "TABLE"),
            }
            for t in rows
        ]

    @api.post("/table_get")
    def table_get(body: FullNameBody):
        t = _client(state).get_table(body.fullName)
        return {
            "full_name": t.get("full_name", body.fullName),
            "columns": [
                {"name": c["name"], "type_name": c.get("type_text", ""), "nullable": c.get("nullable", True)}
                for c in t.get("columns", [])
            ],
            "row_count": None,
        }

    # ---- Warehouses ----
    @api.post("/warehouse_list")
    def warehouse_list():
        return [{"id": w["id"], "name": w.get("name", ""), "state": w.get("state", "")} for w in _client(state).list_warehouses()]

    @api.post("/warehouse_get")
    def warehouse_get(body: IdBody):
        w = _client(state).get_warehouse(body.id)
        return {"id": w["id"], "name": w.get("name", ""), "state": w.get("state", "")}

    @api.post("/warehouse_start")
    def warehouse_start(body: IdBody):
        _client(state).start_warehouse(body.id)
        return {}

    # ---- Checkout estimate (P9.7) ----
    @api.post("/checkout_estimate")
    def checkout_estimate(body: SelectSpikeBody):
        """Remote COUNT(*) with the chosen filters, before checkout starts."""
        try:
            predicates = [Predicate(f.column, FilterOp.parse(f.op), f.value) for f in body.filters]
            compiled = build_working_set_select(body.table, [], predicates)
            compiled.sql = compiled.sql.replace("SELECT *", "SELECT count(*) AS n", 1)
        except SqlError as e:
            raise HTTPException(status_code=400, detail=str(e))
        client = _client(state)
        try:
            resp = client.submit_statement(body.warehouseId, compiled.sql, compiled.params)
            statement_id = resp.get("statement_id", "")
            waited, delay = 0.0, 1.0
            while not is_terminal(resp["status"]["state"]):
                if waited >= 30:
                    raise HTTPException(status_code=504, detail="estimate timed out")
                time.sleep(delay)
                waited += delay
                delay = min(delay * 2, 5.0)
                resp = client.get_statement(statement_id)
            if resp["status"]["state"] != "SUCCEEDED":
                msg = resp["status"].get("error", {}).get("message", resp["status"]["state"])
                raise HTTPException(status_code=400, detail=msg)
            rows = statement_rows(resp)
            count = int(rows[0][0]) if rows else 0
            return {"row_count": count}
        except RestError as e:
            raise HTTPException(status_code=e.status, detail=e.message)

    # ---- Statement Execution spike (P1.10) ----
    @api.post("/run_select_spike")
    def run_select_spike(body: SelectSpikeBody):
        op = new_operation_id()
        try:
            predicates = [Predicate(f.column, FilterOp.parse(f.op), f.value) for f in body.filters]
            compiled = build_working_set_select(body.table, body.columns, predicates)
        except SqlError as e:
            raise HTTPException(status_code=400, detail=str(e))
        log("submitting parameterized select", operation_id=op, cmd="run_select_spike", table=body.table)

        client = _client(state)
        try:
            resp = client.submit_statement(body.warehouseId, compiled.sql, compiled.params)
            statement_id = resp.get("statement_id", "")
            delay, waited = 1.0, 0.0
            while not is_terminal(resp["status"]["state"]):
                if waited >= 60:
                    raise HTTPException(status_code=504, detail="timed out waiting for statement")
                time.sleep(delay)
                waited += delay
                delay = min(delay * 2, 5.0)
                resp = client.get_statement(statement_id)
            state_str = resp["status"]["state"]
            if state_str != "SUCCEEDED":
                msg = resp["status"].get("error", {}).get("message", state_str)
                raise HTTPException(status_code=400, detail=f"statement {state_str}: {msg}")
            cols = statement_columns(resp)
            return {"columns": cols, "rows": statement_rows(resp), "offset": 0, "total": None}
        except RestError as e:
            raise HTTPException(status_code=e.status, detail=e.message)

    # ---- Bounded sample preview while defining a checkout (helps pick/drop
    # columns and validate filters before committing to a full checkout) ----
    @api.post("/checkout_sample")
    def checkout_sample(body: SelectSpikeBody):
        try:
            predicates = [Predicate(f.column, FilterOp.parse(f.op), f.value) for f in body.filters]
            compiled = build_working_set_select(body.table, body.columns, predicates)
            compiled.sql += " LIMIT 20"
        except SqlError as e:
            raise HTTPException(status_code=400, detail=str(e))
        client = _client(state)
        try:
            resp = client.submit_statement(body.warehouseId, compiled.sql, compiled.params)
            statement_id = resp.get("statement_id", "")
            delay, waited = 1.0, 0.0
            while not is_terminal(resp["status"]["state"]):
                if waited >= 30:
                    raise HTTPException(status_code=504, detail="sample timed out")
                time.sleep(delay)
                waited += delay
                delay = min(delay * 2, 5.0)
                resp = client.get_statement(statement_id)
            state_str = resp["status"]["state"]
            if state_str != "SUCCEEDED":
                msg = resp["status"].get("error", {}).get("message", state_str)
                raise HTTPException(status_code=400, detail=f"statement {state_str}: {msg}")
            return {"columns": statement_columns(resp), "rows": statement_rows(resp)}
        except RestError as e:
            raise HTTPException(status_code=e.status, detail=e.message)

    # ---- Workspace fs (workspaces = "notebooks"; own no data, only reference sources) ----
    @api.post("/workspace_create")
    def workspace_create(body: NameBody):
        m = workspace.create(body.name)
        return _ws_summary(m)

    @api.post("/workspace_create_from_source")
    def workspace_create_from_source(body: CreateFromSourceBody):
        """Instantly open a new notebook against an already-imported source — no
        checkout needed, since the source is already local and decoupled."""
        try:
            src = sourcesmod.read_manifest(body.sourceId)
        except sourcesmod.SourceError as e:
            raise HTTPException(status_code=404, detail=str(e))
        m = workspace.create(body.name or src.name, primary_source_id=src.source_id)
        m.pipeline = [{"id": "src", "type": "source", "config": {}, "enabled": True}]
        workspace.write_manifest(m)
        return _ws_summary(m)

    @api.post("/workspace_open")
    def workspace_open(body: WorkspaceIdBody):
        m = workspace.read_manifest(body.workspaceId)
        return _ws_summary(m)

    @api.post("/workspace_list")
    def workspace_list():
        return workspace.list_workspaces()

    @api.post("/workspace_delete")
    def workspace_delete(body: WorkspaceIdBody):
        state.sessions.evict(body.workspaceId)
        workspace.delete_workspace(body.workspaceId)
        return {"ok": True}

    @api.post("/workspace_summaries")
    def workspace_summaries():
        """Saved workspaces ("notebooks") for the Home screen — survives app
        restart (§20). Source info is joined in for display only; the source
        itself is independent and may be shared by other notebooks."""
        out = []
        for wid in workspace.list_workspaces():
            try:
                m = workspace.read_manifest(wid)
            except Exception:  # noqa: BLE001
                continue
            out.append(_ws_summary(m))
        out.sort(key=lambda w: w["created_at"], reverse=True)
        return out

    # ---- Source registry (decoupled, shared across all notebooks) ----
    @api.post("/source_list")
    def source_list():
        return [_source_summary(m) for m in sourcesmod.list_sources()]

    @api.post("/source_delete")
    def source_delete(body: SourceIdBody):
        # Refuse if a workspace still references this source as primary — a
        # dangling notebook is worse than a blocked delete. Join references are
        # left to error clearly when that notebook is next opened.
        for wid in workspace.list_workspaces():
            try:
                m = workspace.read_manifest(wid)
            except Exception:  # noqa: BLE001
                continue
            if m.primary_source_id == body.sourceId:
                raise HTTPException(status_code=400, detail=f'source is used as the primary table of workspace "{m.name}" — delete or repoint that workspace first')
        sourcesmod.delete_source(body.sourceId, keystore=state.keystore)
        return {"ok": True}

    @api.post("/source_refresh")
    def source_refresh(body: SourceIdBody):
        """Re-checkout (uc_table) or re-read the original file (local formats)
        into the same source id — every notebook using it sees fresh data on
        next open."""
        try:
            src = sourcesmod.read_manifest(body.sourceId)
        except sourcesmod.SourceError as e:
            raise HTTPException(status_code=404, detail=str(e))

        if src.kind == "uc_table":
            client = _client(state)
            warehouse_id = state.auth.config.warehouse_id
            if not warehouse_id:
                raise HTTPException(status_code=400, detail="no warehouse configured")
            try:
                preds = [Predicate(f["column"], FilterOp.parse(f["op"]), f.get("value", "")) for f in (src.uc_filters or [])]
                compiled = build_working_set_select(src.uc_table, src.uc_columns, preds)
            except SqlError as e:
                raise HTTPException(status_code=400, detail=str(e))
            sourcesmod.clear_data(body.sourceId)
            engine = CheckoutEngine(client, state.keystore)
            op = new_operation_id()
            try:
                engine.run(src, warehouse_id, compiled, op)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(status_code=400, detail=str(e))
            from .commit import get_table_version

            version = get_table_version(client, warehouse_id, src.uc_table)
            src = sourcesmod.read_manifest(body.sourceId)
            if version is not None:
                src.uc_base_version = version
                sourcesmod.write_manifest(src)
        else:
            try:
                src = localsource.refresh_from_path(state.keystore, body.sourceId)
            except LocalSourceError as e:
                raise HTTPException(status_code=400, detail=str(e))

        # Any open session over this source (as primary or joined) is stale now.
        for wid in workspace.list_workspaces():
            try:
                m = workspace.read_manifest(wid)
            except Exception:  # noqa: BLE001
                continue
            uses_it = m.primary_source_id == body.sourceId or any(
                s.get("type") == "join_file" and s.get("config", {}).get("local_source_id") == body.sourceId for s in (m.pipeline or [])
            )
            if uses_it:
                state.sessions.evict(wid)
        return _source_summary(src)

    @api.post("/workspace_schema")
    def workspace_schema(body: WorkspaceIdBody):
        sess = state.sessions.get(body.workspaceId)
        return {"columns": sess.schema(), "total": sess.total}

    # ---- Windowed preview (Phase 3) ----
    @api.post("/preview_query")
    def preview_query(body: PreviewQueryBody):
        """Return a single window (offset/limit/sort in SQL). The UI never
        receives the full dataset (§11)."""
        sess = state.sessions.get(body.workspaceId)
        sort = [{"column": s.column, "direction": s.direction} for s in body.sort]
        return sess.query(offset=body.offset, limit=body.limit, sort=sort)

    # ---- Pipeline (Phase 4) ----
    @api.post("/preview_step")
    def preview_step(body: PipelinePreviewBody):
        """Compile the pipeline up to stepIndex and return a window of its output."""
        sess = state.sessions.get(body.workspaceId)
        steps = _to_steps(body.steps)
        sort = [{"column": s.column, "direction": s.direction} for s in body.sort]
        try:
            return sess.query_pipeline(steps, body.stepIndex, offset=body.offset, limit=body.limit, sort=sort)
        except (PipelineError, FormulaError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:  # DuckDB binder/parse errors → typed 400, not a 500
            raise HTTPException(status_code=400, detail=_short_db_error(e))

    @api.post("/pipeline_counts")
    def pipeline_counts(body: PipelineCountsBody):
        """Per-step output row counts for the pipeline panel."""
        sess = state.sessions.get(body.workspaceId)
        steps = _to_steps(body.steps)
        out = []
        for i, st_ in enumerate(steps):
            try:
                count = sess.pipeline_row_count(steps, i)
                out.append({"step_id": st_.id, "row_count": count, "error": None})
            except (PipelineError, FormulaError) as e:
                out.append({"step_id": st_.id, "row_count": None, "error": str(e)})
            except Exception as e:  # DuckDB errors reported per step, never a 500
                out.append({"step_id": st_.id, "row_count": None, "error": _short_db_error(e)})
        return out

    @api.post("/pipeline_save")
    def pipeline_save(body: PipelineSaveBody):
        m = workspace.read_manifest(body.workspaceId)
        m.pipeline = [{"id": s.id, "type": s.type, "config": s.config, "enabled": s.enabled, "input_id": s.input_id} for s in body.steps]
        m.pipeline_revision += 1
        workspace.write_manifest(m)
        return {"workspace_id": body.workspaceId, "pipeline_revision": m.pipeline_revision}

    @api.post("/pipeline_get")
    def pipeline_get(body: WorkspaceIdBody):
        m = workspace.read_manifest(body.workspaceId)
        return {"steps": m.pipeline, "pipeline_revision": m.pipeline_revision}

    # ---- Explore: AnalysisSpec (Phase 11, P11.7; Derive Phase 12, P12.3) ----
    def _to_analysis_spec(m: AnalysisSpecModel):
        try:
            derive = [aspec.DeriveColumn(name=d.name, expr=expression.expr_from_dict(d.expr), result_type=d.result_type) for d in m.derive]
            window_derive = [aspec.DeriveColumn(name=d.name, expr=expression.expr_from_dict(d.expr), result_type=d.result_type) for d in m.window_derive]
            window = [
                aspec.WindowExpr(
                    function_id=w.function_id,
                    args=[expression.expr_from_dict(a) for a in w.args],
                    partition_by=w.partition_by,
                    order_by=[aspec.SortSpec(column=s.column, direction=s.direction) for s in w.order_by],
                    frame=aspec.WindowFrame(unit=w.frame.unit, preceding=w.frame.preceding, following=w.frame.following) if w.frame else None,
                    alias=w.alias,
                )
                for w in m.window
            ]
        except expression.ExpressionError as e:
            raise HTTPException(status_code=400, detail=str(e))
        join = None
        if m.join:
            for k in m.join.keys:
                if len(k) != 2:
                    raise HTTPException(status_code=400, detail="join key must be [left_column, right_column]")
            join = aspec.JoinSpec(local_source_id=m.join.local_source_id, join_type=m.join.join_type, keys=[(k[0], k[1]) for k in m.join.keys])
        return aspec.AnalysisSpec(
            dimensions=m.dimensions,
            measures=[aspec.Measure(column=meas.column, aggregation=meas.aggregation, alias=meas.alias) for meas in m.measures],
            filters=[aspec.FilterCond(column=f.column, operator=f.operator, value=f.value) for f in m.filters],
            sort=[aspec.SortSpec(column=s.column, direction=s.direction) for s in m.sort],
            derive=derive,
            join=join,
            distinct=m.distinct,
            having=[aspec.FilterCond(column=f.column, operator=f.operator, value=f.value) for f in m.having],
            window=window,
            window_derive=window_derive,
            qualify=[aspec.FilterCond(column=f.column, operator=f.operator, value=f.value) for f in m.qualify],
            limit=m.limit,
        )

    @api.post("/explore_run_analysis")
    def explore_run_analysis(body: ExploreRunBody):
        """Ephemeral — executes an AnalysisSpec against the selected step's
        output. Never touches the pipeline or its revision (§39, §50)."""
        sess = state.sessions.get(body.workspaceId)
        steps = _to_steps(body.steps)
        spec = _to_analysis_spec(body.spec)
        try:
            return sess.run_analysis(steps, body.stepIndex, spec)
        except (PipelineError, aspec.AnalysisError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:  # DuckDB binder/parse errors -> typed 400
            raise HTTPException(status_code=400, detail=_short_db_error(e))

    def _build_recipe_spec(recipe: str, p: dict):
        def measure(d: dict) -> aspec.Measure:
            return aspec.Measure(column=d["column"], aggregation=d.get("aggregation", "count"), alias=d.get("alias"))

        def sort(d: dict) -> aspec.SortSpec:
            return aspec.SortSpec(column=d["column"], direction=d.get("direction", "asc"))

        try:
            if recipe == "summarize":
                return recipes.summarize(dimensions=p["dimensions"], measures=[measure(m) for m in p["measures"]], sort_desc=p.get("sort_desc", True), limit=p.get("limit", 500))
            if recipe == "trend":
                return recipes.trend(date_column=p["date_column"], grain=p["grain"], measure=measure(p["measure"]), group=p.get("group"), limit=p.get("limit", 500))
            if recipe == "top_bottom_n":
                return recipes.top_bottom_n(dimension=p["dimension"], measure=measure(p["measure"]), n=p["n"], partition=p.get("partition"), mode=p.get("mode", "top"), limit=p.get("limit", 500))
            if recipe == "compare_periods":
                return recipes.compare_periods(date_column=p["date_column"], grain=p["grain"], measure=measure(p["measure"]), group=p.get("group"), limit=p.get("limit", 500))
            if recipe == "running_total":
                return recipes.running_total(order_column=p["order_column"], measure=measure(p["measure"]), partition=p.get("partition"), limit=p.get("limit", 500))
            if recipe == "moving_average":
                return recipes.moving_average(order_column=p["order_column"], measure=measure(p["measure"]), window_width=p["window_width"], partition=p.get("partition"), limit=p.get("limit", 500))
            if recipe == "contribution":
                return recipes.contribution(dimension=p["dimension"], measure=measure(p["measure"]), partition=p.get("partition"), limit=p.get("limit", 500))
            if recipe == "duplicates":
                return recipes.duplicates(keys=p["keys"], tie_breaker=sort(p["tie_breaker"]) if p.get("tie_breaker") else None, limit=p.get("limit", 500))
            if recipe == "missing_values":
                return recipes.missing_values(field=p["field"], limit=p.get("limit", 500))
            if recipe == "distribution":
                return recipes.distribution(field=p["field"], bucket_width=p["bucket_width"], limit=p.get("limit", 500))
            raise HTTPException(status_code=400, detail=f"unknown recipe: {recipe}")
        except recipes.RecipeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except KeyError as e:
            raise HTTPException(status_code=400, detail=f"missing recipe parameter: {e}")

    @api.post("/explore_run_recipe")
    def explore_run_recipe(body: RecipeBody):
        """Intent recipes (Phase 12, P12.9 — §9): expand a named recipe +
        params into an AnalysisSpec, then run it exactly like
        explore_run_analysis. Ephemeral, same as any other Explore query."""
        spec = _build_recipe_spec(body.recipe, body.params)
        sess = state.sessions.get(body.workspaceId)
        steps = _to_steps(body.steps)
        try:
            result = sess.run_analysis(steps, body.stepIndex, spec)
        except (PipelineError, aspec.AnalysisError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:  # DuckDB binder/parse errors -> typed 400
            raise HTTPException(status_code=400, detail=_short_db_error(e))
        result["recipe"] = body.recipe
        return result

    @api.post("/explore_promote_recipe")
    def explore_promote_recipe(body: RecipeBody):
        """"Keep as workflow" for a recipe result — same promotion path as
        explore_promote_analysis, built from a recipe + params instead of a
        raw AnalysisSpec (P12.9 + P11.16 combined)."""
        spec = _build_recipe_spec(body.recipe, body.params)
        sess = state.sessions.get(body.workspaceId)
        steps = _to_steps(body.steps)
        try:
            validated_sql = sess.promote_analysis_sql(steps, body.stepIndex, spec)
        except (PipelineError, aspec.AnalysisError, ai_sql.SqlPolicyError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:  # DuckDB binder/parse errors -> typed 400
            raise HTTPException(status_code=400, detail=_short_db_error(e))

        m = workspace.read_manifest(body.workspaceId)
        step_id = f"explore-{new_operation_id()[:8]}"
        name = body.params.get("name") or f"{body.recipe}: " + aspec.default_step_name(spec)
        config = {"sql": validated_sql, "generated_by": "explore_recipe", "user_question": name}
        if spec.join:
            config["local_source_id"] = spec.join.local_source_id
        m.pipeline.append({"id": step_id, "type": "sql_transform", "config": config, "enabled": True})
        m.pipeline_revision += 1
        workspace.write_manifest(m)
        state.sessions.evict(body.workspaceId)
        return {"workspace_id": body.workspaceId, "step_id": step_id, "pipeline_revision": m.pipeline_revision, "name": name}

    @api.post("/explore_promote_analysis")
    def explore_promote_analysis(body: ExplorePromoteBody):
        """"Keep as workflow" (§26, P11.16) — turns a temporary Explore
        analysis into a durable, human-named sql_transform step. Analytical
        SELECTs from Explore never mutate the pipeline until this is called."""
        sess = state.sessions.get(body.workspaceId)
        steps = _to_steps(body.steps)
        spec = _to_analysis_spec(body.spec)
        try:
            validated_sql = sess.promote_analysis_sql(steps, body.stepIndex, spec)
        except (PipelineError, aspec.AnalysisError, ai_sql.SqlPolicyError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:  # DuckDB binder/parse errors -> typed 400
            raise HTTPException(status_code=400, detail=_short_db_error(e))

        m = workspace.read_manifest(body.workspaceId)
        step_id = f"explore-{new_operation_id()[:8]}"
        name = body.name or aspec.default_step_name(spec)
        config = {"sql": validated_sql, "generated_by": "explore_analysis", "user_question": name}
        if spec.join:
            # Keeps the join's right-side relation resolvable on every future
            # WorkspaceSession open — see query.py's referenced_ids scan.
            config["local_source_id"] = spec.join.local_source_id
        m.pipeline.append({
            "id": step_id,
            "type": "sql_transform",
            "config": config,
            "enabled": True,
        })
        m.pipeline_revision += 1
        workspace.write_manifest(m)
        state.sessions.evict(body.workspaceId)
        return {"workspace_id": body.workspaceId, "step_id": step_id, "pipeline_revision": m.pipeline_revision, "name": name}

    @api.post("/column_stats")
    def column_stats(body: DistinctValuesBody):
        sess = state.sessions.get(body.workspaceId)
        try:
            return sess.column_stats(body.column)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=400, detail=_short_db_error(e))

    @api.post("/column_distinct_values")
    def column_distinct_values(body: DistinctValuesBody):
        sess = state.sessions.get(body.workspaceId)
        try:
            return sess.distinct_values(body.column, limit=body.limit)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=400, detail=_short_db_error(e))

    # ---- Row identity, diff, validation (Phase 6) ----
    @api.post("/row_key_get")
    def row_key_get(body: WorkspaceIdBody):
        m = workspace.read_manifest(body.workspaceId)
        sess = state.sessions.get(body.workspaceId)
        return {"keys": m.row_key, "columns": [c["name"] for c in sess.schema()]}

    @api.post("/row_key_set")
    def row_key_set(body: RowKeyBody):
        sess = state.sessions.get(body.workspaceId)
        res = sess.verify_row_key(body.keys)
        if res.get("unique"):
            m = workspace.read_manifest(body.workspaceId)
            m.row_key = body.keys
            workspace.write_manifest(m)
        return res

    @api.post("/diff_compute")
    def diff_compute(body: DiffBody):
        from .diff import DiffError

        sess = state.sessions.get(body.workspaceId)
        keys = body.keys or workspace.read_manifest(body.workspaceId).row_key
        if not keys:
            raise HTTPException(status_code=400, detail="choose a row identifier before reviewing changes")
        try:
            return sess.diff(_to_steps(body.steps), keys)
        except DiffError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=400, detail=_short_db_error(e))

    @api.post("/validation_run")
    def validation_run(body: ValidateBody):
        sess = state.sessions.get(body.workspaceId)
        try:
            return sess.validate(_to_steps(body.steps), [r.model_dump() for r in body.rules])
        except Exception as e:
            raise HTTPException(status_code=400, detail=_short_db_error(e))

    # ---- Local files (Phase 5) — imports land in the shared source registry,
    # independent of any workspace, and become usable in any notebook. ----
    @api.post("/local_source_import")
    async def local_source_import(file: UploadFile = File(...)):
        data = await file.read()
        try:
            fmt = localsource.detect_format(file.filename or "")
            src = localsource.import_bytes(state.keystore, file.filename or "file", fmt, data)
        except LocalSourceError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return _source_summary(src)

    @api.post("/local_source_list")
    def local_source_list():
        """All sources available to join into any notebook (global registry)."""
        return [_source_summary(m) for m in sourcesmod.list_sources()]

    @api.post("/local_source_from_uc")
    def local_source_from_uc(body: UcSourceBody):
        """Check out a second UC table (governed reduction) as a new, shared
        joinable source — usable from any notebook, not just the one that
        triggered the import."""
        warehouse_id = state.auth.config.warehouse_id
        if not warehouse_id:
            raise HTTPException(status_code=400, detail="no warehouse configured")
        client = _client(state)
        try:
            predicates = [Predicate(f.column, FilterOp.parse(f.op), f.value) for f in body.filters]
            compiled = build_working_set_select(body.table, body.columns, predicates)
        except SqlError as e:
            raise HTTPException(status_code=400, detail=str(e))

        from .checkout import CheckoutError, fetch_result_arrow

        try:
            table = fetch_result_arrow(client, warehouse_id, compiled.sql, compiled.params)
        except (RestError, CheckoutError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        src = localsource.import_arrow_table(state.keystore, body.table.split(".")[-1], "uc_table", table)
        src.uc_table = body.table
        src.uc_columns = body.columns
        src.uc_filters = [f.model_dump() for f in body.filters]
        version = None
        try:
            from .commit import get_table_version

            version = get_table_version(client, warehouse_id, body.table)
        except Exception:  # noqa: BLE001
            pass
        src.uc_base_version = version
        sourcesmod.write_manifest(src)
        return _source_summary(src)

    @api.post("/watch_scan")
    def watch_scan(body: WatchScanBody):
        return watchfolder.scan(body.folder)

    @api.post("/watch_import")
    def watch_import(body: WatchImportBody):
        try:
            src = watchfolder.import_path(state.keystore, body.path)
        except LocalSourceError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return _source_summary(src)

    # ---- Commit write-back (Phase 7) ----
    @api.post("/commit_readiness")
    def commit_readiness(body: DiffBody):
        keys = body.keys or workspace.read_manifest(body.workspaceId).row_key
        sess = state.sessions.get(body.workspaceId)
        checks = {
            "authenticated": state.auth.is_authenticated(),
            "row_key": bool(keys) and sess.verify_row_key(keys).get("unique", False),
            "warehouse": bool(state.auth.config.warehouse_id),
        }
        # A staging volume is only needed for very large commits (inline SQL
        # handles bounded change sets), so it is not a blocking check.
        return {"checks": checks, "ready": all(checks.values()), "keys": keys, "staging_volume": state.auth.config.staging_volume}

    @api.post("/commit_start")
    def commit_start(body: CommitBody):
        keys = body.keys or workspace.read_manifest(body.workspaceId).row_key
        if not keys:
            raise HTTPException(status_code=400, detail="choose a row identifier before committing")
        client = _client(state)
        warehouse_id = state.auth.config.warehouse_id
        if not warehouse_id:
            raise HTTPException(status_code=400, detail="no warehouse configured")

        sess = state.sessions.get(body.workspaceId)
        from .diff import DiffError

        try:
            if body.createNew:
                # New table gets the full transformed output, not just the diff.
                change_table = sess.full_output(_to_steps(body.steps))
                source_columns = list(change_table.column_names)
            else:
                change_table = sess.build_change_set(_to_steps(body.steps), keys)
                source_columns = [c for c in sess.schema_columns() if c in change_table.column_names]
        except DiffError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=400, detail=_short_db_error(e))

        manifest = workspace.read_manifest(body.workspaceId)
        target = body.targetTable
        create_new = body.createNew
        primary_source = None
        if manifest.primary_source_id:
            try:
                primary_source = sourcesmod.read_manifest(manifest.primary_source_id)
            except sourcesmod.SourceError:
                primary_source = None
        base_version = primary_source.uc_base_version if primary_source else None
        source_table = primary_source.uc_table if primary_source else None

        op = new_operation_id()
        status = {"operation_id": op, "state": "PENDING", "error": None, "result": None}
        with state.lock:
            state.checkouts[op] = status

        def worker():
            from .commit import CommitError, ConflictError, commit_change_set

            def prog(s):
                with state.lock:
                    state.checkouts[op].update(s)

            try:
                result = commit_change_set(
                    client, warehouse_id, change_table,
                    target_table=target, keys=keys, source_columns=source_columns,
                    staging_volume=state.auth.config.staging_volume or "",
                    create_new=create_new, base_version=base_version,
                    source_table=source_table, progress=prog,
                )
                # Record the new Delta version on the primary source, so future
                # commits from any notebook using it detect drift correctly.
                if result.get("new_version") is not None and manifest.primary_source_id:
                    try:
                        src = sourcesmod.read_manifest(manifest.primary_source_id)
                        src.uc_base_version = result["new_version"]
                        sourcesmod.write_manifest(src)
                    except sourcesmod.SourceError:
                        pass
                with state.lock:
                    state.checkouts[op].update({"state": "COMMITTED", "result": result})
            except ConflictError as e:
                with state.lock:
                    state.checkouts[op].update({"state": "CONFLICT", "error": str(e), "base_version": e.base_version, "current_version": e.current_version})
            except (CommitError, RestError) as e:
                with state.lock:
                    state.checkouts[op].update({"state": "FAILED", "error": str(e)})
            except Exception as e:  # noqa: BLE001
                with state.lock:
                    state.checkouts[op].update({"state": "FAILED", "error": _short_db_error(e)})

        threading.Thread(target=worker, daemon=True).start()
        return {"operation_id": op, "change_count": change_table.num_rows}

    @api.post("/commit_status")
    def commit_status(body: OpBody):
        with state.lock:
            return state.checkouts.get(body.operationId, {"state": "UNKNOWN"})

    @api.post("/namespace_check")
    def namespace_check(body: NamespaceBody):
        """Does the target catalog / schema exist (for New-table commits)?"""
        client = _client(state)
        try:
            cats = client.list_catalogs()
        except RestError as e:
            raise HTTPException(status_code=400, detail=e.message)
        cat_exists = any(c.get("name") == body.catalog for c in cats)
        sch_exists = False
        if cat_exists and body.schema:
            try:
                schemas = client.list_schemas(body.catalog)
                sch_exists = any(s.get("name") == body.schema for s in schemas)
            except RestError:
                sch_exists = False
        return {"catalog_exists": cat_exists, "schema_exists": sch_exists}

    @api.post("/namespace_create")
    def namespace_create(body: NamespaceCreateBody):
        """Create the catalog and/or schema (only what the caller confirmed)."""
        from .dbsql import quote_ident, quote_qualified

        client = _client(state)
        wh = state.auth.config.warehouse_id
        if not wh:
            raise HTTPException(status_code=400, detail="no warehouse configured")
        try:
            if body.createCatalog:
                client.run_sql_sync(wh, f"CREATE CATALOG IF NOT EXISTS {quote_ident(body.catalog)}")
            if body.createSchema:
                client.run_sql_sync(wh, f"CREATE SCHEMA IF NOT EXISTS {quote_qualified(body.catalog + '.' + body.schema)}")
        except RestError as e:
            raise HTTPException(status_code=400, detail=e.message)
        return {"ok": True}

    # ---- Encrypted checkout (Phase 2 + Sources refactor) ----
    # Checkout creates a standalone *source* (reusable in any notebook) and a
    # *workspace* ("notebook") whose pipeline reads it as the primary table.
    @api.post("/checkout_start")
    def checkout_start(body: CheckoutBody):
        try:
            predicates = [Predicate(f.column, FilterOp.parse(f.op), f.value) for f in body.filters]
            compiled = build_working_set_select(body.table, body.columns, predicates)
        except SqlError as e:
            raise HTTPException(status_code=400, detail=str(e))

        warehouse_id = state.auth.config.warehouse_id
        if not warehouse_id:
            raise HTTPException(status_code=400, detail="no warehouse configured")
        client = _client(state)  # raises 401 if not authenticated

        op = new_operation_id()
        table_short = body.table.split(".")[-1] or "table"
        src = sourcesmod.create_placeholder(table_short, "uc_table")
        src.uc_table = body.table
        src.uc_columns = body.columns
        src.uc_filters = [f.model_dump() for f in body.filters]
        # Record the source Delta version at checkout for conflict detection (§16).
        from .commit import get_table_version

        version = get_table_version(client, warehouse_id, body.table)
        src.uc_base_version = version
        sourcesmod.write_manifest(src)

        # Checkout can be launched from the Sources tab (no workspace wanted
        # yet) or from the "New Workspace" flow (open a notebook on it right
        # after). The source itself is created either way — decoupled.
        workspace_id = None
        if body.createWorkspace:
            m = workspace.create(table_short + " Workspace", primary_source_id=src.source_id)
            m.row_key = body.rowKey
            m.pipeline = [{"id": "src", "type": "source", "config": {}, "enabled": True}]
            workspace.write_manifest(m)
            workspace_id = m.workspace_id

        status = {
            "operation_id": op, "workspace_id": workspace_id, "source_id": src.source_id, "state": "PENDING",
            "completed_chunks": 0, "total_chunks": None, "row_count": 0, "error": None,
        }
        with state.lock:
            state.checkouts[op] = status

        def worker():
            engine = CheckoutEngine(client, state.keystore)

            def prog(s: dict):
                with state.lock:
                    state.checkouts[op].update(s)

            try:
                engine.run(src, warehouse_id, compiled, op, progress=prog)
            except Exception as e:  # noqa: BLE001
                with state.lock:
                    state.checkouts[op].update({"state": "FAILED", "error": str(e)})

        threading.Thread(target=worker, daemon=True).start()
        return {"operation_id": op, "workspace_id": workspace_id, "source_id": src.source_id}

    @api.post("/checkout_status")
    def checkout_status(body: OpBody):
        with state.lock:
            return state.checkouts.get(body.operationId, {"state": "UNKNOWN"})

    # ---- Local NL-to-SQL assistant (Phase 10) ----

    @api.post("/ai_status")
    def ai_status():
        st = ai_model.status()
        return {
            "installed": st.installed,
            "verified": st.verified,
            "smoke_test_passed": st.smoke_test_passed,
            "model_id": st.model_id,
            "size_bytes": st.size_bytes,
            "runtime_running": ai_runtime.is_running(),
        }

    @api.post("/ai_model_install")
    def ai_model_install_endpoint():
        op = new_operation_id()
        status = {"operation_id": op, "state": "DOWNLOADING", "bytes": 0, "total_bytes": ai_model.MANIFEST["size_bytes"], "error": None}
        with state.lock:
            state.ai_ops[op] = status

        def worker():
            def prog(written: int, total: int):
                with state.lock:
                    state.ai_ops[op].update({"bytes": written, "total_bytes": total})
            try:
                if ai_model.model_path().exists():
                    # Already present (pre-bundled/converted locally) — just
                    # verify the checksum rather than attempt a network download.
                    with state.lock:
                        state.ai_ops[op].update({"bytes": ai_model.MANIFEST["size_bytes"], "total_bytes": ai_model.MANIFEST["size_bytes"]})
                    if not ai_model.verify():
                        raise ai_model.ModelError("installed model failed checksum verification")
                else:
                    ai_model.install(progress=prog)
                # Smoke test (P10.3): one deterministic prompt must produce
                # parseable SQL before the model is offered to the UI.
                try:
                    ai_runtime.start()
                    messages = ai_sql.build_prompt(
                        [{"name": "n", "type": "BIGINT"}], "count the rows"
                    )
                    raw = ai_runtime.generate(messages)
                    ai_sql.validate_sql(raw)
                    ai_model.mark_smoke_test_passed(True)
                except Exception:
                    ai_model.mark_smoke_test_passed(False)
                with state.lock:
                    state.ai_ops[op].update({"state": "INSTALLED"})
            except Exception as e:  # noqa: BLE001
                with state.lock:
                    state.ai_ops[op].update({"state": "FAILED", "error": str(e)})

        threading.Thread(target=worker, daemon=True).start()
        return {"operation_id": op}

    @api.post("/ai_model_install_status")
    def ai_model_install_status(body: OpBody):
        with state.lock:
            return state.ai_ops.get(body.operationId, {"state": "UNKNOWN"})

    @api.post("/ai_model_uninstall")
    def ai_model_uninstall():
        ai_runtime.stop()
        ai_model.uninstall()
        return {"ok": True}

    @api.post("/ai_ask")
    def ai_ask(body: AiAskBody):
        sess = state.sessions.get(body.workspaceId)
        steps = _to_steps(body.steps)
        try:
            table, schema = sess.step_output(steps, body.stepIndex)
        except PipelineError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if not ai_model.status().installed:
            raise HTTPException(status_code=400, detail="Local AI model is not installed")
        result = exploration_service.ask(table, schema, body.question)
        return {
            "id": result.id,
            "question": result.question,
            "status": result.status,
            "generated_sql": result.generated_sql,
            "attempt_count": result.attempt_count,
            "inference_ms": result.inference_ms,
            "execution_ms": result.execution_ms,
            "rows_returned": result.rows_returned,
            "columns": result.columns,
            "preview_rows": result.preview_rows,
            "error": result.error,
        }

    @api.post("/ai_run_sql")
    def ai_run_sql(body: AiRunSqlBody):
        """Run user-edited SQL directly against `current` — no model call.
        Lets a person tweak the AI's SQL (or an existing sql_transform step's
        SQL) and see the result before saving it."""
        base = {"id": "", "question": "", "attempt_count": 0, "inference_ms": 0, "execution_ms": 0}
        sess = state.sessions.get(body.workspaceId)
        steps = _to_steps(body.steps)
        try:
            table, _schema = sess.step_output(steps, body.stepIndex)
        except PipelineError as e:
            raise HTTPException(status_code=400, detail=str(e))
        try:
            validated = ai_sql.validate_sql(body.sql)
        except ai_sql.SqlPolicyError as e:
            return {**base, "status": "BLOCKED", "generated_sql": None, "error": str(e), "columns": [], "preview_rows": [], "rows_returned": 0}
        con = ai_sql.restricted_connection(table)
        try:
            arrow_result = ai_sql.explain_and_execute(con, validated)
        except Exception as e:  # noqa: BLE001
            return {**base, "status": "INVALID", "generated_sql": None, "error": _short_db_error(e), "columns": [], "preview_rows": [], "rows_returned": 0}
        finally:
            con.close()
        cols = list(arrow_result.column_names)
        col_lists = [arrow_result.column(c).to_pylist() for c in cols]
        rows = [list(r) for r in zip(*col_lists)] if col_lists and arrow_result.num_rows else []
        return {**base, "status": "SUCCESS", "generated_sql": validated, "error": None, "columns": cols, "preview_rows": rows[:500], "rows_returned": arrow_result.num_rows}

    @api.post("/ai_promote")
    def ai_promote(body: AiPromoteBody):
        try:
            validated = promote_sql(body.sql)
        except ai_sql.SqlPolicyError as e:
            raise HTTPException(status_code=400, detail=str(e))
        m = workspace.read_manifest(body.workspaceId)

        existing = next((s for s in m.pipeline if s.get("id") == body.stepId and s.get("type") == "sql_transform"), None) if body.stepId else None
        if existing is not None:
            existing["config"] = {**existing.get("config", {}), "sql": validated, "user_question": body.question or existing.get("config", {}).get("user_question", "")}
            step_id = existing["id"]
        else:
            step_id = f"ai-{new_operation_id()[:8]}"
            m.pipeline.append({
                "id": step_id,
                "type": "sql_transform",
                "config": {"sql": validated, "generated_by": "local_ai", "user_question": body.question},
                "enabled": True,
            })
        m.pipeline_revision += 1
        workspace.write_manifest(m)
        state.sessions.evict(body.workspaceId)
        return {"workspace_id": body.workspaceId, "step_id": step_id, "pipeline_revision": m.pipeline_revision}

    app.mount("/api", api)

    # Serve the built React UI (pywebview loads this origin). When frozen by
    # PyInstaller the UI is bundled under sys._MEIPASS/ui; in dev it's the
    # sibling frontend/dist.
    import sys

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and (Path(meipass) / "ui").exists():
        dist = Path(meipass) / "ui"
    else:
        dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="ui")

    return app


def _ws_summary(m: workspace.Manifest) -> dict:
    """Workspace ("notebook") summary. Source display fields are joined in from
    the primary source's own manifest — the workspace itself owns no data."""
    src = None
    if m.primary_source_id:
        try:
            src = sourcesmod.read_manifest(m.primary_source_id)
        except sourcesmod.SourceError:
            src = None
    return {
        "workspace_id": m.workspace_id,
        "name": m.name,
        "created_at": m.created_at,
        "primary_source_id": m.primary_source_id,
        "source_table": (src.uc_table if src else None) or (src.name if src else ""),
        "base_version": src.uc_base_version if src else None,
        "pipeline_revision": m.pipeline_revision,
        "row_count": src.row_count if src else 0,
        "logical_bytes": src.logical_bytes if src else 0,
    }


def _source_summary(m: "sourcesmod.SourceManifest") -> dict:
    return {
        "source_id": m.source_id,
        "name": m.name,
        "kind": m.kind,
        "created_at": m.created_at,
        "refreshed_at": m.refreshed_at,
        "columns": m.columns,
        "row_count": m.row_count,
        "logical_bytes": m.logical_bytes,
        "uc_table": m.uc_table,
        "refreshable": m.kind == "uc_table" or bool(m.local_path),
        # Kept for backward-compat with the join picker, which expects `id`.
        "id": m.source_id,
    }
