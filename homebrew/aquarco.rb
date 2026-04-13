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

  # onedir layout: aquarco/aquarco is the PyInstaller entry point
  binary "aquarco/aquarco"

  # Runs before the old version is removed (upgrade) or before uninstall.
  # stop backs up then halts; destroy backs up (if running) then removes the VM.
  uninstall_preflight do
    aquarco = which("aquarco")
    if aquarco
      # stop auto-backs-up then halts; destroy auto-backs-up (if running) then removes VM
      system_command aquarco, args: ["stop"],
                     must_succeed: false, print_stdout: true, print_stderr: true
      system_command aquarco, args: ["destroy", "--yes"],
                     must_succeed: false, print_stdout: true, print_stderr: true
    end
  end

  # Only runs on: brew uninstall --zap aquarco
  # Destroys the VM and wipes all user data under ~/.aquarco.
  zap script:  { executable: which("aquarco"),
                 args: ["destroy", "--yes"],
                 must_succeed: false, print_stdout: true, print_stderr: true },
      trash: "~/.aquarco"

  postflight do
    # Strip Gatekeeper quarantine so macOS doesn't block the unsigned binary
    system_command "/usr/bin/xattr",
                   args: ["-dr", "com.apple.quarantine", staged_path.to_s]
    # Warm up the dyld shared cache so the first user invocation is fast
    system_command "#{staged_path}/aquarco/aquarco", args: ["--help"],
                   print_stdout: false, print_stderr: false

    # On upgrade a backup was created by uninstall_preflight — restore it.
    # On a fresh install there are no backups, so skip silently.
    backup_root = Pathname(Dir.home) / ".aquarco" / "backups"
    if backup_root.directory? && backup_root.children.any?(&:directory?)
      system_command "#{staged_path}/aquarco/aquarco",
                     args: ["init", "--from-backup", "latest"],
                     print_stdout: true, print_stderr: true
    end
  end
end
