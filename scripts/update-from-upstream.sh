#!/usr/bin/env bash
set -euo pipefail

# Nexus Chat Server — Update from Upstream Script
# Pulls latest changes from upstream nexus-chat and re-applies server patches.
#
# Flow:
#   1. Fetch upstream/main
#   2. Reset patched files to upstream versions (undo server patch)
#   3. Merge upstream/main (now conflict-free for patched files)
#   4. Re-apply server patch with --3way for safe merging
#   5. Commit the result

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PATCH_DIR="$ROOT_DIR/scripts/server-patches"
PATCH_FILE="$PATCH_DIR/nexus-chat-server.patch"

cd "$ROOT_DIR"

if [ ! -f "$PATCH_FILE" ]; then
    echo "[ERROR] Patch file not found: $PATCH_FILE"
    exit 1
fi

# Ensure upstream remote exists
if ! git remote get-url upstream >/dev/null 2>&1; then
    echo "[INFO] Adding upstream remote..."
    git remote add upstream https://github.com/aashiquear/nexus-chat.git
fi

echo "[INFO] Fetching upstream/main..."
git fetch upstream main

# Check if there's anything new
UPSTREAM_HEAD=$(git rev-parse upstream/main)
MERGE_BASE=$(git merge-base HEAD upstream/main 2>/dev/null || echo "none")

if [ "$UPSTREAM_HEAD" = "$MERGE_BASE" ]; then
    echo "[INFO] Already up to date with upstream/main."
    echo "       To re-apply server patches anyway, run: scripts/apply-server-patches.sh"
    exit 0
fi

# Abort if working tree is dirty
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "[ERROR] Working tree has uncommitted changes. Commit or stash them first."
    exit 1
fi

# Step 1: Undo the server patch by resetting patched files to upstream baseline
echo "[INFO] Resetting server-patched files to upstream versions..."
PATCHED_FILES=$(grep "^diff --git a/" "$PATCH_FILE" | sed 's|diff --git a/||' | sed 's| b/.*||')
for file in $PATCHED_FILES; do
    if git show upstream/main:"$file" >/dev/null 2>&1; then
        # File exists upstream — restore it
        git show upstream/main:"$file" > "$file"
    else
        # New server-only file — remove so merge doesn't conflict
        rm -f "$file"
    fi
done

git add -A
git diff --cached --quiet || git commit -m "Temporarily revert server patches for upstream merge" --quiet

# Step 2: Merge upstream (should be clean now since we reverted our patches)
echo "[INFO] Merging upstream/main..."
if ! git merge upstream/main --no-edit; then
    echo "[ERROR] Merge conflict. Resolve manually, commit, then run:"
    echo "        scripts/apply-server-patches.sh"
    exit 1
fi

# Step 3: Re-apply server patches with 3-way merge
echo "[INFO] Re-applying server patches..."
if git apply --3way "$PATCH_FILE"; then
    echo "[INFO] Server patch applied cleanly."
else
    echo "[WARN] Patch had conflicts. Look for <<<<<<< markers and resolve."
    echo "       After resolving: git add -A && git commit -m 'Re-apply server patches after upstream merge'"
    exit 1
fi

# Step 4: Commit
echo "[INFO] Staging and committing..."
git add -A
if git diff --cached --quiet; then
    echo "[INFO] No additional changes after merge + patch."
else
    git commit -m "Re-apply server patches after upstream merge"
fi

echo ""
echo "[SUCCESS] Upstream merged and server patches applied."
echo ""
echo "If the patch needs updating (new server-only changes since last regen):"
echo "  git diff upstream/main -- . ':!scripts' ':!.claude' ':!.gitignore' > $PATCH_FILE"
echo ""
echo "Deploy:"
echo "  docker compose build && docker compose up -d"
