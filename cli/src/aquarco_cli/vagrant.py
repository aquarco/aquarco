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
#: Use only for non-compose commands (e.g. supervisor CLI). For ``docker compose``
#: invocations prefer ``COMPOSE_ENV_FLAGS`` which passes secrets directly via
#: ``--env-file`` and does not rely on inherited shell environment.
LOAD_SECRETS = "set -a; . /etc/aquarco/docker-secrets.env; . /home/agent/aquarco/docker/versions.env; set +a"

#: ``docker compose --env-file`` flags that pass secrets and version pins directly
#: to compose without relying on shell environment inheritance. Provision.sh runs
#: ``usermod -aG docker agent`` so the agent user can invoke ``docker`` directly
#: (no inner sudo needed). Using ``--env-file`` keeps secrets out of the process
#: environment and survives any wrapper that resets env vars (e.g. sudo).
COMPOSE_ENV_FLAGS = (
    "--env-file /etc/aquarco/docker-secrets.env"
    " --env-file /home/agent/aquarco/docker/versions.env"
)

#: Shell snippet that exports the supervisor's host-side secrets (DATABASE_URL
#: pointing to localhost, API keys, etc.).  Used by ``aquarco config`` and any
#: command that invokes the supervisor CLI directly on the VM.
LOAD_SUPERVISOR_SECRETS = "set -a; . /etc/aquarco/secrets.env; set +a"


class VagrantError(Exception):
    """Raised when a vagrant command exits non-zero."""


def get_postgres_version_mismatch(vagrant: "VagrantHelper") -> tuple[str, str] | None:
    """Return (data_version, configured_version) when the pgdata volume's PostgreSQL
    major version differs from the version configured in compose files.

    Returns None if versions match or if either version cannot be determined.

    data_version       — major version read from the pgdata volume's PG_VERSION file
    configured_version — major version from versions.env (prod) or compose.yml (dev)

    This is a safety pre-flight check for ``aquarco update``. Starting a new
    PostgreSQL major-version image against an existing data directory (e.g. pg16
    data with a pg18 image) causes the container to refuse to start and can leave
    the cluster in an inconsistent state. When a mismatch is detected, ``aquarco
    update`` blocks and instructs the user to use the safe upgrade path:
    ``aquarco destroy && aquarco init && aquarco restore``.

    The check is intentionally non-fatal on SSH or Docker errors (returns None) so
    that a missing volume or an unreachable Docker daemon never blocks unrelated
    update steps. The worst outcome of a false-negative is the same container
    startup failure that would have happened anyway.
    """
    try:
        # Read PG_VERSION from the named Docker volume.
        # This runs via `vagrant ssh -c` as the vagrant user (not agent), and the
        # vagrant user is not in the docker group, so `sudo docker` is required.
        # provision.sh grants vagrant NOPASSWD sudo for /usr/bin/docker.
        r = vagrant.ssh(
            "sudo docker run --rm -v aquarco_pgdata:/pgdata:ro alpine "
            "sh -c 'cat /pgdata/PG_VERSION 2>/dev/null || true'",
            stream=False,
        )
        data_ver = (r.stdout or "").strip()
        # PG_VERSION contains only the major version integer (e.g. "16", not "16.2").
        # PostgreSQL has used this convention since version 10.  If this ever
        # changes, isdigit() will need updating to handle dotted versions.
        if not data_ver or not data_ver.isdigit():
            return None

        # Configured version: prefer versions.env (prod), fall back to compose.yml (dev).
        # versions.env is the single source of truth for production image tags.
        # compose.yml hard-codes the image tag for dev (no versions.env there).
        # We use an explicit `if` instead of `||` because `cut` always exits 0
        # even on empty input, which would make the fallback branch unreachable.
        r = vagrant.ssh(
            "if ver=$(grep -E '^AQUARCO_POSTGRES_VERSION=' "
            "/home/agent/aquarco/docker/versions.env 2>/dev/null | cut -d= -f2) "
            "&& [ -n \"$ver\" ]; then echo \"$ver\"; else "
            "grep -oP 'image: postgres:\\K[0-9]+' "
            "/home/agent/aquarco/docker/compose.yml 2>/dev/null | head -1; fi",
            stream=False,
        )
        raw = (r.stdout or "").strip()
        # Strip Alpine tag suffix so "18-alpine" and "18" both yield "18".
        conf_ver = raw.split("-")[0].strip()
        if not conf_ver or not conf_ver.isdigit():
            return None

        return (data_ver, conf_ver) if data_ver != conf_ver else None
    except Exception:
        # Non-fatal: SSH failure, no Docker, or volume not yet created.
        # Callers treat None as "no mismatch detected" and proceed normally.
        return None


def get_compose_prefix(vagrant: "VagrantHelper") -> str:
    """Return the docker compose command prefix appropriate for the VM's environment.

    Production VMs use pre-built registry images via ``compose.prod.yml``.
    Dev VMs build from the source tree via the default ``compose.yml``.

    The returned string calls ``docker`` directly (no ``sudo``) because
    ``provision.sh`` adds the ``agent`` user to the ``docker`` group
    (``usermod -aG docker agent``), so the socket is reachable without elevation.
    The prefix appends ``COMPOSE_ENV_FLAGS`` (two ``--env-file`` flags) so that
    secrets from ``/etc/aquarco/docker-secrets.env`` and version pins from
    ``versions.env`` are read directly by compose rather than inherited through
    the shell — this survives any wrapper that resets the process environment
    (such as ``sudo -u agent``, which CLI commands still use for file ownership
    reasons even though docker itself no longer needs sudo).
    """
    try:
        result = vagrant.ssh("sudo cat /etc/aquarco/env 2>/dev/null || echo development")
        env = (result.stdout or "").strip()
    except Exception:
        env = "development"
    base = "docker compose -f compose.prod.yml" if env == "production" else "docker compose"
    return f"{base} {COMPOSE_ENV_FLAGS}"


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
