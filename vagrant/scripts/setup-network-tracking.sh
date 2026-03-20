#!/usr/bin/env bash
# setup-network-tracking.sh — Configure DNS logging and connection tracking
#
# Called by: provision.sh (automatically), or manually to reconfigure
# OS:        Ubuntu 24.04 LTS
# Idempotent: yes
#
# What this does:
#   - Configures dnsmasq to log all DNS queries to /var/log/aquarco/dns-queries.log
#   - Adds iptables OUTPUT logging for new outbound connections (LOG only, no BLOCK)
#   - Persists iptables rules via iptables-persistent
#   - Installs log rotation for network logs
#   - Installs a daily domain-usage summary cron job

set -euo pipefail

LOG_DIR="/var/log/aquarco"
DNS_LOG="${LOG_DIR}/dns-queries.log"
CONN_LOG="${LOG_DIR}/connections.log"

log() {
  echo "[network-tracking] $*"
}

# ─── 1. Ensure log directory ──────────────────────────────────────────────────

mkdir -p "${LOG_DIR}"
touch "${DNS_LOG}" "${CONN_LOG}"
chmod 644 "${DNS_LOG}" "${CONN_LOG}"

# ─── 2. dnsmasq — local resolver with full query logging ──────────────────────

log "Configuring dnsmasq DNS logging..."

# Stop systemd-resolved if it is occupying port 53
if systemctl is-active --quiet systemd-resolved; then
  log "Disabling systemd-resolved stub listener to free port 53..."
  mkdir -p /etc/systemd/resolved.conf.d
  cat > /etc/systemd/resolved.conf.d/no-stub.conf <<'EOF'
[Resolve]
DNSStubListener=no
EOF
  systemctl restart systemd-resolved
fi

# Point /etc/resolv.conf at dnsmasq
if [[ ! -L /etc/resolv.conf ]] || [[ "$(readlink /etc/resolv.conf)" != "/run/systemd/resolve/resolv.conf" ]]; then
  # Replace with a static file pointing to dnsmasq on localhost
  cat > /etc/resolv.conf <<'EOF'
# Managed by aquarco setup-network-tracking.sh
# All DNS goes through dnsmasq (port 53) for logging
nameserver 127.0.0.1
EOF
fi

# dnsmasq tracking config
cat > /etc/dnsmasq.d/aquarco-tracking.conf <<'EOF'
# Aquarco network tracking — DNS query logging
# All queries are logged; dnsmasq forwards them upstream unchanged.
# This config is managed by vagrant/scripts/setup-network-tracking.sh

# Log all DNS queries (extra format includes timestamp, query type, client IP)
log-queries=extra

# Write DNS query log to dedicated file (separate from syslog)
log-facility=/var/log/aquarco/dns-queries.log

# Use Google + Cloudflare as upstream resolvers
server=8.8.8.8
server=8.8.4.4
server=1.1.1.1

# Cache size (reduce upstream queries for repeated lookups)
cache-size=1000

# Don't read /etc/hosts for upstream forwarding decisions
no-hosts

# Listen only on loopback (DNS for this machine only)
interface=lo
listen-address=127.0.0.1
bind-interfaces
EOF

# Validate dnsmasq config before enabling
dnsmasq --test 2>&1 && log "dnsmasq config is valid"

systemctl enable dnsmasq
systemctl restart dnsmasq
log "dnsmasq is running and logging DNS queries to ${DNS_LOG}"

# ─── 3. iptables — outbound connection logging (LOG only, never BLOCK) ────────

log "Configuring iptables outbound connection logging..."

# Flush any existing AIHOME logging rules to avoid duplicates on re-run
iptables -D OUTPUT -m state --state NEW -j LOG \
  --log-prefix "AIHOME_OUT: " --log-level info 2>/dev/null || true

# Add rule: log every new outbound TCP/UDP connection
# IMPORTANT: This is LOG-only. No DROP or REJECT rules are added.
iptables -A OUTPUT -m state --state NEW -j LOG \
  --log-prefix "AIHOME_OUT: " \
  --log-level info

# Persist iptables rules so they survive reboots
log "Persisting iptables rules..."
netfilter-persistent save 2>/dev/null || iptables-save > /etc/iptables/rules.v4

log "iptables logging rule installed (OUTPUT NEW connections -> syslog with prefix AIHOME_OUT:)"

# ─── 4. rsyslog — route AIHOME_OUT kernel messages to dedicated log file ──────

log "Configuring rsyslog to capture iptables AIHOME_OUT messages..."
cat > /etc/rsyslog.d/49-aquarco-connections.conf <<'EOF'
# Route Aquarco iptables connection log messages to a dedicated file.
# Matches kernel messages with the AIHOME_OUT: prefix.
:msg, contains, "AIHOME_OUT:" /var/log/aquarco/connections.log
& stop
EOF

systemctl restart rsyslog
log "rsyslog configured — connection logs will appear in ${CONN_LOG}"

# ─── 5. Log rotation for network logs ─────────────────────────────────────────

log "Installing logrotate config for network logs..."
cat > /etc/logrotate.d/aquarco-network <<'EOF'
/var/log/aquarco/dns-queries.log
/var/log/aquarco/connections.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0644 root root
    sharedscripts
    postrotate
        # Signal dnsmasq to reopen its log file after rotation
        kill -HUP "$(cat /run/dnsmasq.pid 2>/dev/null)" 2>/dev/null || true
        # Signal rsyslog to reopen log files
        systemctl kill --signal=HUP rsyslog 2>/dev/null || true
    endscript
}
EOF

# ─── 6. Daily domain summary cron job ─────────────────────────────────────────

log "Installing daily network summary cron job..."

REPORT_SCRIPT="/usr/local/bin/aquarco-network-daily-summary"
cat > "${REPORT_SCRIPT}" <<'SUMMARY'
#!/usr/bin/env bash
# Daily summary: parse DNS query log and emit a domain usage report to syslog/log file.
set -euo pipefail

LOG_FILE="/var/log/aquarco/dns-queries.log"
SUMMARY_FILE="/var/log/aquarco/network-summary-$(date +%Y%m%d).txt"
CUTOFF_EPOCH=$(date -d "yesterday 00:00:00" +%s 2>/dev/null || date -v-1d -j -f "%H:%M:%S" "00:00:00" +%s)

{
  echo "Domain Usage Report — $(date -d yesterday '+%Y-%m-%d' 2>/dev/null || date -v-1d '+%Y-%m-%d')"
  echo "Generated: $(date -Iseconds)"
  echo "─────────────────────────────────────────────────────────────"

  if [[ -f "${LOG_FILE}" ]]; then
    # dnsmasq extra format: <timestamp> dnsmasq[PID]: query[TYPE] <domain> from <ip>
    grep "query\[" "${LOG_FILE}" \
      | awk '{
          # Extract domain from "query[TYPE] domain from ..."
          for (i=1; i<=NF; i++) {
            if ($i ~ /^query\[/) {
              domain = $(i+1)
              # Normalise to lowercase, strip trailing dot
              gsub(/\.$/, "", domain)
              n = tolower(domain)
              count[n]++
            }
          }
        }
        END {
          for (d in count) print count[d], d
        }' \
      | sort -rn \
      | awk '{printf "%-50s %8d queries\n", $2, $1}' \
      | head -50
  else
    echo "(No DNS log found at ${LOG_FILE})"
  fi

  echo ""
  echo "─────────────────────────────────────────────────────────────"
  echo "Full logs: ${LOG_FILE}"
} > "${SUMMARY_FILE}"

echo "[aquarco] Daily network summary written to ${SUMMARY_FILE}"

# Keep only the last 30 daily summaries
find /var/log/aquarco -name "network-summary-*.txt" -mtime +30 -delete 2>/dev/null || true
SUMMARY

chmod +x "${REPORT_SCRIPT}"

# Run daily at 00:05 so previous day's log is fully written
cat > /etc/cron.d/aquarco-network-summary <<'CRON'
# Aquarco daily network domain usage summary
5 0 * * * root /usr/local/bin/aquarco-network-daily-summary
CRON

log "Daily summary cron installed — runs at 00:05, output in /var/log/aquarco/network-summary-YYYYMMDD.txt"

# ─── Done ─────────────────────────────────────────────────────────────────────

log ""
log "Network tracking is active:"
log "  DNS queries  -> ${DNS_LOG}"
log "  Connections  -> ${CONN_LOG}"
log ""
log "To view live DNS queries:"
log "  tail -f ${DNS_LOG}"
log ""
log "To view live connections:"
log "  tail -f ${CONN_LOG}"
log ""
log "To generate an on-demand domain report:"
log "  /home/agent/aquarco/supervisor/scripts/network-report.sh"
log ""
