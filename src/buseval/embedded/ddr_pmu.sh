#!/bin/sh
# Copy this script onto a machine that has perf.
# It does not call buseval. Event choice matches src/buseval/collect/pmu.py:
# memory-controller PMUs from `perf list`, not CPU cache misses.
#
# Run it: sh ddr_pmu.sh
# Prints count window, count size, DDR read, and DDR write. -o saves the same text
# without color. --probe only lists events.
# Sourcing would apply `exit` to the current shell, so a sourced call is
# handed to a child shell and control returns to the prompt.
if [ -n "${BASH_VERSION:-}" ] && [ "${BASH_SOURCE[0]:-}" != "$0" ]; then
  bash "${BASH_SOURCE[0]}" "$@"
  return $?
fi
set -eu

OUT=""
PROBE=0
DURATION=1

while [ $# -gt 0 ]; do
  case "$1" in
    -o) OUT=$2; shift 2 ;;
    --probe) PROBE=1; shift ;;
    --duration) DURATION=$2; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if ! command -v perf >/dev/null 2>&1; then
  echo "perf is not installed or not on PATH" >&2
  exit 1
fi

needs_password() {
  if [ "$(id -u)" -eq 0 ]; then
    return 1
  fi
  if sudo -n true >/dev/null 2>&1; then
    return 1
  fi
  return 0
}

pick_pair() {
  perf list 2>/dev/null | awk '
    function burst(spec,    e, pmu, event, family, role, channel, rank) {
      if (split(spec, parts, "/") < 2) return
      pmu = parts[1]
      event = parts[2]
      e = tolower(spec)
      if (pmu ~ /^(cpu|software|tracepoint|kprobe|uprobe|breakpoint)$/) return
      if (e ~ /clk|cycle|act|pchg|ratio|alloc|slot|page_tbl|_io_/) return
      if (e !~ /cas|dram|ddr|data_read|data_write/) return
      family = pmu
      sub(/_[0-9]+$/, "", family)
      role = ""
      if (e ~ /write|cas_cmd\.wr|\.wr\//) role = "write"
      else if (e ~ /read|cas_cmd\.rd|\.rd\//) role = "read"
      if (role == "") return
      channel = (event ~ /_[0-9]+$/) ? 1 : 0
      rank = 9
      if (e ~ /cas_cmd\.rd|cas_cmd\.wr/) rank = 0
      else if (e ~ /cas_count_read|cas_count_write/) rank = 1
      else if (e ~ /data_read|data_write/) rank = 2
      print family, role, channel, rank, spec
    }
    { burst($1) }
  ' | sort -k3,3n -k4,4n | awk '
    {
      fam = $1
      role = $2
      channel = $3
      spec = $5
      key = fam SUBSEP role
      if (!(key in seen)) {
        seen[key] = 1
        pick[key] = spec
        families[fam] = 1
      }
      if (channel == 0) agg[fam] = 1
    }
    END {
      for (fam in families) {
        if (!((fam SUBSEP "read") in pick && (fam SUBSEP "write") in pick)) continue
        if (best == "" || (agg[fam] && !agg[best]) || (agg[fam] && agg[best] && fam < best)) best = fam
      }
      if (best != "") print pick[best SUBSEP "read"] "," pick[best SUBSEP "write"]
    }
  '
}

PAIR=$(pick_pair || true)
if [ -z "$PAIR" ] && grep -q AuthenticAMD /proc/cpuinfo 2>/dev/null; then
  if needs_password; then
    cat <<'EOF' >&2
This CPU is AMD and the memory-controller counters are not registered.
buseval collect will run: sudo modprobe amd_uncore

What changes: the in-tree AMD uncore driver is loaded until reboot.
It is not written to the boot configuration.

Effect: the kernel then exposes memory-controller perf counters.
Those counters do not modify memory contents. Counting them still
needs administrator rights.
EOF
    printf 'Continue and enter the administrator password? [y/N] ' >&2
    read -r answer || answer=""
    case "$answer" in
      y|Y|yes|YES) ;;
      *) echo cancelled >&2; exit 130 ;;
    esac
  fi
  sudo modprobe amd_uncore || true
  PAIR=$(pick_pair || true)
fi

if [ -z "$PAIR" ]; then
  echo "perf list has no memory-controller read and write events" >&2
  exit 1
fi

count_bytes() {
  read_ev=$(printf '%s\n' "$PAIR" | awk -F, '{split($1, a, "/"); print a[2]}')
  write_ev=$(printf '%s\n' "$PAIR" | awk -F, '{split($2, a, "/"); print a[2]}')
  pmu=$(printf '%s\n' "$PAIR" | awk -F/ '{print $1}')
  scaled=$(
    for dir in /sys/bus/event_source/devices/"$pmu" /sys/bus/event_source/devices/"$pmu"_*; do
      [ -d "$dir/events" ] || continue
      for ev in "$read_ev" "$write_ev"; do
        if [ -f "$dir/events/$ev.scale" ]; then
          awk 'NR==1 {print $1; exit}' "$dir/events/$ev.scale"
        elif [ -f "$dir/events/$ev" ]; then
          awk -F= '/^scale=/ {print $2; exit}' "$dir/events/$ev"
        fi
      done
    done
  )
  from_sys=$(printf '%s\n' "$scaled" | awk '
    function keep(v,    n) {
      if (v == "" || v < 1) return
      n = int(v + 0.5)
      if ((v > n ? v - n : n - v) > 0.000001) return
      if (got != "" && got != n) bad = 1
      got = n
    }
    NF { keep($1 + 0) }
    END { if (!bad && got != "") print got }
  ')
  if [ -n "$from_sys" ]; then
    printf '%s\n' "$from_sys"
    return 0
  fi
  perf list --details 2>/dev/null | awk -v read_ev="$read_ev" -v write_ev="$write_ev" '
    function factor(line, ev,    i, n, rest, pre, before, p) {
      if (index(line, "duration") == 0 || index(line, ev) == 0) return ""
      n = length(ev)
      i = 1
      while (i <= length(line)) {
        p = index(substr(line, i), ev)
        if (p == 0) return ""
        i = i + p - 1
        before = (i == 1) ? "" : substr(line, i - 1, 1)
        if (before ~ /[A-Za-z0-9_]/) { i++; continue }
        rest = substr(line, i + n)
        sub(/^[ \t]+/, "", rest)
        if (substr(rest, 1, 1) == "*") {
          sub(/^\*[ \t]*/, "", rest)
          if (match(rest, /^[0-9]+/)) return substr(rest, 1, RLENGTH) + 0
        }
        pre = substr(line, 1, i - 1)
        if (match(pre, /[0-9]+[ \t]*\*[ \t]*$/)) {
          num = substr(pre, RSTART, RLENGTH)
          gsub(/[^0-9]/, "", num)
          return num + 0
        }
        i++
      }
      return ""
    }
    function note(n) {
      if (n == "") return
      if (got != "" && got != n) bad = 1
      got = n
    }
    { note(factor($0, read_ev)); note(factor($0, write_ev)) }
    END { if (!bad && got != "") print got }
  '
}

BYTES=$(count_bytes || true)

if [ "$PROBE" -eq 1 ]; then
  echo "arch=$(uname -m)"
  echo "events=$PAIR"
  echo "bytes=${BYTES:-}"
  exit 0
fi

format_report() {
  awk -v use_color="$1" -v bytes="${BYTES:-0}" '
    function role(name,    e) {
      e = tolower(name)
      if (e ~ /clk|cycle|act|pchg|ratio|alloc|slot|page_tbl|_io_/) return ""
      if (e !~ /cas|dram|ddr|data_read|data_write/) return ""
      if (e ~ /write|cas_cmd\.wr|\.wr/) return "write"
      else if (e ~ /read|cas_cmd\.rd|\.rd/) return "read"
      return ""
    }
    function tint(text, code) {
      if (use_color + 0) return "\033[" code "m" text "\033[0m"
      return text
    }
    function near_bytes(rate, count,    raw, nearest, diff) {
      if (rate == "" || count == 0 || duration == 0) return ""
      raw = rate * 1000000 * duration / count
      nearest = int(raw + 0.5)
      diff = raw - nearest
      if (diff < 0) diff = -diff
      if (nearest < 1 || diff > 0.05) return ""
      return nearest
    }
    {
      if ($0 ~ /not counted|not supported/) next
      if (match($0, /[0-9]+(\.[0-9]+)?[ \t]+seconds time elapsed/)) {
        duration = substr($0, RSTART, RLENGTH)
        sub(/[ \t]+seconds time elapsed/, "", duration)
        next
      }
      gsub(/,/, "", $1)
      if ($1 ~ /^[0-9]+(\.[0-9]+)?$/ && $2 != "") {
        kind = role($2)
        if (kind == "read") read_n += $1
        else if (kind == "write") write_n += $1
        if (match($0, /#[ \t]*[0-9]+(\.[0-9]+)?[ \t]*MB\/s/)) {
          chunk = substr($0, RSTART, RLENGTH)
          if (match(chunk, /[0-9]+(\.[0-9]+)?/)) {
            if (kind == "read") rate_read = substr(chunk, RSTART, RLENGTH)
            else if (kind == "write") rate_write = substr(chunk, RSTART, RLENGTH)
          }
        }
      }
    }
    END {
      if (duration == "" || (read_n == 0 && write_n == 0)) exit 1
      if (bytes + 0 <= 0) {
        one = near_bytes(rate_read, read_n)
        two = near_bytes(rate_write, write_n)
        if (one != "" && two != "" && one != two) bytes = 0
        else if (one != "") bytes = one
        else bytes = two + 0
      }
      time_s = tint(duration " s", "35")
      print "count window  " time_s
      if (bytes + 0 > 0) {
        size_b = tint(bytes " B", "94")
        print "count size  " size_b
      }
      print ""
      printf "DDR read  %d times\n", read_n
      if (bytes + 0 > 0)
        printf "%d × %s / %s = %.4f MB/s\n", read_n, size_b, time_s, read_n * bytes / duration / 1000000
      print ""
      printf "DDR write  %d times\n", write_n
      if (bytes + 0 > 0)
        printf "%d × %s / %s = %.4f MB/s\n", write_n, size_b, time_s, write_n * bytes / duration / 1000000
    }
  '
}

RAW=$(mktemp)
trap 'rm -f "$RAW"' EXIT

if [ "$(id -u)" -eq 0 ]; then
  perf stat -a -e "$PAIR" -- sleep "$DURATION" >"$RAW" 2>&1
else
  if needs_password; then
    cat <<EOF >&2
buseval collect will run: sudo perf stat -a
for about ${DURATION} second(s).

What changes: nothing is written to boot configuration, and
perf_event_paranoid is not modified. The administrator password
is used only for this one command.

What it can see: read and write bursts at the memory controller
for the whole machine, including DMA from other programs, the
NIC, and the GPU. On a shared machine, a local user can thus
see the size of that traffic.

What it cannot see: file contents, and it does not change memory.
EOF
    printf 'Continue and enter the administrator password? [y/N] ' >&2
    read -r answer || answer=""
    case "$answer" in
      y|Y|yes|YES) ;;
      *) echo cancelled >&2; exit 130 ;;
    esac
  fi
  sudo perf stat -a -e "$PAIR" -- sleep "$DURATION" >"$RAW" 2>&1
fi || {
  cat "$RAW" >&2
  echo "perf stat failed for $PAIR" >&2
  exit 1
}

COLOR=0
if [ -t 1 ]; then
  COLOR=1
fi
if ! format_report "$COLOR" <"$RAW"; then
  cat "$RAW" >&2
  echo "perf stat failed for $PAIR" >&2
  exit 1
fi
if [ -n "$OUT" ]; then
  format_report 0 <"$RAW" >"$OUT"
fi
