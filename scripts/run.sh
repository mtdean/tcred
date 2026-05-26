#!/bin/bash
# situation-monitor/scripts/run.sh
# Convenience wrapper to start the backend server.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"

echo "Starting Situation Monitor..."
echo "Project root: $PROJECT_ROOT"

# Activate venv
if [ -f "$BACKEND_DIR/.venv/bin/activate" ]; then
    source "$BACKEND_DIR/.venv/bin/activate"
else
    echo "ERROR: No .venv found. Run:"
    echo "  cd $BACKEND_DIR && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Check .env
if [ ! -f "$PROJECT_ROOT/.env" ]; then
    echo "WARNING: .env not found. Copy .env.example and fill in keys."
fi

# Find LAN IP
LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || echo "unknown")
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Local:  http://localhost:8000"
echo "  iPad:   http://$LAN_IP:8000"
echo "  API docs: http://localhost:8000/docs"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

cd "$BACKEND_DIR"
python main.py
