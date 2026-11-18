#!/bin/bash
# situation-monitor/scripts/install_service.sh
# Install the backend as an always-on service for the current user.
#
#   macOS → launchd agent  (~/Library/LaunchAgents/com.situationmonitor.server.plist)
#           + a daily 07:00 gh-pages publish (com.situationmonitor.publish.plist)
#   Linux → systemd user unit (~/.config/systemd/user/situation-monitor.service)
#
# Both auto-start on login and restart the server if it crashes.
#
# Usage:
#   scripts/install_service.sh              # install (or reinstall) + start
#   scripts/install_service.sh --uninstall  # stop + remove
#   PORT=8080 scripts/install_service.sh    # non-default port

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PORT="${PORT:-8000}"
PYTHON="$PROJECT_ROOT/backend/.venv/bin/python"

LABEL="com.situationmonitor.server"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PUBLISH_LABEL="com.situationmonitor.publish"
PUBLISH_PLIST="$HOME/Library/LaunchAgents/$PUBLISH_LABEL.plist"
UNIT_NAME="situation-monitor.service"
UNIT="$HOME/.config/systemd/user/$UNIT_NAME"

uninstall() {
    case "$(uname -s)" in
        Darwin)
            launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
            launchctl bootout "gui/$(id -u)/$PUBLISH_LABEL" 2>/dev/null || true
            rm -f "$PLIST" "$PUBLISH_PLIST"
            echo "Removed $PLIST $PUBLISH_PLIST" ;;
        Linux)
            systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
            rm -f "$UNIT"
            systemctl --user daemon-reload
            echo "Removed $UNIT" ;;
    esac
}

if [ "${1:-}" = "--uninstall" ]; then
    uninstall
    exit 0
fi

if [ ! -x "$PYTHON" ]; then
    echo "ERROR: $PYTHON not found — run scripts/setup.sh first." >&2
    exit 1
fi
mkdir -p "$PROJECT_ROOT/logs"

case "$(uname -s)" in
    Darwin)
        mkdir -p "$(dirname "$PLIST")"
        # '|' delimiter: the project path contains slashes.
        sed -e "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" -e "s|__PORT__|$PORT|g" \
            "$SCRIPT_DIR/$LABEL.plist.template" > "$PLIST"
        # bootout first so a reinstall picks up the new plist.
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        launchctl bootstrap "gui/$(id -u)" "$PLIST"
        echo "Installed launchd agent: $PLIST"
        echo "  Restart: launchctl kickstart -k gui/$(id -u)/$LABEL"
        echo "  Logs:    $PROJECT_ROOT/logs/server.err.log"

        sed -e "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" -e "s|__PATH__|$PATH|g" \
            "$SCRIPT_DIR/$PUBLISH_LABEL.plist.template" > "$PUBLISH_PLIST"
        launchctl bootout "gui/$(id -u)/$PUBLISH_LABEL" 2>/dev/null || true
        launchctl bootstrap "gui/$(id -u)" "$PUBLISH_PLIST"
        echo "Installed daily 07:00 gh-pages publish: $PUBLISH_PLIST"
        echo "  Run now: launchctl kickstart gui/$(id -u)/$PUBLISH_LABEL"
        echo "  Logs:    $PROJECT_ROOT/logs/publish.log"
        ;;
    Linux)
        mkdir -p "$(dirname "$UNIT")"
        cat > "$UNIT" <<EOF
[Unit]
Description=Situation Monitor server
After=network-online.target

[Service]
WorkingDirectory=$PROJECT_ROOT/backend
ExecStart=$PYTHON -m uvicorn main:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=5
LimitNOFILE=4096
StandardOutput=append:$PROJECT_ROOT/logs/server.out.log
StandardError=append:$PROJECT_ROOT/logs/server.err.log

[Install]
WantedBy=default.target
EOF
        systemctl --user daemon-reload
        systemctl --user enable --now "$UNIT_NAME"
        echo "Installed systemd user unit: $UNIT"
        echo "  Restart: systemctl --user restart $UNIT_NAME"
        echo "  Logs:    $PROJECT_ROOT/logs/server.err.log"

        sed -e "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" -e "s|__PATH__|$PATH|g" \
            "$SCRIPT_DIR/$PUBLISH_LABEL.plist.template" > "$PUBLISH_PLIST"
        launchctl bootout "gui/$(id -u)/$PUBLISH_LABEL" 2>/dev/null || true
        launchctl bootstrap "gui/$(id -u)" "$PUBLISH_PLIST"
        echo "Installed daily 07:00 gh-pages publish: $PUBLISH_PLIST"
        echo "  Run now: launchctl kickstart gui/$(id -u)/$PUBLISH_LABEL"
        echo "  Logs:    $PROJECT_ROOT/logs/publish.log"
        echo "  (To keep it running while logged out: loginctl enable-linger \$USER)"
        ;;
    *)
        echo "Unsupported OS $(uname -s) — run scripts/run.sh in a terminal instead." >&2
        exit 1
        ;;
esac

echo "Server: http://localhost:$PORT"
