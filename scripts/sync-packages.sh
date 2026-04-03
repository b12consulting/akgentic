#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
PACKAGES_DIR="$ROOT_DIR/packages"

skipped=()
synced=()
failed=()

for pkg in "$PACKAGES_DIR"/*/; do
    name="$(basename "$pkg")"

    # Skip if not a git repo (not a submodule)
    if [ ! -d "$pkg/.git" ] && [ ! -f "$pkg/.git" ]; then
        echo "⏭  $name — not a git repo, skipping"
        continue
    fi

    # Check for uncommitted changes
    if ! git -C "$pkg" diff --quiet || ! git -C "$pkg" diff --cached --quiet; then
        echo "⚠  $name — has uncommitted changes, skipping"
        skipped+=("$name")
        continue
    fi

    # Also check for untracked files
    if [ -n "$(git -C "$pkg" ls-files --others --exclude-standard)" ]; then
        echo "⚠  $name — has untracked files, skipping"
        skipped+=("$name")
        continue
    fi

    echo "🔄 $name — pulling master..."
    if git -C "$pkg" fetch origin master && git -C "$pkg" pull origin master --ff-only; then
        synced+=("$name")
        echo "✅ $name — synced"
    else
        failed+=("$name")
        echo "❌ $name — failed to sync"
    fi

    echo ""
done

echo "========================================="
echo "Summary:"
echo "  Synced:  ${#synced[@]} — ${synced[*]:-none}"
echo "  Skipped: ${#skipped[@]} — ${skipped[*]:-none}"
echo "  Failed:  ${#failed[@]} — ${failed[*]:-none}"
echo "========================================="

if [ ${#failed[@]} -gt 0 ]; then
    exit 1
fi
