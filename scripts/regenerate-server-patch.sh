#!/usr/bin/env bash
set -euo pipefail

# Nexus Chat Server — Regenerate the server patch
# Diffs the current tree against the upstream ref and stores the result as
# the server patch that update-from-upstream.sh re-applies after merges.
# Run this after changing server-only code (auth, per-user scoping, admin
# UI, settings.server.yaml, …) and commit the refreshed patch.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PATCH_FILE="$ROOT_DIR/scripts/server-patches/nexus-chat-server.patch"
UPSTREAM_REF="${UPSTREAM_REF:-upstream/main}"
UPSTREAM_BRANCH="${UPSTREAM_REF#upstream/}"

cd "$ROOT_DIR"

if ! git remote get-url upstream >/dev/null 2>&1; then
    git remote add upstream https://github.com/aashiquear/nexus-chat.git
fi
git fetch upstream "$UPSTREAM_BRANCH" --quiet

mkdir -p "$(dirname "$PATCH_FILE")"
# Exclude the patch machinery itself and local-only files.
git diff "$UPSTREAM_REF" -- . ':!scripts' ':!.claude' ':!.gitignore' ':!frontend/package-lock.json' > "$PATCH_FILE"

FILES=$(grep -c "^diff --git" "$PATCH_FILE" || true)
echo "[SUCCESS] Wrote $PATCH_FILE ($FILES files differ from $UPSTREAM_REF)"
echo "          Review with: git diff --stat $UPSTREAM_REF"
