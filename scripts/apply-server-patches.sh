#!/usr/bin/env bash
set -euo pipefail

# Nexus Chat Server — Apply Server Patches (without upstream pull)
# Re-applies the server patch to the current working tree.
# Use this for sanity-checking that the patch applies cleanly.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PATCH_DIR="$ROOT_DIR/scripts/server-patches"
PATCH_FILE="$PATCH_DIR/nexus-chat-server.patch"

cd "$ROOT_DIR"

if [ ! -f "$PATCH_FILE" ]; then
    echo "[ERROR] Patch file not found: $PATCH_FILE"
    exit 1
fi

# Check for uncommitted changes
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "[WARN] You have uncommitted changes. Stash or commit them first."
    echo "       Proceeding anyway — conflicts may be harder to resolve."
fi

echo "[INFO] Resetting tracked files to upstream/main baseline..."
if ! git remote get-url upstream >/dev/null 2>&1; then
    echo "[INFO] Adding upstream remote..."
    git remote add upstream https://github.com/aashiquear/nexus-chat.git
fi
git fetch upstream main --quiet

# Reset the patched files to their upstream versions first
PATCHED_FILES=$(grep "^diff --git a/" "$PATCH_FILE" | sed 's|diff --git a/||' | sed 's| b/.*||')
for file in $PATCHED_FILES; do
    if git show upstream/main:"$file" >/dev/null 2>&1; then
        git show upstream/main:"$file" > "$file"
    else
        # New file — remove it so the patch can create it
        rm -f "$file"
    fi
done

echo "[INFO] Applying server patch..."
if git apply --3way "$PATCH_FILE"; then
    echo "[SUCCESS] Server patch applied cleanly."
else
    echo "[WARN] Patch applied with conflicts. Check for <<<<<<< markers."
    echo "       Resolve them, then run: git add -A && git commit -m 'Re-apply server patches'"
    exit 1
fi

echo "[INFO] Staging patched files..."
git add -A
if git diff --cached --quiet; then
    echo "[INFO] No changes — patch already matches working tree."
else
    git commit -m "Re-apply server patches (sanity check)"
    echo "[SUCCESS] Server patches committed."
fi

echo ""
echo "Next steps:"
echo "  docker compose build"
echo "  docker compose up -d"
