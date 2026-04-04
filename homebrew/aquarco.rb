# frozen_string_literal: true

# Homebrew formula for Aquarco CLI
#
# Installs the aquarco CLI tool for managing Aquarco VMs.
# The formula patches the build-type constant to "production" so that
# `aquarco update` is disabled — updates go through `brew upgrade` instead.

class Aquarco < Formula
  include Language::Python::Virtualenv

  desc "CLI for managing Aquarco autonomous agent VMs"
  homepage "https://github.com/borissuska/aquarco"
  url "https://github.com/borissuska/aquarco/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "PLACEHOLDER_SHA256"
  license "MIT"

  depends_on "python@3.11"
  depends_on cask: "vagrant"
  depends_on cask: "virtualbox"

  def install
    # Patch build type to production — disables `aquarco update`
    inreplace "cli/src/aquarco_cli/_build.py",
              'BUILD_TYPE: str = "development"',
              'BUILD_TYPE: str = "production"'

    venv = virtualenv_create(libexec, "python3.11")
    venv.pip_install_and_link buildpath / "cli"
  end

  test do
    # Verify the CLI starts and reports its version
    assert_match version.to_s, shell_output("#{bin}/aquarco --version")

    # Verify the production guard blocks `aquarco update`
    output = shell_output("#{bin}/aquarco update 2>&1", 1)
    assert_match "not available", output
  end
end
