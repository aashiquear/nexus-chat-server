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
echo "          Run 'docker compose build' and 'docker compose up -d' to deploy."
