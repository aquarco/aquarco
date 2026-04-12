"""CLI configuration — resolved from environment variables and defaults."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from aquarco_cli._build import BUILD_TYPE


def _load_saved_port() -> int:
    """Read port from ~/.aquarco.json if it exists."""
    config_file = Path.home() / ".aquarco.json"
    if config_file.exists():
        try:
            data = json.loads(config_file.read_text())
            return int(data.get("port", 8080))
        except (json.JSONDecodeError, ValueError, OSError):
            pass
    return 8080


@dataclass
class CliConfig:
    """Runtime configuration for the Aquarco CLI."""

    # Port used for the Caddy reverse proxy
    port: int = field(default_factory=_load_saved_port)

    # GraphQL endpoint (Caddy reverse-proxy)
    api_url: str = field(default="")

    # Vagrant working directory (containing the Vagrantfile)
    vagrant_dir: str = field(
        default_factory=lambda: os.environ.get("AQUARCO_VAGRANT_DIR", "")
    )

    # Internal API key (for mutations like setDrainMode that require auth)
    api_key: str = field(
        default_factory=lambda: os.environ.get("AQUARCO_INTERNAL_API_KEY", "")
    )

    # HTTP timeout in seconds
    http_timeout: float = field(
        default_factory=lambda: float(os.environ.get("AQUARCO_HTTP_TIMEOUT", "30"))
    )

    # VM name used by vagrant commands
    vm_name: str = field(
        default_factory=lambda: os.environ.get("AQUARCO_VM_NAME", "")
    )

    _MAX_PARENT_DEPTH: int = 10

    def __post_init__(self) -> None:
        """Resolve api_url from env or port-based default."""
        if not self.api_url:
            env_url = os.environ.get("AQUARCO_API_URL", "")
            if env_url:
                self.api_url = env_url
            else:
                self.api_url = f"http://localhost:{self.port}/api/graphql"

    def resolve_vagrant_dir(self) -> Path:
        """Return the path to the directory containing the Vagrantfile.

        Resolution order:
        1. Explicit AQUARCO_VAGRANT_DIR env var
        2. Production install: ``vagrant/`` sibling of the binary's parent dir
        3. ``vagrant/`` subdirectory of the repo root (auto-detected, up to
           :attr:`_MAX_PARENT_DEPTH` levels)
        4. Current working directory as last resort
        """
        if self.vagrant_dir:
            return Path(self.vagrant_dir).resolve()

        # Production install (onedir layout): binary is at <install>/aquarco/aquarco
        # so vagrant/ lives at <install>/vagrant/
        if BUILD_TYPE == "production":
            install_root = Path(sys.executable).parent.parent
            candidate = install_root / "vagrant" / "Vagrantfile"
            if candidate.exists():
                return (install_root / "vagrant").resolve()

        # Walk up from cwd looking for vagrant/Vagrantfile (bounded)
        current = Path.cwd()
        ancestors = [current, *current.parents]
        for parent in ancestors[: self._MAX_PARENT_DEPTH]:
            candidate = parent / "vagrant" / "Vagrantfile"
            if candidate.exists():
                return (parent / "vagrant").resolve()
            # Also check if Vagrantfile is directly in the directory
            if (parent / "Vagrantfile").exists():
                return parent.resolve()

        return current


# Lazy singleton — reads env vars on first access, not at import time
_cfg: CliConfig | None = None


def get_config() -> CliConfig:
    """Return the singleton CliConfig, creating it lazily on first call."""
    global _cfg  # noqa: PLW0603
    if _cfg is None:
        _cfg = CliConfig()
    return _cfg


def reset_config() -> None:
    """Reset the singleton (useful for tests)."""
    global _cfg  # noqa: PLW0603
    _cfg = None
