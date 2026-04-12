# frozen_string_literal: true

# Homebrew formula for Aquarco CLI
#
# Installs the aquarco CLI tool for managing Aquarco VMs.
# The formula patches the build-type constant to "production" so that
# `aquarco update` is disabled — updates go through `brew upgrade` instead.
#
# On first `aquarco init`, the VM is provisioned with production Docker images
# tagged rc-1.0.0 from ghcr.io/borissuska/aquarco.

class Aquarco < Formula
  include Language::Python::Virtualenv

  desc "CLI for managing Aquarco autonomous agent VMs"
  homepage "https://github.com/aquarco/aquarco"
  url "https://github.com/aquarco/aquarco/archive/refs/tags/vrc-1.0.0.tar.gz"
  # SHA256 is stamped automatically by the release CI job (see .github/workflows/release.yml).
  # To compute manually: curl -sL <url> | shasum -a 256
  sha256 "PLACEHOLDER_SHA256"
  license "MIT"
  version "rc-1.0.0"

  depends_on "python@3.11"

  def install
    # Install the full source tree to share so Vagrant's synced_folder ("..")
    # from vagrant/Vagrantfile correctly resolves to this directory, giving the
    # VM access to docker/versions.env, supervisor/, config/, db/, etc.
    (share/"aquarco").install Dir["*"]

    # Patch build type to production — disables `aquarco update`
    inreplace "cli/src/aquarco_cli/_build.py",
              'BUILD_TYPE: str = "development"',
              'BUILD_TYPE: str = "production"'

    venv = virtualenv_create(libexec, "python3.11")
    venv.pip_install buildpath/"cli"

    # Wrap the binary to inject required env vars:
    # - AQUARCO_VAGRANT_DIR: tells the CLI where to find the installed Vagrantfile
    # - AQUARCO_DOCKER_MODE: tells provision.sh to write "production" to /etc/aquarco/env
    #   so the VM starts the stack using pre-built images from versions.env
    (bin/"aquarco").write <<~EOS
      #!/bin/bash
      export AQUARCO_VAGRANT_DIR="#{share}/aquarco/vagrant"
      export AQUARCO_DOCKER_MODE="production"
      exec "#{libexec}/bin/aquarco" "$@"
    EOS
    chmod 0555, bin/"aquarco"
  end

  def caveats
    <<~EOS
      Aquarco requires VirtualBox and Vagrant to manage VMs.
      Install them before running `aquarco init`:
        brew install --cask virtualbox
        brew install --cask vagrant
    EOS
  end

  test do
    # Verify the CLI starts and reports its version
    assert_match "rc-1.0.0", shell_output("#{bin}/aquarco --version")

    # Verify the production guard blocks `aquarco update`
    output = shell_output("#{bin}/aquarco update 2>&1", 1)
    assert_match "not available", output
  end
end
