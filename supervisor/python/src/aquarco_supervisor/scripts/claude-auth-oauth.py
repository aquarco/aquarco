#!/usr/bin/env python3
"""
claude-auth-oauth.py — Direct OAuth PKCE flow for Claude CLI authentication.

Bypasses the CLI's Ink TUI entirely by performing the OAuth exchange directly
and writing credentials to ~/.claude/.credentials.json.

Uses the same client_id, endpoints, and credential format as the CLI.
The authorize URL is shown in the web UI; the user opens it in a browser,
authorizes, and copies the auth code back into the web UI.
"""

import hashlib
import base64
import json
import os
import secrets
import string
import sys
import time
import urllib.request
import urllib.error

IPC_DIR = sys.argv[1] if len(sys.argv) > 1 else "/var/lib/aquarco/claude-ipc"

# Claude CLI OAuth constants (from CLI source V0A + FP8/jZ1 functions)
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
REDIRECT_URI = "https://platform.claude.com/oauth/code/callback"
SCOPES = "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload"

# User-Agent rotation to avoid Cloudflare blocking.
# Cloudflare rejects Python's default UA and some custom strings.
# We try multiple known-good UAs in order.
USER_AGENTS = [
    "axios/1.7.9",
    "node-fetch/1.0 (+https://github.com/bitinn/node-fetch)",
    "Mozilla/5.0 (compatible; ClaudeCode/2.1)",
    "claude-code/2.1.84",
]


def get_cli_version():
    """Get installed Claude CLI version for User-Agent, fall back to default."""
    try:
        import subprocess
        out = subprocess.check_output(["claude", "--version"], text=True, timeout=5).strip()
        ver = out.split()[0] if out else "2.1.80"
        return ver
    except Exception:
        return "2.1.80"


CLI_VERSION = get_cli_version()

# Credential storage
CLAUDE_DIR = os.path.expanduser("~/.claude")
CREDENTIALS_FILE = os.path.join(CLAUDE_DIR, ".credentials.json")


def log(msg):
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(json.dumps({"ts": ts, "component": "claude-auth-oauth", "msg": msg}),
          file=sys.stderr, flush=True)


def write_response(name, data):
    path = os.path.join(IPC_DIR, name)
    with open(path, "w") as f:
        json.dump(data, f)


def generate_code_verifier(length=64):
    """Generate PKCE code_verifier (same algorithm as CLI)."""
    charset = string.ascii_letters + string.digits + "-._~"
    return "".join(secrets.choice(charset) for _ in range(length))


def generate_code_challenge(verifier):
    """Generate S256 code_challenge from verifier."""
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    b64 = base64.b64encode(digest).decode("utf-8")
    # URL-safe: replace / with _, + with -, remove =
    return b64.replace("/", "_").replace("+", "-").rstrip("=")


def build_authorize_url(code_challenge, state):
    """Build the OAuth authorize URL with PKCE parameters."""
    params = {
        "code": "true",
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    query = "&".join(f"{k}={urllib.request.quote(str(v), safe='')}" for k, v in params.items())
    return f"{AUTHORIZE_URL}?{query}"


def exchange_token(auth_code, code_verifier, state):
    """Exchange authorization code for tokens.

    Tries multiple User-Agent strings to avoid Cloudflare 403/429 blocks.
    Falls back to next UA on Cloudflare-style errors.
    """
    body = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "code_verifier": code_verifier,
        "state": state,
    }
    log(f"Token exchange payload: code_len={len(auth_code)}, "
        f"redirect_uri={REDIRECT_URI}, "
        f"verifier_len={len(code_verifier)}")
    payload = json.dumps(body).encode("utf-8")

    last_error = None

    for ua in USER_AGENTS:
        log(f"Trying User-Agent: {ua}")
        req = urllib.request.Request(
            TOKEN_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": ua,
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "identity",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                log(f"Token exchange succeeded with User-Agent: {ua}")
                return result
        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace")
            last_error = e

            # Cloudflare block (403) or rate limit (429) → try next UA
            if e.code in (403, 429):
                is_cloudflare = "cloudflare" in resp_body.lower() or "cf-ray" in str(e.headers).lower()
                log(f"HTTP {e.code} with UA '{ua}' "
                    f"(cloudflare={'yes' if is_cloudflare else 'no'}), "
                    f"body={resp_body[:150]}")
                if is_cloudflare:
                    time.sleep(2)  # Brief pause before trying next UA
                    continue
                # Non-Cloudflare 429 = real rate limit from OAuth server
                if e.code == 429:
                    log(f"Real rate limit (non-Cloudflare), waiting 30s")
                    time.sleep(30)
                    continue

            log(f"Token exchange failed: HTTP {e.code} — {resp_body[:300]}")
            raise
        except Exception as e:
            log(f"Token exchange error with UA '{ua}': {e}")
            last_error = e
            continue

    # All UAs exhausted — do one final retry with the best UA after a long pause
    log("All User-Agents exhausted, waiting 60s for final retry")
    time.sleep(60)

    best_ua = USER_AGENTS[0]
    req = urllib.request.Request(
        TOKEN_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": best_ua,
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "identity",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        resp_body = e.read().decode("utf-8", errors="replace")
        log(f"Final retry failed: HTTP {e.code} — {resp_body[:300]}")
        raise
    except Exception as e:
        log(f"Final retry error: {e}")
        if last_error:
            raise last_error
        raise


def save_credentials(token_response):
    """Save OAuth tokens in the same format as the CLI."""
    os.makedirs(CLAUDE_DIR, exist_ok=True)

    # Read existing credentials if any
    creds = {}
    if os.path.exists(CREDENTIALS_FILE):
        try:
            with open(CREDENTIALS_FILE, "r") as f:
                creds = json.load(f)
        except (json.JSONDecodeError, IOError):
            creds = {}

    expires_in = token_response.get("expires_in", 3600)
    expires_at = int(time.time() * 1000) + (expires_in * 1000)

    creds["claudeAiOauth"] = {
        "accessToken": token_response["access_token"],
        "refreshToken": token_response.get("refresh_token", ""),
        "expiresAt": expires_at,
        "scopes": token_response.get("scope", SCOPES),
    }

    # Store account info if present
    account = token_response.get("account", {})
    org = token_response.get("organization", {})
    if account:
        creds["oauthAccount"] = {
            "accountUuid": account.get("uuid", ""),
            "emailAddress": account.get("email_address", ""),
            "organizationUuid": org.get("uuid", ""),
            "organizationName": org.get("name", ""),
        }

    with open(CREDENTIALS_FILE, "w") as f:
        json.dump(creds, f, indent=2)
    os.chmod(CREDENTIALS_FILE, 0o600)

    log(f"Credentials saved to {CREDENTIALS_FILE}")


def main():
    log("Starting direct OAuth PKCE flow")

    # Generate PKCE parameters
    code_verifier = generate_code_verifier()
    code_challenge = generate_code_challenge(code_verifier)
    state = secrets.token_urlsafe(32)

    # Build authorize URL
    authorize_url = build_authorize_url(code_challenge, state)
    log(f"Authorize URL: {authorize_url[:80]}...")

    # Write the URL for the web UI
    write_response("login-response", {"authorizeUrl": authorize_url})

    # Wait for auth code from web UI
    code_file = os.path.join(IPC_DIR, "code-submit")
    deadline = time.time() + 600  # 10 minute timeout

    log("Waiting for code-submit...")

    while time.time() < deadline:
        if os.path.exists(code_file):
            with open(code_file, "r") as f:
                auth_code = f.read().strip()
            os.unlink(code_file)

            if not auth_code:
                log("Empty code-submit file, ignoring")
                continue

            # The callback page shows "code#state" — strip the #state suffix
            if "#" in auth_code:
                auth_code = auth_code.split("#")[0]

            log(f"Received auth code ({len(auth_code)} chars)")

            try:
                token_response = exchange_token(auth_code, code_verifier, state)
                log("Token exchange successful")

                save_credentials(token_response)

                write_response("code-complete", {"success": True})
                log("Login complete!")
                return

            except Exception as e:
                error_msg = str(e)
                log(f"Token exchange failed: {error_msg}")
                write_response("code-complete", {
                    "success": False,
                    "error": f"Token exchange failed: {error_msg}",
                })
                return

        time.sleep(2)

    log("Timed out waiting for code-submit")
    write_response("code-complete", {"success": False, "error": "Timed out"})


if __name__ == "__main__":
    main()
