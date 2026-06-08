#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8289}"
PID_FILE="${PID_FILE:-$ROOT/data/stockwhisper.pid}"
LOG_FILE="${LOG_FILE:-$ROOT/data/stockwhisper.log}"
ENV_FILE="${ENV_FILE:-$ROOT/.env.local}"

mkdir -p "$ROOT/data"

load_env() {
  if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi
}

pid_running() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE")"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

port_pid() {
  rtk python3 - "$PORT" <<'PY'
import os
import sys
from pathlib import Path

port = int(sys.argv[1])
port_hex = f"{port:04X}"
inodes = set()
for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
    if not table.exists():
        continue
    for line in table.read_text().splitlines()[1:]:
        parts = line.split()
        if parts[1].endswith(":" + port_hex) and parts[3] == "0A":
            inodes.add(parts[9])
for proc in Path("/proc").iterdir():
    if not proc.name.isdigit():
        continue
    try:
        cmdline = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        if "uvicorn app.main:app" not in cmdline:
            continue
        for fd in (proc / "fd").iterdir():
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target.startswith("socket:[") and target[8:-1] in inodes:
                print(proc.name)
                raise SystemExit(0)
    except (PermissionError, FileNotFoundError):
        pass
raise SystemExit(1)
PY
}

adopt_running_port_process() {
  local pid
  if pid="$(port_pid 2>/dev/null)"; then
    echo "$pid" >"$PID_FILE"
    return 0
  fi
  return 1
}

start_service() {
  if pid_running; then
    echo "stockwhisper already running: pid $(cat "$PID_FILE")"
    return 0
  fi
  if adopt_running_port_process; then
    echo "stockwhisper already running on port $PORT: pid $(cat "$PID_FILE")"
    return 0
  fi
  load_env
  nohup rtk python3 -m uvicorn app.main:app --host "$HOST" --port "$PORT" >>"$LOG_FILE" 2>&1 &
  echo "$!" >"$PID_FILE"
  sleep 1
  if pid_running; then
    echo "stockwhisper started: pid $(cat "$PID_FILE"), http://127.0.0.1:$PORT"
    echo "log: $LOG_FILE"
  else
    echo "stockwhisper failed to start; check $LOG_FILE" >&2
    exit 1
  fi
}

stop_service() {
  if ! pid_running; then
    adopt_running_port_process >/dev/null 2>&1 || true
  fi
  if ! pid_running; then
    rm -f "$PID_FILE"
    echo "stockwhisper not running"
    return 0
  fi
  local pid
  pid="$(cat "$PID_FILE")"
  kill "$pid"
  for _ in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$PID_FILE"
      echo "stockwhisper stopped"
      return 0
    fi
    sleep 0.5
  done
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
  echo "stockwhisper force stopped"
}

status_service() {
  if ! pid_running; then
    adopt_running_port_process >/dev/null 2>&1 || true
  fi
  if pid_running; then
    echo "running: pid $(cat "$PID_FILE"), http://127.0.0.1:$PORT"
  else
    echo "stopped"
    exit 1
  fi
}

case "${1:-status}" in
  start)
    start_service
    ;;
  stop)
    stop_service
    ;;
  restart)
    stop_service
    start_service
    ;;
  status)
    status_service
    ;;
  logs)
    touch "$LOG_FILE"
    tail -f "$LOG_FILE"
    ;;
  *)
    echo "usage: $0 {start|stop|restart|status|logs}" >&2
    exit 2
    ;;
esac
