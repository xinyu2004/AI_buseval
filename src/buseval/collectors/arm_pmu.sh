#!/bin/sh
# ARM PC collector. Runs perf stat around a short memory workload and writes meas.json.
# POSIX sh only: no bash arrays, no jq. JSON is produced by the Python parser
# that already ships with buseval on this machine.
set -eu

OUT=""
PROBE=0
PATTERN=read
BYTES=67108864
THREADS=1
DURATION=1
NAME=CPU0

while [ $# -gt 0 ]; do
  case "$1" in
    -o) OUT=$2; shift 2 ;;
    --probe) PROBE=1; shift ;;
    --pattern) PATTERN=$2; shift 2 ;;
    --bytes) BYTES=$2; shift 2 ;;
    --threads) THREADS=$2; shift 2 ;;
    --duration) DURATION=$2; shift 2 ;;
    --name) NAME=$2; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if ! command -v perf >/dev/null 2>&1; then
  echo "perf is not installed or not on PATH" >&2
  exit 1
fi

PART=$(awk -F: '/CPU part/ { gsub(/[ \t]/, "", $2); print $2; exit }' /proc/cpuinfo 2>/dev/null || true)
# Event names differ by core. These two exist on A53/A72/A76 PMUs when the kernel exposes them.
EVENTS="l2d_cache_refill,l2d_cache_wb"
case "$PART" in
  0xd03|0xd07|0xd08|0xd09|0xd0a|0xd0b|0xd0c|0xd0d|0xd40|0xd41|0xd44|0xd4a|"")
    EVENTS="l2d_cache_refill,l2d_cache_wb"
    ;;
esac

if [ "$PROBE" -eq 1 ]; then
  echo "cpu_part=${PART:-unknown}"
  echo "events=$EVENTS"
  perf list 2>/dev/null | awk 'BEGIN{IGNORECASE=1} /l2d_cache_refill|l2d_cache_wb|bus_access/ { print }'
  exit 0
fi

if [ -z "$OUT" ]; then
  echo "arm_pmu.sh requires -o meas.json" >&2
  exit 2
fi

RAW=$(mktemp)
trap 'rm -f "$RAW"' EXIT

# perf wraps the workload. The workload moves `bytes` per thread once, then
# waits out `duration`, matching the cpu estimator's bytes/duration rate.
if ! perf stat -e "$EVENTS" -o "$RAW" -- python3 -c '
import sys, time
pattern, nbytes, threads, duration = sys.argv[1], int(sys.argv[2]), max(int(sys.argv[3]), 1), float(sys.argv[4])
total = max(nbytes * threads, 4096)
buf = bytearray(total)
start = time.perf_counter()
if pattern == "write":
    for i in range(0, total, 64):
        buf[i] = 1
elif pattern == "copy":
    dst = bytearray(total)
    dst[:] = buf
else:
    acc = 0
    for i in range(0, total, 64):
        acc += buf[i]
    sys.stderr.write(str(acc % 2))
remain = duration - (time.perf_counter() - start)
if remain > 0:
    time.sleep(remain)
' "$PATTERN" "$BYTES" "$THREADS" "$DURATION"; then
  echo "perf stat failed. Check /proc/sys/kernel/perf_event_paranoid or event names ($EVENTS)." >&2
  exit 1
fi

if ! python3 -m buseval.collect.perf_text "$RAW" -o "$OUT" --name "$NAME"; then
  echo "failed to convert perf stat text into meas.json" >&2
  exit 1
fi
