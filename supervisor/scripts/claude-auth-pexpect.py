#!/usr/bin/env python3
"""
claude-auth-pexpect.py — drive `claude auth login` interactively via pty.

Uses low-level pty.fork() to properly interact with Ink's raw mode TUI.
"""

import json
import os
import pty
import re
import select
import signal
import subprocess
import sys
import time

IPC_DIR = sys.argv[1] if len(sys.argv) > 1 else "/var/lib/aquarco/claude-ipc"

def log(msg):
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(json.dumps({"ts": ts, "component": "claude-auth-pexpect", "msg": msg}),
          file=sys.stderr, flush=True)

def write_response(name, data):
    path = os.path.join(IPC_DIR, name)
    with open(path, "w") as f:
        json.dump(data, f)

def read_until(fd, timeout, pattern=None):
    """Read from fd until timeout or pattern found. Returns all output."""
    output = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = max(deadline - time.time(), 0.1)
        r, _, _ = select.select([fd], [], [], min(remaining, 0.5))
        if r:
            try:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                output += chunk
                if pattern:
                    text = output.decode("utf-8", errors="replace")
                    # Strip ANSI codes for matching
                    clean = re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', text)
                    if re.search(pattern, clean):
                        return output
            except OSError:
                break
    return output

def main():
    log("Starting claude auth login via pty.fork()")

    pid, master_fd = pty.fork()

    if pid == 0:
        # Child process — exec claude auth login
        os.execvp("claude", ["claude", "auth", "login"])
        sys.exit(1)

    # Parent process
    try:
        # Set terminal size
        import fcntl
        import struct
        import termios
        winsize = struct.pack("HHHH", 24, 200, 0, 0)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
    except Exception as e:
        log(f"Failed to set winsize: {e}")

    # Wait for URL
    log("Waiting for auth URL...")
    output = read_until(master_fd, 30, r"https://claude\.ai/oauth/authorize\S+")
    text = output.decode("utf-8", errors="replace")
    clean = re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', text)

    urls = re.findall(r'https://claude\.ai/oauth/authorize\S+', clean)
    if not urls:
        log(f"Failed to capture URL. Output: {repr(clean[:300])}")
        write_response("login-response", {"error": "Failed to capture auth URL"})
        os.kill(pid, signal.SIGTERM)
        os.waitpid(pid, 0)
        return

    url = urls[0].strip()
    log(f"Captured URL: {url[:80]}...")
    write_response("login-response", {"authorizeUrl": url})

    # Drain any remaining output
    time.sleep(1)
    read_until(master_fd, 2)

    log("Waiting for code-submit...")

    # Wait for user to submit the auth code
    code_file = os.path.join(IPC_DIR, "code-submit")
    deadline = time.time() + 600

    while time.time() < deadline:
        if os.path.exists(code_file):
            with open(code_file, "r") as f:
                auth_code = f.read().strip()
            os.unlink(code_file)

            if not auth_code:
                log("Empty code-submit file, ignoring")
                continue

            log(f"Received auth code ({len(auth_code)} chars)")

            # Write the code directly to the PTY master fd
            # This is what the terminal emulator does when you type/paste
            code_bytes = auth_code.encode("utf-8")

            # Send in small chunks with delays (simulates typing)
            chunk_size = 8
            for i in range(0, len(code_bytes), chunk_size):
                chunk = code_bytes[i:i+chunk_size]
                os.write(master_fd, chunk)
                time.sleep(0.05)

            time.sleep(0.3)
            # Send Enter
            os.write(master_fd, b"\r")
            log("Code written to PTY, waiting for CLI response...")

            # Read CLI output after code submission
            response = read_until(master_fd, 60, r"(?i)(success|authenticated|error|invalid|logged in)")
            resp_text = response.decode("utf-8", errors="replace")
            resp_clean = re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', resp_text)
            log(f"CLI output after code: {repr(resp_clean[:300])}")

            # Wait for process to exit
            time.sleep(2)

            # Check auth status
            try:
                result = subprocess.run(
                    ["claude", "auth", "status", "--json"],
                    capture_output=True, text=True, timeout=10
                )
                status = json.loads(result.stdout) if result.stdout.strip() else {}
                logged_in = status.get("loggedIn", False)
                log(f"Auth status: loggedIn={logged_in}, full={json.dumps(status)}")
                write_response("code-complete", {"success": logged_in})
            except Exception as e:
                log(f"Status check failed: {e}")
                write_response("code-complete", {"success": False, "error": str(e)})

            # Clean up
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
            return

        time.sleep(2)

    log("Timed out waiting for code-submit")
    write_response("code-complete", {"success": False, "error": "Timed out"})
    os.kill(pid, signal.SIGTERM)
    os.waitpid(pid, 0)

if __name__ == "__main__":
    main()
