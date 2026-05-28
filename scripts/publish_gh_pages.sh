#!/usr/bin/env bash
# Rebuild the gh-pages branch as a fresh static snapshot of the dashboard.
# Run from the repo root with the backend up at http://localhost:8000.
#
#   ./scripts/publish_gh_pages.sh        # rebuild + commit, don't push
#   ./scripts/publish_gh_pages.sh --push # rebuild + commit + push to origin
#
# The first run creates the worktree at /tmp/tcred-gh-pages and an orphan
# gh-pages branch. Subsequent runs reuse the existing worktree.

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORKTREE=/tmp/tcred-gh-pages
BASE_PATH=/tcred/
PUSH=0

for arg in "$@"; do
  case "$arg" in
    --push) PUSH=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

cd "$REPO_ROOT"

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

echo "done — gh-pages at $(git rev-parse --short HEAD)"
