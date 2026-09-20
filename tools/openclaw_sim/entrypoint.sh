#!/usr/bin/env sh
set -eu

mkdir -p "$SKILLS_DIR"
python /app/fake_openclaw_gateway.py &
gateway_pid="$!"

sleep 1
python /app/skillforgebridge.py --daemon &
bridge_pid="$!"

term() {
  kill "$bridge_pid" "$gateway_pid" 2>/dev/null || true
  wait "$bridge_pid" "$gateway_pid" 2>/dev/null || true
}
trap term INT TERM

wait "$bridge_pid"
