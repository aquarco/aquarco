"""aquarco init — bootstrap the Aquarco VM."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import typer

from aquarco_cli._build import BUILD_TYPE
from aquarco_cli.config import get_config, reset_config
from aquarco_cli.console import print_error, print_info, print_success
from aquarco_cli.health import print_health_table
from aquarco_cli.vagrant import VagrantHelper

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
    dev: bool = typer.Option(
        False, "--dev",
        help="Development mode: mount the aquarco source tree into the VM "
        "(sets AQUARCO_DEV=1). Equivalent to exporting that variable before running.",
    ),
) -> None:
    """One-command bootstrap of a working Aquarco environment."""
    if dev:
        import os
        os.environ["AQUARCO_DEV"] = "1"
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
            shutil.copy2(src_vagrant / "Vagrantfile", dst_vagrant / "Vagrantfile")
            src_scripts = src_vagrant / "scripts"
            if src_scripts.is_dir():
                dst_scripts = dst_vagrant / "scripts"
                if dst_scripts.exists():
                    shutil.rmtree(dst_scripts)
                shutil.copytree(src_scripts, dst_scripts)

    vagrant = VagrantHelper()
    print_info(f"Using Vagrantfile in {vagrant.vagrant_dir}")

    print_info("Starting VM with provisioning (this may take several minutes)...")
    try:
        vagrant.up(provision=True)
    except Exception as exc:
        print_error(f"vagrant up failed: {exc}")
        raise typer.Exit(code=1) from exc

    # 3. Health checks
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
