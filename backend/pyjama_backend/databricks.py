"""Databricks REST client — the read path.

Unity Catalog browsing (§7), SQL warehouse lifecycle (§8.2), Statement Execution
(§8.4/§8.5). Phase 1 uses inline JSON results for small SELECTs; Phase 2 will add
ARROW_STREAM + EXTERNAL_LINKS streaming on the same client.

Constructed with base URL + access token so tests can point it at a mock server.
The bearer token is attached per request and never logged.
"""

from __future__ import annotations

from .dbsql import StatementParam
from .logging_setup import Secret

import requests

TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELED", "CLOSED"}


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


class RestError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"databricks api {status}: {message}")
        self.status = status
        self.message = message


class DatabricksClient:
    def __init__(self, base: str, access_token: Secret, session: requests.Session | None = None):
        self._base = base.rstrip("/")
        self._token = access_token
        self._http = session or requests.Session()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token.expose()}"}

    def _get(self, path: str, params: dict | None = None) -> dict:
        r = self._http.get(self._base + path, headers=self._headers(), params=params, timeout=60)
        return self._parse(r)

    def _post(self, path: str, body: dict | None = None) -> dict:
        r = self._http.post(self._base + path, headers=self._headers(), json=body, timeout=120)
        return self._parse(r)

    @staticmethod
    def _parse(r: requests.Response) -> dict:
        if r.ok:
            return r.json() if r.content else {}
        try:
            msg = r.json().get("message", "unknown error")
        except Exception:
            msg = r.text[:200] or "unknown error"
        raise RestError(r.status_code, msg)

    # ---- Unity Catalog (§7) ----

    def list_catalogs(self) -> list[dict]:
        return self._get("/api/2.1/unity-catalog/catalogs").get("catalogs", []) or []

    def list_schemas(self, catalog: str) -> list[dict]:
        return self._get("/api/2.1/unity-catalog/schemas", {"catalog_name": catalog}).get("schemas", []) or []

    def list_tables(self, catalog: str, schema: str) -> list[dict]:
        return self._get(
            "/api/2.1/unity-catalog/tables",
            {"catalog_name": catalog, "schema_name": schema},
        ).get("tables", []) or []

    def get_table(self, full_name: str) -> dict:
        return self._get(f"/api/2.1/unity-catalog/tables/{full_name}")

    # ---- Warehouses (§8.2) ----

    def list_warehouses(self) -> list[dict]:
        return self._get("/api/2.0/sql/warehouses").get("warehouses", []) or []

    def get_warehouse(self, warehouse_id: str) -> dict:
        return self._get(f"/api/2.0/sql/warehouses/{warehouse_id}")

    def start_warehouse(self, warehouse_id: str) -> None:
        self._post(f"/api/2.0/sql/warehouses/{warehouse_id}/start")

    # ---- Statement Execution (§8.4/§8.5) — inline JSON for the read-path spike ----

    def submit_statement(self, warehouse_id: str, sql: str, params: list[StatementParam]) -> dict:
        return self._post("/api/2.0/sql/statements", {
            "warehouse_id": warehouse_id,
            "statement": sql,
            "parameters": [p.to_api() for p in params],
            "format": "JSON_ARRAY",
            "disposition": "INLINE",
            "wait_timeout": "10s",
            "on_wait_timeout": "CONTINUE",
        })

    def get_statement(self, statement_id: str) -> dict:
        return self._get(f"/api/2.0/sql/statements/{statement_id}")

    def cancel_statement(self, statement_id: str) -> None:
        self._post(f"/api/2.0/sql/statements/{statement_id}/cancel")

    # ---- Statement Execution: Arrow + external links (Phase 2, §8.4/§8.6) ----

    def submit_statement_arrow(self, warehouse_id: str, sql: str, params: list[StatementParam]) -> dict:
        """Submit for a large result: Arrow stream chunks behind presigned URLs.
        Fully async (wait_timeout 0s) — poll get_statement until terminal."""
        return self._post("/api/2.0/sql/statements", {
            "warehouse_id": warehouse_id,
            "statement": sql,
            "parameters": [p.to_api() for p in params],
            "format": "ARROW_STREAM",
            "disposition": "EXTERNAL_LINKS",
            "wait_timeout": "0s",
        })

    def get_chunk_link(self, statement_id: str, chunk_index: int) -> dict:
        """Fetch the presigned external link for a result chunk. Short-lived and
        secret — never logged."""
        resp = self._get(f"/api/2.0/sql/statements/{statement_id}/result/chunks/{chunk_index}")
        links = resp.get("external_links", [])
        if not links:
            raise RestError(500, f"no external link for chunk {chunk_index}")
        return links[0]

    def download_external(self, url: str) -> bytes:
        """Download a presigned chunk over TLS. CRITICAL: do NOT send the
        Databricks Authorization header to the storage host (§8.6). Uses a bare
        request with no default session headers/auth."""
        r = requests.get(url, timeout=300)  # no self._http (which could carry auth), no headers
        if not r.ok:
            raise RestError(r.status_code, f"chunk download failed: {r.status_code}")
        return r.content


    # ---- Sync SQL + UC Volume staging (Phase 7) ----

    def run_sql_sync(self, warehouse_id: str, sql: str, params: list | None = None, poll_budget_s: float = 180.0) -> dict:
        """Submit a statement (inline JSON) and block until terminal. Used for
        DESCRIBE HISTORY, MERGE, CREATE TABLE."""
        import time as _time

        resp = self.submit_statement(warehouse_id, sql, params or [])
        sid = resp.get("statement_id", "")
        waited, delay = 0.0, 1.0
        while not is_terminal(resp["status"]["state"]):
            if waited >= poll_budget_s:
                raise RestError(504, "statement timed out")
            _time.sleep(delay)
            waited += delay
            delay = min(delay * 2, 5.0)
            resp = self.get_statement(sid)
        if resp["status"]["state"] != "SUCCEEDED":
            raise RestError(400, resp["status"].get("error", {}).get("message", resp["status"]["state"]))
        return resp

    def upload_volume_file(self, volume_path: str, data: bytes) -> None:
        """PUT a file to a Unity Catalog Volume via the Files API. `volume_path`
        begins with /Volumes/<catalog>/<schema>/<volume>/..."""
        url = f"{self._base}/api/2.0/fs/files{volume_path}"
        r = self._http.put(url, headers={**self._headers(), "Content-Type": "application/octet-stream"}, params={"overwrite": "true"}, data=data, timeout=300)
        if not r.ok:
            raise RestError(r.status_code, (r.text or "upload failed")[:200])

    def delete_volume_file(self, volume_path: str) -> None:
        url = f"{self._base}/api/2.0/fs/files{volume_path}"
        try:
            self._http.delete(url, headers=self._headers(), timeout=60)
        except Exception:
            pass  # best-effort cleanup


def statement_manifest_chunks(resp: dict) -> list[dict]:
    """Per-chunk metadata (chunk_index, row_count, byte_count) from the manifest."""
    return resp.get("manifest", {}).get("chunks", []) or []


def statement_total_chunks(resp: dict) -> int | None:
    return resp.get("manifest", {}).get("total_chunk_count")


def statement_columns(resp: dict) -> list[str]:
    cols = resp.get("manifest", {}).get("schema", {}).get("columns", [])
    return [c["name"] for c in cols]


def statement_rows(resp: dict) -> list[list]:
    return resp.get("result", {}).get("data_array", []) or []
