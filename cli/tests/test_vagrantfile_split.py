"""Tests for the Vagrantfile prod/dev split and path-detection fix.

Validates:
- The prod Vagrantfile detects its deploy context (in-repo vs flat production install)
- The dev Vagrantfile uses fixed relative paths (always in-repo)
- Structural differences between prod and dev are correct
- init.py copies the prod Vagrantfile to a location consistent with the fallback path logic
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROD_VAGRANTFILE = REPO_ROOT / "vagrant" / "prod" / "Vagrantfile"
DEV_VAGRANTFILE = REPO_ROOT / "vagrant" / "dev" / "Vagrantfile"


@pytest.fixture
def prod_text() -> str:
    return PROD_VAGRANTFILE.read_text()


@pytest.fixture
def dev_text() -> str:
    return DEV_VAGRANTFILE.read_text()


class TestProdVagrantfileContextDetection:
    """The prod Vagrantfile must detect whether it runs from the repo or production."""

    def test_checks_basename_for_prod(self, prod_text: str) -> None:
        """Must check File.basename(__dir__) == 'prod' to detect in-repo context."""
        assert 'File.basename(__dir__) == "prod"' in prod_text

    def test_in_repo_repo_root_two_levels_up(self, prod_text: str) -> None:
        """When in-repo (vagrant/prod/), REPO_ROOT goes two levels up."""
        assert 'File.expand_path("../..", __dir__)' in prod_text

    def test_in_repo_scripts_dir_sibling(self, prod_text: str) -> None:
        """When in-repo, SCRIPTS_DIR is ../scripts (sibling of prod/)."""
        assert 'File.expand_path("../scripts", __dir__)' in prod_text

    def test_production_repo_root_one_level_up(self, prod_text: str) -> None:
        """When deployed flat (~/.aquarco/vagrant/), REPO_ROOT is one level up."""
        assert 'File.expand_path("..", __dir__)' in prod_text

    def test_production_scripts_dir_local(self, prod_text: str) -> None:
        """When deployed flat, SCRIPTS_DIR is scripts/ under __dir__."""
        assert 'File.expand_path("scripts", __dir__)' in prod_text

    def test_has_else_branch(self, prod_text: str) -> None:
        """The if/else must be present for both code paths."""
        assert re.search(r'^if\s+File\.basename', prod_text, re.MULTILINE)
        assert re.search(r'^else\b', prod_text, re.MULTILINE)
        assert re.search(r'^end\b', prod_text, re.MULTILINE)


class TestDevVagrantfileNoBranchDetection:
    """The dev Vagrantfile must NOT have context detection — always runs from repo."""

    def test_no_basename_check(self, dev_text: str) -> None:
        """Dev Vagrantfile should not check File.basename."""
        assert "File.basename" not in dev_text

    def test_fixed_repo_root(self, dev_text: str) -> None:
        """Dev Vagrantfile uses fixed two-levels-up REPO_ROOT."""
        assert 'REPO_ROOT   = File.expand_path("../..", __dir__)' in dev_text

    def test_fixed_scripts_dir(self, dev_text: str) -> None:
        """Dev Vagrantfile uses fixed ../scripts SCRIPTS_DIR."""
        assert 'SCRIPTS_DIR = File.expand_path("../scripts", __dir__)' in dev_text


class TestProdDevStructuralDifferences:
    """Validate the intentional differences between prod and dev Vagrantfiles."""

    def test_prod_autostart_true(self, prod_text: str) -> None:
        assert re.search(r'autostart:\s*true', prod_text)

    def test_dev_autostart_false(self, dev_text: str) -> None:
        assert re.search(r'autostart:\s*false', dev_text)

    def test_prod_machine_name_aquarco(self, prod_text: str) -> None:
        assert '"aquarco"' in prod_text
        assert 'hostname = "aquarco"' in prod_text

    def test_dev_machine_name_aquarco_dev(self, dev_text: str) -> None:
        assert '"aquarco-dev"' in dev_text
        assert 'hostname = "aquarco-dev"' in dev_text

    def test_dev_has_synced_folder(self, dev_text: str) -> None:
        """Dev mounts the source tree; prod should not."""
        assert "synced_folder REPO_ROOT" in dev_text

    def test_prod_no_source_sync(self, prod_text: str) -> None:
        """Prod should NOT mount source tree — it uploads via file provisioner."""
        assert "synced_folder REPO_ROOT" not in prod_text

    def test_dev_mode_flag_prod(self, prod_text: str) -> None:
        """Prod sets DEV_MODE=0."""
        assert '"DEV_MODE"' in prod_text
        assert '"0"' in prod_text

    def test_dev_mode_flag_dev(self, dev_text: str) -> None:
        """Dev sets DEV_MODE=1."""
        assert '"DEV_MODE"' in dev_text
        assert '"1"' in dev_text

    def test_both_use_provision_sh(self, prod_text: str, dev_text: str) -> None:
        """Both Vagrantfiles must run provision.sh from SCRIPTS_DIR."""
        assert "SCRIPTS_DIR}/provision.sh" in prod_text
        assert "SCRIPTS_DIR}/provision.sh" in dev_text

    def test_both_forward_same_ports(self, prod_text: str, dev_text: str) -> None:
        """Both Vagrantfiles forward proxy (8080) and postgres (15432)."""
        for text in (prod_text, dev_text):
            assert "guest: 8080" in text
            assert "host: 15432" in text


class TestProdVagrantfileProvisionerPaths:
    """All file provisioner sources must use REPO_ROOT or SCRIPTS_DIR variables."""

    def test_docker_uses_repo_root(self, prod_text: str) -> None:
        assert '#{REPO_ROOT}/docker' in prod_text

    def test_supervisor_python_uses_repo_root(self, prod_text: str) -> None:
        assert '#{REPO_ROOT}/supervisor/python' in prod_text

    def test_supervisor_yaml_uses_repo_root(self, prod_text: str) -> None:
        assert '#{REPO_ROOT}/supervisor/config/supervisor.yaml' in prod_text

    def test_config_uses_repo_root(self, prod_text: str) -> None:
        assert '#{REPO_ROOT}/config' in prod_text

    def test_network_tracking_uses_scripts_dir(self, prod_text: str) -> None:
        assert '#{SCRIPTS_DIR}/setup-network-tracking.sh' in prod_text


class TestInitCopyPathConsistency:
    """init.py must copy the prod Vagrantfile to ~/.aquarco/vagrant/Vagrantfile,
    consistent with the Vagrantfile's else-branch (non-'prod' basename) path logic."""

    def test_init_copies_from_prod_subdir(self) -> None:
        """init.py must reference vagrant/prod/Vagrantfile as the source."""
        init_path = REPO_ROOT / "cli" / "src" / "aquarco_cli" / "commands" / "init.py"
        init_text = init_path.read_text()
        assert '"prod"' in init_text or "'prod'" in init_text
        assert "Vagrantfile" in init_text

    def test_init_copies_to_flat_vagrant_dir(self) -> None:
        """init.py copies to ~/.aquarco/vagrant/Vagrantfile (flat, not vagrant/prod/)."""
        init_path = REPO_ROOT / "cli" / "src" / "aquarco_cli" / "commands" / "init.py"
        init_text = init_path.read_text()
        # The destination is dst_vagrant / "Vagrantfile" where dst_vagrant = ~/.aquarco/vagrant
        assert 'dst_vagrant / "Vagrantfile"' in init_text

    def test_init_copies_scripts_alongside(self) -> None:
        """init.py must also copy scripts/ into the vagrant dir."""
        init_path = REPO_ROOT / "cli" / "src" / "aquarco_cli" / "commands" / "init.py"
        init_text = init_path.read_text()
        assert "scripts" in init_text

    def test_config_resolves_home_vagrant_first(self) -> None:
        """In production, config.py checks ~/.aquarco/vagrant/Vagrantfile first."""
        config_path = REPO_ROOT / "cli" / "src" / "aquarco_cli" / "config.py"
        config_text = config_path.read_text()
        assert '".aquarco"' in config_text or '"vagrant"' in config_text
        assert "home_vagrant" in config_text
