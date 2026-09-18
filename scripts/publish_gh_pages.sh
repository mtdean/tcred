#!/usr/bin/env bash
# Rebuild the gh-pages branch as a fresh static snapshot of the dashboard.
# Run from the repo root with the backend up at http://localhost:8000.
#
#   ./scripts/publish_gh_pages.sh              # refresh data + rebuild + commit
#   ./scripts/publish_gh_pages.sh --push       # …and push to origin
#   ./scripts/publish_gh_pages.sh --no-refresh # snapshot whatever the DB has now
#
# The first run creates the worktree at /tmp/tcred-gh-pages and an orphan
# gh-pages branch. Subsequent runs reuse the existing worktree.

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORKTREE=/tmp/tcred-gh-pages
BASE_PATH=/tcred/
API=http://localhost:8000/api
PUSH=0
REFRESH=1

for arg in "$@"; do
  case "$arg" in
    --push) PUSH=1 ;;
    --no-refresh) REFRESH=0 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

cd "$REPO_ROOT"

# Kick a background job via POST /api/jobs/run/{id} and wait for it to finish,
# so the snapshot below captures fresh data. Failures are non-fatal — a stale
# snapshot beats no snapshot.
refresh_job() {
  local job="$1"
  local run_id
  run_id=$(curl -sf --max-time 10 -X POST "$API/jobs/run/$job" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])' \
    2>/dev/null) || { echo "  ! could not start $job refresh (server down?)"; return 0; }
  echo "  … $job refresh running (run $run_id)"
  for _ in $(seq 1 120); do  # up to 10 min
    sleep 5
    local status
    status=$(curl -sf --max-time 10 "$API/jobs/run/$run_id" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' \
      2>/dev/null || echo running)
    if [ "$status" != "running" ]; then
      echo "  ✓ $job refresh: $status"
      return 0
    fi
  done
  echo "  ! $job refresh still running after 10 min — snapshotting anyway"
}

if [ "$REFRESH" = "1" ]; then
  echo "→ refreshing news + macro data before snapshot"
  refresh_job feeds
  refresh_job fred
fi

echo "→ snapshotting API to frontend/public/api-snapshot/"
python3 scripts/snapshot_api.py

echo "→ building static frontend (base=$BASE_PATH)"
(
  cd frontend
  VITE_STATIC_MODE=true VITE_BASE="$BASE_PATH" npm run build
  cp index.html "$REPO_ROOT/frontend/dist/404.html"
  touch dist/.nojekyll
)

if [ ! -d "$WORKTREE/.git" ] && [ ! -f "$WORKTREE/.git" ]; then
  echo "→ creating orphan gh-pages worktree at $WORKTREE"
  git worktree add --orphan -b gh-pages "$WORKTREE"
fi

echo "→ syncing dist → $WORKTREE"
# Wipe existing tracked files but preserve .git and .gitallowed.
find "$WORKTREE" -mindepth 1 -maxdepth 1 \
  -not -name '.git' -not -name '.gitallowed' -exec rm -rf {} +
cp -R frontend/dist/. "$WORKTREE/"

# Keep .gitallowed so git-secrets doesn't trip on the minified bundle.
if [ ! -f "$WORKTREE/.gitallowed" ]; then
  printf '.*\n' > "$WORKTREE/.gitallowed"
fi

cd "$WORKTREE"
git add -A
if git diff --cached --quiet; then
  echo "→ no changes to commit"
else
  STAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  git commit -m "build: refresh static snapshot ($STAMP)"
  echo "→ committed"
fi

if [ "$PUSH" = "1" ]; then
  echo "→ pushing to origin/gh-pages"
  git push origin gh-pages
fi

# Restore frontend/dist to a local-mode build so the FastAPI server at
# localhost:8000 keeps working (the gh-pages build bakes a /tcred/ base path
# into index.html, which breaks local serving).
echo "→ restoring local-mode dist for FastAPI serving"
(
  cd "$REPO_ROOT/frontend"
  npm run build >/dev/null
)

echo "done — gh-pages at $(cd "$WORKTREE" && git rev-parse --short HEAD)"
