"""aquarco init — bootstrap the Aquarco VM."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Optional

import typer

from aquarco_cli._build import BUILD_TYPE
from aquarco_cli.commands.backup import perform_backup
from aquarco_cli.commands.restore import (
    DEFAULT_BACKUP_ROOT,
    restore_credentials,
    restore_db,
    run_migrations,
)
from aquarco_cli.config import get_config, reset_config
from aquarco_cli.console import print_error, print_info, print_success, print_warning
from aquarco_cli.health import print_health_table
from aquarco_cli.vagrant import VagrantError, VagrantHelper

app = typer.Typer(context_settings={"help_option_names": ["-h", "--help"]})


def _check_prerequisite(binary: str, display_name: str, install_url: str) -> bool:
    if shutil.which(binary) is None:
        print_error(
            f"{display_name} not found. Install from {install_url}"
        )
        return False
    return True


@app.callback(invoke_without_command=True)
def init(
    ctx: typer.Context,
    port: int = typer.Option(
        8080, "--port",
        help="Host port for the Caddy reverse proxy (default: 8080). "
        "Saved to ~/.aquarco.json for future commands.",
    ),
    from_backup: Optional[str] = typer.Option(
        None, "--from-backup",
        metavar="BACKUP_DIR|latest",
        help="After provisioning, restore from a backup. "
             "Use 'latest' to pick the most recent backup in ~/.aquarco/backups/, "
             "or supply a path to a specific backup directory.",
    ),
) -> None:
    """One-command bootstrap of a working Aquarco environment."""
    # Save port configuration when --port is explicitly provided or config already exists
    config_file = Path.home() / ".aquarco.json"
    # Click's get_parameter_source returns ParameterSource.COMMANDLINE when user passed --port
    import click
    port_source = ctx.get_parameter_source("port")
    port_explicitly_set = port_source is not None and port_source == click.core.ParameterSource.COMMANDLINE
    if port_explicitly_set or config_file.exists():
        try:
            existing = {}
            if config_file.exists():
                existing = json.loads(config_file.read_text())
            existing["port"] = port
            config_file.write_text(json.dumps(existing, indent=2) + "\n")
            reset_config()  # reload with new port
        except OSError as exc:
            print_error(f"Failed to save port configuration: {exc}")

    # 1. Check prerequisites
    ok = True
    ok = _check_prerequisite(
        "VBoxManage", "VirtualBox", "https://www.virtualbox.org/"
    ) and ok
    ok = _check_prerequisite(
        "vagrant", "Vagrant", "https://www.vagrantup.com/"
    ) and ok
    if not ok:
        raise typer.Exit(code=1)

    # 2. In production, sync Vagrantfile + scripts to ~/.aquarco/vagrant/ so that
    #    the working directory survives brew reinstalls (the .vagrant/ state dir
    #    lives there and must not move when the Caskroom path changes).
    if BUILD_TYPE == "production":
        install_root = Path(sys.executable).parent.parent
        src_vagrant = install_root / "vagrant"
        dst_vagrant = Path.home() / ".aquarco" / "vagrant"
        if src_vagrant.is_dir():
            dst_vagrant.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_vagrant / "prod" / "Vagrantfile", dst_vagrant / "Vagrantfile")
            src_scripts = src_vagrant / "scripts"
            if src_scripts.is_dir():
                dst_scripts = dst_vagrant / "scripts"
                if dst_scripts.exists():
                    shutil.rmtree(dst_scripts)
                shutil.copytree(src_scripts, dst_scripts)

        # Copy docker compose files so the Vagrantfile file provisioner can
        # upload them to the VM (REPO_ROOT/docker is resolved in Vagrantfile via __dir__)
        src_docker = install_root / "docker"
        dst_docker = Path.home() / ".aquarco" / "docker"
        if src_docker.is_dir():
            if dst_docker.exists():
                shutil.rmtree(dst_docker)
            shutil.copytree(src_docker, dst_docker)

        # Copy supervisor Python package so the Vagrantfile file provisioner can
        # upload it to the VM (REPO_ROOT/supervisor/python is resolved in Vagrantfile via __dir__)
        src_supervisor = install_root / "supervisor" / "python"
        dst_supervisor = Path.home() / ".aquarco" / "supervisor" / "python"
        if src_supervisor.is_dir():
            if dst_supervisor.exists():
                shutil.rmtree(dst_supervisor)
            shutil.copytree(src_supervisor, dst_supervisor)

        # Copy supervisor config so provision.sh can install it to /etc/aquarco/
        src_supervisor_config = install_root / "supervisor" / "config" / "supervisor.yaml"
        dst_supervisor_config = Path.home() / ".aquarco" / "supervisor" / "config" / "supervisor.yaml"
        if src_supervisor_config.exists():
            dst_supervisor_config.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_supervisor_config, dst_supervisor_config)

        # Copy agent/pipeline config so the Vagrantfile file provisioner can
        # upload it to the VM (REPO_ROOT/config is resolved in Vagrantfile via __dir__)
        src_config = install_root / "config"
        dst_config = Path.home() / ".aquarco" / "config"
        if src_config.is_dir():
            if dst_config.exists():
                shutil.rmtree(dst_config)
            shutil.copytree(src_config, dst_config,
                            ignore=shutil.ignore_patterns("._*"))

    vagrant = VagrantHelper()
    print_info(f"Using Vagrantfile in {vagrant.vagrant_dir}")

    # Back up before (re-)provisioning an already-running VM so no data is lost
    if vagrant.is_running():
        print_info("VM is already running — backing up before re-provisioning...")
        perform_backup(vagrant)

    print_info("Starting VM with provisioning (this may take several minutes)...")
    try:
        vagrant.up(provision=True)
    except Exception as exc:
        print_error(f"vagrant up failed: {exc}")
        raise typer.Exit(code=1) from exc

    # 3. Restore from backup (if requested)
    if from_backup is not None:
        from aquarco_cli.commands.restore import latest_backup

        if from_backup == "latest":
            backup_dir = latest_backup(DEFAULT_BACKUP_ROOT)
            if backup_dir is None:
                print_error(f"No backups found in {DEFAULT_BACKUP_ROOT}")
                raise typer.Exit(code=1)
            print_info(f"Restoring from latest backup: {backup_dir}")
        else:
            backup_dir = Path(from_backup)
            if not backup_dir.is_dir():
                print_error(f"Backup directory not found: {backup_dir}")
                raise typer.Exit(code=1)
            print_info(f"Restoring from backup: {backup_dir}")

        print_info("Restoring credentials...")
        ok = restore_credentials(vagrant, backup_dir)
        print_info("Restoring database...")
        ok = ok and restore_db(vagrant, backup_dir)
        if ok:
            print_info("Running migrations...")
            ok = run_migrations(vagrant) and ok
        if not ok:
            print_error("Backup restore completed with errors (see above).")
            raise typer.Exit(code=1)

    # 4. Health checks
    print_info("Checking stack health...")
    all_healthy = print_health_table()

    if all_healthy:
        print_success("Aquarco initialized successfully!")
    else:
        print_error(
            "Some services are not healthy. Check the table above and try "
            "'aquarco status' after a few seconds."
        )
        raise typer.Exit(code=1)
