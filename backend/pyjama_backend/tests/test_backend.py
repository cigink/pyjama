import time

import pytest
import responses

from pyjama_backend import databricks, oauth, workspace
from pyjama_backend.auth import AuthService, NotAuthenticated
from pyjama_backend.config import ConfigError, DatabricksConfig
from pyjama_backend.dbsql import FilterOp, Predicate, SqlError, build_working_set_select, quote_ident, quote_qualified
from pyjama_backend.keystore import MemoryKeyStore
from pyjama_backend.logging_setup import Secret, scrub
from pyjama_backend.pkce import Pkce, s256_challenge


# ---- logging / redaction ----
def test_secret_never_renders_value():
    s = Secret("dapi-super-secret-token")
    assert str(s) == "***"
    assert repr(s) == "Secret(***)"
    assert s.expose() == "dapi-super-secret-token"


def test_scrub_redacts_tokens_and_urls():
    line = "GET https://store.example/chunk?sig=abc123 token eyJhbGciOiJIUzI1NiJ9.aVeryLongOpaqueAccessTokenValue1234567890"
    out = scrub(line)
    assert "eyJhbGci" not in out
    assert "sig=abc123" not in out
    assert "***" in out


# ---- config ----
def test_config_validation():
    c = DatabricksConfig(workspace_url="https://x.cloud.databricks.com")
    assert c.is_configured()
    assert c.base_url() == "https://x.cloud.databricks.com"
    with pytest.raises(ConfigError):
        DatabricksConfig(workspace_url="http://insecure").base_url()
    with pytest.raises(ConfigError):
        DatabricksConfig().base_url()


# ---- dbsql ----
def test_quote_and_escape():
    assert quote_ident("customer_id") == "`customer_id`"
    assert quote_ident("a`b") == "`a``b`"
    assert quote_qualified("main.crm.customers") == "`main`.`crm`.`customers`"
    with pytest.raises(SqlError):
        quote_ident("")


def test_parameterized_select():
    q = build_working_set_select(
        "main.crm.customers",
        ["customer_id", "country"],
        [Predicate("country", FilterOp.EQ, "NL"), Predicate("updated_at", FilterOp.AFTER, "2026-01-01")],
    )
    assert q.sql == "SELECT `customer_id`, `country` FROM `main`.`crm`.`customers` WHERE `country` = :p0 AND `updated_at` > :p1"
    assert q.params[0].value == "NL"
    assert "NL" not in q.sql


def test_injection_stays_in_params():
    q = build_working_set_select("t", ["c"], [Predicate("c", FilterOp.EQ, "x'; DROP TABLE t;--")])
    assert "DROP TABLE" not in q.sql
    assert q.params[0].value == "x'; DROP TABLE t;--"


def test_contains_null_inlist():
    q = build_working_set_select(
        "t", [],
        [
            Predicate("name", FilterOp.CONTAINS, "aco"),
            Predicate("email", FilterOp.IS_NOT_NULL),
            Predicate("region", FilterOp.IN_LIST, "EMEA, APAC"),
        ],
    )
    assert q.sql == "SELECT * FROM `t` WHERE `name` LIKE :p0 AND `email` IS NOT NULL AND `region` IN (:p2_0, :p2_1)"
    assert q.params[0].value == "%aco%"
    assert [p.value for p in q.params] == ["%aco%", "EMEA", "APAC"]


# ---- pkce ----
def test_pkce_rfc_vector():
    assert s256_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_pkce_unique():
    a, b = Pkce.generate(), Pkce.generate()
    assert a.verifier.expose() != b.verifier.expose()
    assert s256_challenge(a.verifier.expose()) == a.challenge


# ---- oauth ----
def test_extract_code_checks_state():
    assert oauth.extract_code("http://localhost:8020/?code=abc&state=xyz", "xyz") == "abc"
    with pytest.raises(oauth.OAuthError):
        oauth.extract_code("http://localhost:8020/?code=abc&state=WRONG", "xyz")
    with pytest.raises(oauth.OAuthError):
        oauth.extract_code("http://localhost:8020/?state=xyz", "xyz")


@responses.activate
def test_exchange_code_parses_tokens():
    responses.add(responses.POST, "https://x/oidc/v1/token",
                  json={"access_token": "AT", "refresh_token": "RT", "expires_in": 3600, "scope": "all-apis offline_access"})
    tokens = oauth.exchange_code("https://x", "databricks-cli", "code", Secret("verifier"), "http://localhost:8020")
    assert tokens.access_token.expose() == "AT"
    assert tokens.refresh_token.expose() == "RT"
    assert tokens.expires_at > time.time()


@responses.activate
def test_refresh_failure_no_leak():
    responses.add(responses.POST, "https://x/oidc/v1/token", status=401, body="invalid_grant")
    with pytest.raises(oauth.OAuthError) as ei:
        oauth.refresh_tokens("https://x", "databricks-cli", Secret("super-secret-refresh"))
    assert "super-secret-refresh" not in str(ei.value)


# ---- databricks REST ----
@responses.activate
def test_list_catalogs_with_bearer():
    responses.add(responses.GET, "https://x/api/2.1/unity-catalog/catalogs",
                  json={"catalogs": [{"name": "main"}, {"name": "samples"}]})
    client = databricks.DatabricksClient("https://x", Secret("tok"))
    assert [c["name"] for c in client.list_catalogs()] == ["main", "samples"]
    assert responses.calls[0].request.headers["Authorization"] == "Bearer tok"


@responses.activate
def test_api_error_maps_status():
    responses.add(responses.GET, "https://x/api/2.0/sql/warehouses", status=403,
                  json={"message": "no access"})
    client = databricks.DatabricksClient("https://x", Secret("tok"))
    with pytest.raises(databricks.RestError) as ei:
        client.list_warehouses()
    assert ei.value.status == 403 and ei.value.message == "no access"


@responses.activate
def test_statement_inline_rows():
    responses.add(responses.POST, "https://x/api/2.0/sql/statements", json={
        "statement_id": "01ef", "status": {"state": "SUCCEEDED"},
        "manifest": {"schema": {"columns": [{"name": "customer_id"}, {"name": "company"}]}},
        "result": {"data_array": [["83728", "ACME"], ["83729", "Foo BV"]]},
    })
    client = databricks.DatabricksClient("https://x", Secret("tok"))
    resp = client.submit_statement("wh-1", "SELECT 1", [])
    assert databricks.is_terminal(resp["status"]["state"])
    assert databricks.statement_columns(resp) == ["customer_id", "company"]
    assert databricks.statement_rows(resp)[0][1] == "ACME"


# ---- workspace fs ----
def test_workspace_round_trip():
    # Workspaces ("notebooks") own no data of their own — just metadata pointing
    # at a source (sources.py). A workspace can exist with no primary source yet.
    m = workspace.create("Round Trip Test")
    read = workspace.read_manifest(m.workspace_id)
    assert read.workspace_id == m.workspace_id
    assert read.name == "Round Trip Test"
    assert read.primary_source_id is None
    root = workspace.workspaces_root() / m.workspace_id
    assert (root / "manifest.enc").exists()
    assert m.workspace_id in workspace.list_workspaces()

    m2 = workspace.create("With Source", primary_source_id="src-123")
    assert workspace.read_manifest(m2.workspace_id).primary_source_id == "src-123"

    workspace.delete_workspace(m.workspace_id)
    workspace.delete_workspace(m2.workspace_id)
    assert not root.exists()
    assert m.workspace_id not in workspace.list_workspaces()


# ---- auth service ----
def test_access_context_without_session():
    svc = AuthService(DatabricksConfig(workspace_url="https://x.cloud.databricks.com"), MemoryKeyStore())
    assert not svc.is_authenticated()
    with pytest.raises(NotAuthenticated):
        svc.access_context()


def test_logout_clears_refresh():
    ks = MemoryKeyStore()
    svc = AuthService(DatabricksConfig(workspace_url="https://x.cloud.databricks.com"), ks)
    ks.set("databricks-refresh", "rt")
    svc.logout()
    assert ks.get("databricks-refresh") is None
