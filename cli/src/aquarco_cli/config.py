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

    @property
    def _vagrant_subdir(self) -> str:
        """Return 'dev' when AQUARCO_VM_NAME contains 'dev', else 'prod'."""
        return "dev" if "dev" in self.vm_name else "prod"

    def resolve_vagrant_dir(self) -> Path:
        """Return the path to the directory containing the Vagrantfile.

        Resolution order:
        1. Explicit AQUARCO_VAGRANT_DIR env var
        2. Production install: ``~/.aquarco/vagrant/`` (stable across brew reinstalls)
        3. ``vagrant/<subdir>/`` subdirectory of the repo root (auto-detected, up to
           :attr:`_MAX_PARENT_DEPTH` levels), where subdir is 'dev' or 'prod' based
           on AQUARCO_VM_NAME
        4. Current working directory as last resort
        """
        if self.vagrant_dir:
            return Path(self.vagrant_dir).resolve()

        subdir = self._vagrant_subdir

        # Production install (onedir layout): binary is at <install>/aquarco/aquarco
        # Prefer ~/.aquarco/vagrant/ — a stable location that survives brew reinstalls.
        # Fall back to the install-local copy only before the first `aquarco init`.
        if BUILD_TYPE == "production":
            home_vagrant = Path.home() / ".aquarco" / "vagrant"
            if (home_vagrant / "Vagrantfile").exists():
                return home_vagrant.resolve()
            install_root = Path(sys.executable).parent.parent
            candidate = install_root / "vagrant" / subdir / "Vagrantfile"
            if candidate.exists():
                return (install_root / "vagrant" / subdir).resolve()

        # Walk up from cwd looking for vagrant/<subdir>/Vagrantfile (bounded)
        current = Path.cwd()
        ancestors = [current, *current.parents]
        for parent in ancestors[: self._MAX_PARENT_DEPTH]:
            candidate = parent / "vagrant" / subdir / "Vagrantfile"
            if candidate.exists():
                return (parent / "vagrant" / subdir).resolve()
            # Also check if Vagrantfile is directly in the directory
            if (parent / "Vagrantfile").exists():
                return parent.resolve()

        # For editable dev installs the package lives inside the repo tree.
        # Walk up from __file__ so the vagrant dir is found even when the user
        # runs aquarco from outside the project directory (e.g. from ~).
        pkg_dir = Path(__file__).resolve().parent
        for ancestor in [pkg_dir, *pkg_dir.parents[: self._MAX_PARENT_DEPTH]]:
            candidate = ancestor / "vagrant" / subdir / "Vagrantfile"
            if candidate.exists():
                return (ancestor / "vagrant" / subdir).resolve()

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
