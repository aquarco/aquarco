"""Vagrant subprocess helpers — run on the host, target the VM."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Sequence

from aquarco_cli.config import get_config


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
    ) -> subprocess.CompletedProcess[str]:
        # Insert vm_name right after the subcommand verb (args[0])
        if self.vm_name and args:
            cmd = ["vagrant", args[0], self.vm_name, *args[1:]]
        else:
            cmd = ["vagrant", *args]

        kwargs: dict = {"cwd": str(self.vagrant_dir)}

        if stream:
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

    def ssh(self, command: str, *, stream: bool = False) -> subprocess.CompletedProcess[str]:
        """Run a command inside the VM via ``vagrant ssh -c``."""
        return self._run(["ssh", "-c", shlex.quote(command)], stream=stream, check=True)

    def is_running(self) -> bool:
        try:
            return self.status() == "running"
        except VagrantError:
            return False
