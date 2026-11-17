#!/bin/bash
# situation-monitor/scripts/setup.sh
# One-shot setup on a fresh clone. Safe to re-run — every step is idempotent.
#
#   1. Python venv + backend dependencies   (Python 3.11+)
#   2. .env from .env.example, prompting for the three core credentials
#   3. Frontend build                       (Node 18+; skipped if Node missing)
#   4. SQLite database + schema
#   5. Optional: always-on service          (--service)
#
# Usage:
#   scripts/setup.sh                  # interactive
#   scripts/setup.sh --service        # also install the auto-start service
#   scripts/setup.sh --dev            # also install test dependencies
#   scripts/setup.sh --no-prompt      # never prompt (CI / scripted installs)
#
# After setup: scripts/run.sh (foreground) — first launch pulls market, FRED,
# EDGAR etc. automatically. For deep history see scripts/backfill_history.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
ENV_FILE="$PROJECT_ROOT/.env"

WANT_SERVICE=0
WANT_DEV=0
PROMPT=1
[ -t 0 ] || PROMPT=0
for arg in "$@"; do
    case "$arg" in
        --service)   WANT_SERVICE=1 ;;
        --dev)       WANT_DEV=1 ;;
        --no-prompt) PROMPT=0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

step() { printf '\n\033[1;33m==> %s\033[0m\n' "$1"; }
warn() { printf '\033[1;31m  ! %s\033[0m\n' "$1"; }

# ── 1. Python ────────────────────────────────────────────────────────────────
step "Python environment"
PY=""
for cand in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$cand" >/dev/null 2>&1 &&
       "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
        PY="$cand"; break
    fi
done
if [ -z "$PY" ]; then
    echo "ERROR: Python 3.11+ not found. Install it (macOS: brew install python@3.14)." >&2
    exit 1
fi
echo "  using $("$PY" --version) ($(command -v "$PY"))"

if [ ! -x "$BACKEND_DIR/.venv/bin/python" ]; then
    "$PY" -m venv "$BACKEND_DIR/.venv"
fi
VENV_PY="$BACKEND_DIR/.venv/bin/python"
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -r "$BACKEND_DIR/requirements.txt"
if [ "$WANT_DEV" = 1 ]; then
    "$VENV_PY" -m pip install --quiet -r "$BACKEND_DIR/requirements-dev.txt"
fi
echo "  dependencies installed"

# ── 2. .env ──────────────────────────────────────────────────────────────────
step "Credentials (.env)"
if [ ! -f "$ENV_FILE" ]; then
    cp "$PROJECT_ROOT/.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "  created .env from .env.example"
fi

# set_env KEY VALUE — replace the KEY= line in .env (value passed via env,
# so keys containing sed/regex metacharacters are safe).
set_env() {
    KEY="$1" VAL="$2" "$VENV_PY" - "$ENV_FILE" <<'PYEOF'
import os, sys
path, key, val = sys.argv[1], os.environ["KEY"], os.environ["VAL"]
lines = open(path).read().splitlines()
for i, line in enumerate(lines):
    if line.startswith(key + "="):
        lines[i] = f"{key}={val}"
        break
else:
    lines.append(f"{key}={val}")
open(path, "w").write("\n".join(lines) + "\n")
PYEOF
}
get_env() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- || true; }

ask() {  # ask KEY "prompt" [hidden]
    local key="$1" label="$2" hidden="${3:-}" cur val
    cur="$(get_env "$key")"
    if [ -n "$cur" ] && [[ "$cur" != *you@email.com* ]]; then
        echo "  $key already set"
        return
    fi
    if [ "$PROMPT" = 0 ]; then
        warn "$key not set — edit .env"
        return
    fi
    if [ -n "$hidden" ]; then
        read -r -s -p "  $label (Enter to skip): " val; echo
    else
        read -r -p "  $label (Enter to skip): " val
    fi
    if [ -n "$val" ]; then set_env "$key" "$val"; else warn "$key skipped — edit .env later"; fi
}

ask ANTHROPIC_API_KEY "Anthropic API key (console.anthropic.com) — news scoring, digests, analyst" secret
ask FRED_API_KEY "FRED API key (fred.stlouisfed.org/docs/api/api_key.html, free)" secret
if [ "$PROMPT" = 1 ] && { [ -z "$(get_env EDGAR_USER_AGENT)" ] || [[ "$(get_env EDGAR_USER_AGENT)" == *you@email.com* ]]; }; then
    read -r -p "  Contact email for the SEC/NY Fed User-Agent (Enter to skip): " email
    if [ -n "$email" ]; then set_env EDGAR_USER_AGENT "SituationMonitor/0.1 $email"; fi
fi
[[ "$(get_env EDGAR_USER_AGENT)" == *you@email.com* ]] &&
    warn "EDGAR_USER_AGENT still has the placeholder email — SEC may block requests"
echo "  optional extras (Gmail newsletters, ntfy push alerts) are documented in .env"

# ── 3. Frontend ──────────────────────────────────────────────────────────────
step "Frontend build"
if command -v node >/dev/null 2>&1 &&
   node -e 'process.exit(parseInt(process.versions.node, 10) >= 18 ? 0 : 1)' 2>/dev/null; then
    (cd "$FRONTEND_DIR" && npm ci --silent && npm run build --silent >/dev/null)
    echo "  built frontend/dist (served by the backend at /)"
else
    warn "Node 18+ not found — skipping. The API still works; install Node and re-run for the dashboard UI."
fi

# ── 4. Database ──────────────────────────────────────────────────────────────
step "Database"
mkdir -p "$PROJECT_ROOT/logs"
# Importing cache.db creates the file and applies the schema + migrations.
(cd "$BACKEND_DIR" && "$VENV_PY" -c "import cache.db; from config import settings; print('  ', settings.DB_PATH)")

# ── 5. Service ───────────────────────────────────────────────────────────────
if [ "$WANT_SERVICE" = 1 ]; then
    step "Auto-start service"
    "$SCRIPT_DIR/install_service.sh"
fi

step "Done"
if [ "$WANT_SERVICE" = 1 ]; then
    echo "  The server is running in the background."
else
    echo "  Start the server:      scripts/run.sh"
    echo "  Or run it always-on:   scripts/install_service.sh"
fi
echo "  Dashboard:             http://localhost:8000"
echo "  Deep history (optional, slow): scripts/backfill_history.sh"
echo "  Add your own data sources:     plugins/README.md"
