"""Vagrant subprocess helpers — run on the host, target the VM."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence

from aquarco_cli._build import BUILD_TYPE
from aquarco_cli.config import get_config


# ---------------------------------------------------------------------------
# Shared VM / Docker constants used across CLI commands
# ---------------------------------------------------------------------------

#: Working directory for docker compose inside the VM.
COMPOSE_DIR = "/home/agent/aquarco/docker"

#: Shell snippet that exports secrets from the provisioned env file.
#: Required before any ``docker compose`` invocation because compose.yml
#: declares POSTGRES_PASSWORD and DATABASE_URL as required variables.
LOAD_SECRETS = "set -a; . /etc/aquarco/docker-secrets.env; set +a"

#: Shell snippet that exports the supervisor's host-side secrets (DATABASE_URL
#: pointing to localhost, API keys, etc.).  Used by ``aquarco config`` and any
#: command that invokes the supervisor CLI directly on the VM.
LOAD_SUPERVISOR_SECRETS = "set -a; . /etc/aquarco/secrets.env; set +a"


class VagrantError(Exception):
    """Raised when a vagrant command exits non-zero."""


class VagrantHelper:
    """Convenience wrapper around the ``vagrant`` CLI."""

    def __init__(self, vagrant_dir: Path | None = None, vm_name: str = "") -> None:
        _cfg = get_config()
        self.vagrant_dir = vagrant_dir or _cfg.resolve_vagrant_dir()
        self.vm_name = vm_name or _cfg.vm_name

    # ------------------------------------------------------------------
    # Low-level
    # ------------------------------------------------------------------

    def _run(
        self,
        args: Sequence[str],
        *,
        stream: bool = False,
        check: bool = True,
        capture: bool = True,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        # Insert vm_name right after the subcommand verb (args[0])
        if self.vm_name and args:
            cmd = ["vagrant", args[0], self.vm_name, *args[1:]]
        else:
            cmd = ["vagrant", *args]

        env = {**os.environ, "AQUARCO_PORT": str(get_config().port)}
        if BUILD_TYPE == "production":
            env.setdefault("AQUARCO_DOCKER_MODE", "production")
        kwargs: dict = {"cwd": str(self.vagrant_dir), "env": env}

        if input is not None:
            result = subprocess.run(
                cmd, input=input, capture_output=True, text=True, **kwargs  # noqa: S603
            )
        elif stream:
            # Stream stdout/stderr to the terminal in real time
            result = subprocess.run(cmd, **kwargs)  # noqa: S603
        elif capture:
            result = subprocess.run(
                cmd, capture_output=True, text=True, **kwargs  # noqa: S603
            )
        else:
            result = subprocess.run(cmd, **kwargs)  # noqa: S603

        if check and result.returncode != 0:
            stderr = getattr(result, "stderr", "") or ""
            raise VagrantError(
                f"vagrant {' '.join(args)} failed (rc={result.returncode}): {stderr.strip()}"
            )
        return result

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def status(self) -> str:
        """Return the VM status string (e.g. 'running', 'poweroff')."""
        result = self._run(["status", "--machine-readable"])
        for line in result.stdout.splitlines():
            parts = line.split(",")
            if len(parts) >= 4 and parts[2] == "state":
                return parts[3]
        return "unknown"

    def up(self, *, provision: bool = False) -> None:
        """Bring the VM up, optionally with provisioning.  Streams output."""
        args = ["up"]
        if provision:
            args.append("--provision")
        self._run(args, stream=True)

    def halt(self) -> None:
        self._run(["halt"], stream=True)

    def provision(self) -> None:
        """Re-provision the VM.  Streams output."""
        self._run(["provision"], stream=True)

    def ssh(self, command: str, *, stream: bool = False, input: str | None = None) -> subprocess.CompletedProcess[str]:
        """Run a command inside the VM via ``vagrant ssh -c``.

        .. warning::
            ``command`` is passed directly to the remote shell so that callers
            can use shell operators (``&&``, ``|``, ``;``).  Callers **must not**
            pass unsanitised user input — all current call-sites use hardcoded
            command strings.
        """
        return self._run(["ssh", "-c", command], stream=stream, check=True, input=input)

    def destroy(self) -> None:
        """Destroy the VM. Streams output."""
        self._run(["destroy", "--force"], stream=True)

    def is_running(self) -> bool:
        try:
            return self.status() == "running"
        except VagrantError:
            return False
