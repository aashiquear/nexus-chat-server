#!/usr/bin/env bash
set -euo pipefail

# Nexus Chat Server — Update from Upstream Script
# Pulls latest changes from upstream main and re-applies server patches.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PATCH_DIR="$ROOT_DIR/scripts/server-patches"

cd "$ROOT_DIR"

# Ensure upstream remote exists
if ! git remote get-url upstream >/dev/null 2>&1; then
    echo "[INFO] Adding upstream remote..."
    git remote add upstream git@github.com:aashiquear/nexus-chat.git
fi

echo "[INFO] Fetching upstream/main..."
git fetch upstream main

# Warn if there are server-side commits not yet captured as a patch
LAST_PATCH_COMMIT=$(git log --oneline --grep="Re-apply server patches" -1 | awk '{print $1}')
if [ -n "$LAST_PATCH_COMMIT" ]; then
    UNPATCHED=$(git log --oneline "$LAST_PATCH_COMMIT"..HEAD -- . ':!scripts/server-patches' | wc -l)
else
    UNPATCHED=$(git log --oneline 553e80c..HEAD -- . ':!scripts/server-patches' | wc -l)
fi
if [ "$UNPATCHED" -gt 0 ]; then
    echo "[WARN] $UNPATCHED server-side commit(s) since the last patch re-apply are not in $PATCH_DIR/nexus-chat-server.patch."
    echo "       Consider regenerating the patch before merging:"
    echo "       git diff 553e80c..HEAD -- . ':!scripts/server-patches' > $PATCH_DIR/nexus-chat-server.patch"
fi

echo "[INFO] Merging upstream/main into current branch (preferring upstream changes)..."
if ! git merge -X theirs upstream/main --no-edit; then
    echo "[ERROR] Merge conflict detected. Please resolve manually and re-run."
    exit 1
fi

echo "[INFO] Re-applying server patches..."
for patch in "$PATCH_DIR"/*.patch; do
    if [ -f "$patch" ]; then
        echo "[INFO] Applying $(basename "$patch")..."
        if ! git apply "$patch"; then
            echo "[WARN] Patch $(basename "$patch") failed to apply. Skipping."
        fi
    fi
done

echo "[INFO] Staging patch-applied changes..."
git add -A
git diff --cached --quiet || git commit -m "Re-apply server patches after upstream merge" || true

echo "[SUCCESS] Upstream updates merged and server patches applied."
echo "          If new server-only changes were made since the last update, regenerate the patch:"
echo "          git diff 553e80c..HEAD -- . ':!scripts/server-patches' > $PATCH_DIR/nexus-chat-server.patch"
echo "          Run 'docker compose build' and 'docker compose up -d' to deploy."
