"""aquarco install — bootstrap the Aquarco VM."""

from __future__ import annotations

import shutil

import typer

from aquarco_cli.console import print_error, print_info, print_success
from aquarco_cli.health import print_health_table
from aquarco_cli.vagrant import VagrantHelper

app = typer.Typer()


def _check_prerequisite(binary: str, display_name: str, install_url: str) -> bool:
    if shutil.which(binary) is None:
        print_error(
            f"{display_name} not found. Install from {install_url}"
        )
        return False
    return True


@app.callback(invoke_without_command=True)
def install() -> None:
    """One-command bootstrap of a working Aquarco environment."""
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

    # 2. Locate Vagrantfile and bring VM up
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
        print_success("Aquarco installed successfully!")
    else:
        print_error(
            "Some services are not healthy. Check the table above and try "
            "'aquarco status' after a few seconds."
        )
        raise typer.Exit(code=1)
