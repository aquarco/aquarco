# frozen_string_literal: true

# Homebrew cask for Aquarco CLI
#
# Installs the aquarco CLI tool for managing Aquarco VMs.
# VirtualBox and Vagrant are installed automatically as cask dependencies.
#
# On first `aquarco init`, the VM is provisioned with production Docker images
# tagged rc-1.0.0 from ghcr.io/borissuska/aquarco.

cask "aquarco" do
  version "1.0.0rc1"
  sha256 "PLACEHOLDER_SHA256"

  url "https://github.com/aquarco/aquarco/releases/download/PLACEHOLDER_TAG/aquarco-macos-arm64.tar.gz"
  name "Aquarco"
  desc "CLI for managing Aquarco autonomous agent VMs"
  homepage "https://github.com/aquarco/aquarco"

  depends_on cask: "virtualbox"
  depends_on cask: "vagrant"

  binary "aquarco"

  postflight do
    # Strip Gatekeeper quarantine so macOS doesn't block the unsigned binary
    system_command "/usr/bin/xattr",
                   args: ["-dr", "com.apple.quarantine", staged_path.to_s]
  end
end
