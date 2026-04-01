"""CLI configuration — resolved from environment variables and defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CliConfig:
    """Runtime configuration for the Aquarco CLI."""

    # GraphQL endpoint (Caddy reverse-proxy)
    api_url: str = field(
        default_factory=lambda: os.environ.get(
            "AQUARCO_API_URL", "http://localhost:8080/api/graphql"
        )
    )

    # Vagrant working directory (containing the Vagrantfile)
    vagrant_dir: str = field(
        default_factory=lambda: os.environ.get("AQUARCO_VAGRANT_DIR", "")
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

    def resolve_vagrant_dir(self) -> Path:
        """Return the path to the directory containing the Vagrantfile.

        Resolution order:
        1. Explicit AQUARCO_VAGRANT_DIR env var
        2. ``vagrant/`` subdirectory of the repo root (auto-detected, up to
           :attr:`_MAX_PARENT_DEPTH` levels)
        3. Current working directory as last resort
        """
        if self.vagrant_dir:
            return Path(self.vagrant_dir).resolve()

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
