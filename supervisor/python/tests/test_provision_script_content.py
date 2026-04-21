"""Static analysis tests for provision.sh changes (b5c6d35).

Validates that the provision script:
  - Uses `systemctl restart` (not `start`) for supervisor and auth services
  - Copies scripts to /var/lib/aquarco/scripts/ for stable fallback
  - Sets correct ownership on copied scripts
  - Has both dev-mode and prod-mode source paths for script copy
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Locate provision.sh relative to the test file
PROVISION_SCRIPT = (
    Path(__file__).parent.parent.parent.parent / "vagrant" / "scripts" / "provision.sh"
)


@pytest.fixture
def provision_content() -> str:
    """Read the full provision.sh content."""
    assert PROVISION_SCRIPT.exists(), f"provision.sh not found at {PROVISION_SCRIPT}"
    return PROVISION_SCRIPT.read_text()


# ---------------------------------------------------------------------------
# systemctl restart (Fix 1)
# ---------------------------------------------------------------------------


class TestSystemctlRestart:
    """Verify 'start' was replaced with 'restart' for both services."""

    def test_supervisor_service_uses_restart(self, provision_content: str) -> None:
        """The supervisor service must use `systemctl restart` not `systemctl start`."""
        # Find the line that manages the supervisor service
        lines = provision_content.splitlines()
        for i, line in enumerate(lines):
            if "aquarco-supervisor-python.service" in line and "systemctl" in line:
                if "restart" in line or "enable" in line or "disable" in line or "daemon-reload" in line:
                    continue
                # If we find a `systemctl start` (not restart), fail
                if "systemctl start" in line:
                    pytest.fail(
                        f"Line {i+1}: supervisor service still uses 'systemctl start': {line.strip()}"
                    )

    def test_supervisor_service_restart_present(self, provision_content: str) -> None:
        """There must be at least one `systemctl restart aquarco-supervisor-python` line."""
        assert "systemctl restart aquarco-supervisor-python.service" in provision_content

    def test_auth_service_uses_restart(self, provision_content: str) -> None:
        """The auth helper service must use `systemctl restart` not `systemctl start`."""
        lines = provision_content.splitlines()
        for i, line in enumerate(lines):
            if "aquarco-claude-auth.service" in line and "systemctl" in line:
                if "restart" in line or "enable" in line or "disable" in line or "daemon-reload" in line:
                    continue
                if "systemctl start" in line:
                    pytest.fail(
                        f"Line {i+1}: auth service still uses 'systemctl start': {line.strip()}"
                    )

    def test_auth_service_restart_present(self, provision_content: str) -> None:
        """There must be at least one `systemctl restart aquarco-claude-auth` line."""
        assert "systemctl restart aquarco-claude-auth.service" in provision_content

    def test_no_systemctl_start_for_aquarco_services(self, provision_content: str) -> None:
        """No `systemctl start` should remain for any aquarco-* service.
        `systemctl start` is a no-op when the service is already running,
        so restart is always the correct verb for re-provision."""
        lines = provision_content.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "systemctl start" in stripped and "aquarco" in stripped:
                pytest.fail(
                    f"Line {i+1}: found 'systemctl start' for aquarco service: {stripped}"
                )


# ---------------------------------------------------------------------------
# Script copy to /var/lib/aquarco/scripts/ (Fix 2)
# ---------------------------------------------------------------------------


class TestScriptCopyBlock:
    """Verify the script-copy block in provision.sh."""

    def test_mkdir_stable_scripts_dir(self, provision_content: str) -> None:
        """provision.sh creates /var/lib/aquarco/scripts/ directory."""
        assert "mkdir -p /var/lib/aquarco/scripts" in provision_content

    def test_cp_scripts_to_stable_dir(self, provision_content: str) -> None:
        """Scripts are copied to /var/lib/aquarco/scripts/."""
        assert "/var/lib/aquarco/scripts/" in provision_content
        # Verify there's a cp command targeting this directory
        assert "cp " in provision_content
        lines = provision_content.splitlines()
        cp_to_scripts = [
            line for line in lines
            if "cp " in line and "/var/lib/aquarco/scripts/" in line
        ]
        assert len(cp_to_scripts) > 0, "Expected cp command to /var/lib/aquarco/scripts/"

    def test_dev_mode_scripts_source(self, provision_content: str) -> None:
        """In dev mode, scripts are sourced from the mounted repo."""
        assert "aquarco/supervisor/scripts" in provision_content

    def test_prod_mode_scripts_source(self, provision_content: str) -> None:
        """In prod mode, scripts are sourced from the pip package."""
        assert "aquarco-supervisor-python" in provision_content
        assert "aquarco_supervisor/scripts" in provision_content

    def test_chown_scripts_dir(self, provision_content: str) -> None:
        """Copied scripts must have correct ownership (agent user)."""
        lines = provision_content.splitlines()
        chown_lines = [
            line for line in lines
            if "chown" in line and "/var/lib/aquarco/scripts" in line
        ]
        assert len(chown_lines) > 0, (
            "Expected chown command for /var/lib/aquarco/scripts/"
        )

    def test_scripts_copy_has_log_message(self, provision_content: str) -> None:
        """A log message confirms scripts were copied."""
        assert "Supervisor scripts copied to /var/lib/aquarco/scripts/" in provision_content

    def test_scripts_copy_handles_missing_source(self, provision_content: str) -> None:
        """When the source directory doesn't exist, a warning is logged."""
        assert "scripts source not found" in provision_content or \
               "may be empty" in provision_content


# ---------------------------------------------------------------------------
# Script ordering: copy happens after pip install, before venv lock
# ---------------------------------------------------------------------------


class TestScriptCopyOrdering:
    """The script copy must happen after pip install but before venv lock."""

    def test_copy_after_pip_install(self, provision_content: str) -> None:
        """The mkdir -p /var/lib/aquarco/scripts line comes after pip install."""
        pip_pos = provision_content.find("pip install")
        mkdir_pos = provision_content.find("mkdir -p /var/lib/aquarco/scripts")
        assert pip_pos > 0, "pip install not found"
        assert mkdir_pos > 0, "mkdir for scripts not found"
        assert mkdir_pos > pip_pos, "Script copy must come after pip install"

    def test_copy_before_venv_lock(self, provision_content: str) -> None:
        """The script copy must happen before the venv is locked read-only."""
        mkdir_pos = provision_content.find("mkdir -p /var/lib/aquarco/scripts")
        lock_pos = provision_content.find("chmod -R a-w")
        assert mkdir_pos > 0, "mkdir for scripts not found"
        assert lock_pos > 0, "venv lock not found"
        assert mkdir_pos < lock_pos, "Script copy must happen before venv lock"
