"""PyJama Python backend — Databricks read path + governed local workspace.

Ported from the original Rust core (same API contracts) so the whole stack is
one language. Module map:

  config        non-secret runtime config (env)
  logging_setup structured logging + Secret redaction
  dbsql         identifier quoting + parameterized SELECT (injection boundary)
  pkce          PKCE for OAuth U2M
  keystore      OS credential store (+ in-memory fake)
  oauth         OAuth U2M flow (authorize/exchange/refresh/loopback)
  auth          AuthService (session + refresh)
  databricks    REST client (Unity Catalog / warehouses / statements)
  workspace     encrypted workspace filesystem + manifest
  server        FastAPI command surface
  main          pywebview desktop launcher
"""

__version__ = "0.1.0"
