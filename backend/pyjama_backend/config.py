"""Non-secret runtime configuration (from environment).

Safe to log and persist: workspace host, public OAuth client id, selected
warehouse. Secrets go through ``keystore``, never here.
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


class ConfigError(Exception):
    pass


def app_data_dir() -> Path:
    """OS per-user application-data directory for PyJama."""
    system = platform.system()
    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    elif system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "PyJama"


def config_path() -> Path:
    return app_data_dir() / "config.json"


@dataclass
class DatabricksConfig:
    workspace_url: str = ""
    # Built-in Databricks CLI public client id supports the U2M loopback flow.
    client_id: str = "databricks-cli"
    warehouse_id: str | None = None
    # UC Volume for commit staging, e.g. /Volumes/<catalog>/<schema>/<volume>
    staging_volume: str | None = None

    @classmethod
    def load(cls) -> "DatabricksConfig":
        """Load config: start from config.json (if present), then let any
        PYJAMA_* env vars override. Lets the packaged app run with no terminal,
        while env still works for development."""
        data: dict = {}
        path = config_path()
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                data = {}
        workspace_url = os.environ.get("PYJAMA_WORKSPACE_URL", data.get("workspace_url", ""))
        client_id = os.environ.get("PYJAMA_CLIENT_ID", data.get("client_id", "databricks-cli"))
        warehouse_id = os.environ.get("PYJAMA_WAREHOUSE_ID", data.get("warehouse_id") or "") or None
        staging_volume = os.environ.get("PYJAMA_STAGING_VOLUME", data.get("staging_volume") or "") or None
        return cls(workspace_url=workspace_url.rstrip("/"), client_id=client_id or "databricks-cli", warehouse_id=warehouse_id, staging_volume=staging_volume)

    # Backwards-compatible alias.
    from_env = load

    def save(self) -> None:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "workspace_url": self.workspace_url,
            "client_id": self.client_id,
            "warehouse_id": self.warehouse_id,
            "staging_volume": self.staging_volume,
        }, indent=2))

    def base_url(self) -> str:
        """Validate and return the workspace base URL (scheme+host, no slash)."""
        if not self.workspace_url:
            raise ConfigError("workspace URL is not configured (set PYJAMA_WORKSPACE_URL)")
        parsed = urlparse(self.workspace_url)
        if parsed.scheme != "https":
            raise ConfigError("workspace URL must use https")
        if not parsed.netloc:
            raise ConfigError("workspace URL is not a valid URL")
        return f"{parsed.scheme}://{parsed.netloc}"

    def is_configured(self) -> bool:
        try:
            self.base_url()
        except ConfigError:
            return False
        return bool(self.client_id)
