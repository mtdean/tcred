#!/usr/bin/env bash
# Rebuild the gh-pages branch as a fresh static snapshot of the dashboard.
# Run from the repo root with the backend up at http://localhost:8000.
# scripts/com.situationmonitor.publish.plist.template runs it daily at 07:00.
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

# Kick every refresh job via POST /api/jobs/run/{id} (the hand-wired jobs plus
# every pluggable source from /api/sources), then wait for them all so the
# snapshot below captures fresh data. Failures are non-fatal — a stale
# snapshot beats no snapshot.
CORE_JOBS="feeds market fred edgar bdc regulatory"

refresh_all() {
  local sources jobs job run_id
  sources=$(curl -sf --max-time 10 "$API/sources" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print(" ".join(s["id"] for s in (d["sources"] if isinstance(d, dict) else d)
               if s.get("enabled", True)))' 2>/dev/null) \
    || { echo "  ! could not list sources (server down?)"; return 0; }
  jobs="$CORE_JOBS $sources"

  local run_ids=()
  for job in $jobs; do
    run_id=$(curl -sf --max-time 10 -X POST "$API/jobs/run/$job" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])' \
      2>/dev/null) || { echo "  ! could not start $job"; continue; }
    echo "  … $job running (run $run_id)"
    run_ids+=("$job:$run_id")
  done

  local deadline=$(( $(date +%s) + 1800 ))  # up to 30 min for the slowest job
  local pending=("${run_ids[@]}")
  while [ "${#pending[@]}" -gt 0 ] && [ "$(date +%s)" -lt "$deadline" ]; do
    sleep 10
    local still=()
    for entry in "${pending[@]}"; do
      local status
      status=$(curl -sf --max-time 10 "$API/jobs/run/${entry#*:}" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' \
        2>/dev/null || echo running)
      if [ "$status" = "running" ]; then
        still+=("$entry")
      else
        echo "  ✓ ${entry%%:*}: $status"
      fi
    done
    pending=("${still[@]+"${still[@]}"}")
  done
  for entry in "${pending[@]+"${pending[@]}"}"; do
    echo "  ! ${entry%%:*} still running after 30 min — snapshotting anyway"
  done
}

if [ "$REFRESH" = "1" ]; then
  echo "→ refreshing every data source before snapshot"
  refresh_all
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
  # Push over HTTPS with gh's stored token: the launchd daily run has no tty
  # to unlock an SSH key, and the SSH push hung there.
  git -c credential.helper= -c credential.helper='!gh auth git-credential' \
    push "https://github.com/$(git remote get-url origin | sed -E 's#^(git@github.com:|https://github.com/)##')" gh-pages
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
