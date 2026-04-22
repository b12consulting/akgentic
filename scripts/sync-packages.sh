#!/bin/bash

PARENT_DIR="packages"
BRANCH="master"

echo "--- Syncing packages on $BRANCH ---"

# Iterate over each directory in packages/
for dir in "$PARENT_DIR"/*/; do
    PKG=$(basename "$dir")

    # Check whether a remote matching the directory name exists
    if git remote | grep -q "^$PKG$"; then
        echo "🔄 Updating $PKG..."
        git fetch "$PKG" "$BRANCH"
        git subtree pull --prefix "$PARENT_DIR/$PKG" "$PKG" "$BRANCH" --squash -m "chore(release): sync $PKG from $BRANCH"
    else
        echo "⚠️  No remote found for $PKG, skipping."
    fi
done

echo "--- Sync complete. Your packages are up to date. ---"