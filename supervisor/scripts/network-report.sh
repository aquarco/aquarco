#!/usr/bin/env bash
# network-report.sh — AI Fishtank network usage report
#
# Parses DNS query logs and iptables connection logs to produce a
# domain usage report. Supports JSON output and time filtering.
#
# Usage:
#   network-report.sh [options]
#
# Options:
#   --json           Output machine-readable JSON
#   --since <spec>   Only include entries newer than <spec>
#                    Accepts: Nh (N hours), Nd (N days), or ISO timestamp
#                    Examples: --since 24h  --since 7d  --since 2026-03-14T00:00:00
#   --top <N>        Limit to top N domains (default: 50)
#   --help           Show this help
#
# Examples:
#   network-report.sh
#   network-report.sh --since 24h
#   network-report.sh --since 7d --json
#   network-report.sh --since 2026-03-14T00:00:00 --top 20

set -euo pipefail

DNS_LOG="/var/log/aifishtank/dns-queries.log"
CONN_LOG="/var/log/aifishtank/connections.log"

# ─── Defaults ─────────────────────────────────────────────────────────────────

OUTPUT_JSON=false
SINCE_SPEC=""
TOP_N=50

# ─── Argument parsing ─────────────────────────────────────────────────────────

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --json           Output machine-readable JSON
  --since <spec>   Filter to entries newer than <spec>
                   Formats: 24h, 7d, 2026-03-14T00:00:00
  --top <N>        Show top N domains (default: 50)
  --help           Show this help

Examples:
  $(basename "$0")
  $(basename "$0") --since 24h
  $(basename "$0") --since 7d --json
  $(basename "$0") --top 20 --since 2026-03-14
EOF
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --json)        OUTPUT_JSON=true;  shift ;;
    --since)       SINCE_SPEC="$2";  shift 2 ;;
    --top)         TOP_N="$2";       shift 2 ;;
    --help|-h)     usage ;;
    *)             echo "Unknown option: $1" >&2; usage ;;
  esac
done

# ─── Time helpers ─────────────────────────────────────────────────────────────

# Parse --since spec to a Unix epoch cutoff
parse_since_epoch() {
  local spec="$1"
  local now
  now="$(date +%s)"

  if [[ "${spec}" =~ ^([0-9]+)h$ ]]; then
    echo $(( now - ${BASH_REMATCH[1]} * 3600 ))
  elif [[ "${spec}" =~ ^([0-9]+)d$ ]]; then
    echo $(( now - ${BASH_REMATCH[1]} * 86400 ))
  else
    # Treat as ISO timestamp
    date -d "${spec}" +%s 2>/dev/null \
      || date -j -f "%Y-%m-%dT%H:%M:%S" "${spec}" +%s 2>/dev/null \
      || { echo "Cannot parse --since '${spec}'" >&2; exit 1; }
  fi
}

CUTOFF_EPOCH=0
SINCE_LABEL="all time"
if [[ -n "${SINCE_SPEC}" ]]; then
  CUTOFF_EPOCH="$(parse_since_epoch "${SINCE_SPEC}")"
  SINCE_LABEL="since ${SINCE_SPEC}"
fi

# ─── DNS log parsing ──────────────────────────────────────────────────────────
#
# dnsmasq extra format (--log-queries=extra):
#   Jan  1 00:00:00 dnsmasq[1234]: query[A] github.com from 127.0.0.1
#   Jan  1 00:00:00 dnsmasq[1234]: query[AAAA] github.com from 127.0.0.1
#
# When log-facility is set, the syslog prefix is dropped; lines look like:
#   2026-01-01T00:00:00.000000+00:00 dnsmasq[1234]: query[A] github.com from 127.0.0.1

parse_dns_log() {
  local cutoff="$1"

  [[ -f "${DNS_LOG}" ]] || return 0

  awk -v cutoff="${cutoff}" '
  function parse_epoch(ts,    cmd, result) {
    # Try to get epoch from the timestamp field using date
    cmd = "date -d \"" ts "\" +%s 2>/dev/null || date -j -f \"%Y-%m-%dT%H:%M:%S\" \"" ts "\" +%s 2>/dev/null"
    cmd | getline result
    close(cmd)
    return (result + 0)
  }

  /query\[/ {
    # Find timestamp: first token if it looks like an ISO timestamp,
    # otherwise reconstruct from syslog MMM DD HH:MM:SS format
    ts = $1
    epoch = 0

    if (ts ~ /^[0-9]{4}-/) {
      # ISO format: strip sub-second and timezone for date parsing
      gsub(/\.[0-9]+[+-][0-9:]+$/, "", ts)
      gsub(/Z$/, "", ts)
      epoch = parse_epoch(ts)
    }
    # If epoch is 0 (parse failed or old syslog format), include the entry
    if (cutoff > 0 && epoch > 0 && epoch < cutoff) next

    # Extract domain: token after "query[TYPE]"
    for (i = 1; i <= NF; i++) {
      if ($i ~ /^query\[/) {
        domain = $(i+1)
        gsub(/\.$/, "", domain)
        domain = tolower(domain)
        count[domain]++
        if (first_seen[domain] == "") first_seen[domain] = $1
        last_seen[domain] = $1
      }
    }
  }

  END {
    for (d in count) {
      print count[d] "\t" d "\t" first_seen[d] "\t" last_seen[d]
    }
  }
  ' "${DNS_LOG}"
}

# ─── Connection log parsing ───────────────────────────────────────────────────
#
# iptables LOG format in syslog:
#   Mar 14 10:30:01 aifishtank kernel: AIHOME_OUT: IN= OUT=eth0 ... DST=140.82.121.4 ...

parse_conn_log() {
  local cutoff="$1"

  [[ -f "${CONN_LOG}" ]] || return 0

  awk -v cutoff="${cutoff}" '
  /AIHOME_OUT:/ {
    # Extract destination IP from DST=x.x.x.x
    for (i = 1; i <= NF; i++) {
      if ($i ~ /^DST=/) {
        ip = substr($i, 5)
        conn_count[ip]++
      }
    }
  }
  END {
    for (ip in conn_count) {
      print conn_count[ip] "\t" ip
    }
  }
  ' "${CONN_LOG}"
}

# ─── Collect data ─────────────────────────────────────────────────────────────

DNS_DATA="$(parse_dns_log "${CUTOFF_EPOCH}" | sort -rn)"
CONN_DATA="$(parse_conn_log "${CUTOFF_EPOCH}" | sort -rn)"

TOTAL_DNS="$(echo "${DNS_DATA}" | grep -c . 2>/dev/null || echo 0)"
TOTAL_UNIQUE_IPS="$(echo "${CONN_DATA}" | grep -c . 2>/dev/null || echo 0)"

NOW_ISO="$(date -Iseconds)"
REPORT_LABEL="Domain Usage Report (${SINCE_LABEL})"

# ─── JSON output ──────────────────────────────────────────────────────────────

if [[ "${OUTPUT_JSON}" == "true" ]]; then
  python3 - <<PYEOF
import json, sys

since_label = "${SINCE_LABEL}"
now = "${NOW_ISO}"
top_n = ${TOP_N}

dns_raw = """${DNS_DATA}"""
conn_raw = """${CONN_DATA}"""

domains = []
for line in dns_raw.strip().splitlines():
    if not line.strip():
        continue
    parts = line.split("\t")
    if len(parts) >= 2:
        entry = {
            "domain": parts[1] if len(parts) > 1 else "",
            "queries": int(parts[0]),
            "first_seen": parts[2] if len(parts) > 2 else None,
            "last_seen": parts[3] if len(parts) > 3 else None,
        }
        domains.append(entry)

domains = sorted(domains, key=lambda x: -x["queries"])[:top_n]

connections = []
for line in conn_raw.strip().splitlines():
    if not line.strip():
        continue
    parts = line.split("\t")
    if len(parts) >= 2:
        connections.append({"ip": parts[1], "count": int(parts[0])})

output = {
    "generated_at": now,
    "since": since_label,
    "summary": {
        "unique_domains": len(domains),
        "unique_dest_ips": len(connections),
        "total_dns_queries": sum(d["queries"] for d in domains),
    },
    "domains": domains,
    "top_destination_ips": sorted(connections, key=lambda x: -x["count"])[:20],
}
print(json.dumps(output, indent=2))
PYEOF
  exit 0
fi

# ─── Human-readable output ────────────────────────────────────────────────────

echo ""
echo "${REPORT_LABEL}"
echo "Generated: ${NOW_ISO}"
printf "%0.s─" {1..65}
echo ""
echo ""

if [[ -z "${DNS_DATA}" ]]; then
  echo "  No DNS query data found."
  echo "  Log file: ${DNS_LOG}"
  echo ""
else
  printf "  %-45s  %10s\n" "Domain" "Queries"
  printf "  %-45s  %10s\n" "─────────────────────────────────────────────" "──────────"

  echo "${DNS_DATA}" | head -n "${TOP_N}" | while IFS=$'\t' read -r count domain first last; do
    printf "  %-45s  %10s\n" "${domain}" "$(printf '%d' "${count}" | sed ':a;s/\B[0-9]\{3\}\>/,&/;ta')"
  done

  echo ""
  TOTAL_QUERIES="$(echo "${DNS_DATA}" | awk -F'\t' '{s+=$1} END {print s}')"
  printf "  %-45s  %10s\n" "TOTAL (all domains)" \
    "$(printf '%d' "${TOTAL_QUERIES}" | sed ':a;s/\B[0-9]\{3\}\>/,&/;ta')"
fi

echo ""
printf "%0.s─" {1..65}
echo ""

if [[ -n "${CONN_DATA}" ]]; then
  echo ""
  echo "  Top destination IPs (from iptables connection log):"
  echo ""
  printf "  %-20s  %10s\n" "Destination IP" "Connections"
  printf "  %-20s  %10s\n" "──────────────────" "───────────"
  echo "${CONN_DATA}" | head -n 20 | while IFS=$'\t' read -r count ip; do
    printf "  %-20s  %10d\n" "${ip}" "${count}"
  done
  echo ""
fi

printf "%0.s─" {1..65}
echo ""
echo ""
echo "  Log files:"
echo "    DNS queries  : ${DNS_LOG}"
echo "    Connections  : ${CONN_LOG}"
echo ""
echo "  Options: --json  --since <Nh|Nd|ISO>  --top <N>"
echo ""
