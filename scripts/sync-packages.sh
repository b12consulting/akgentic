#!/bin/bash

ORG="b12consulting"
PARENT_DIR="packages"
BRANCH="master"

if [ -n "$(git status --porcelain)" ]; then
    echo "❌ Working tree has uncommitted changes. Commit or stash them before syncing." >&2
    git status --short >&2
    exit 1
fi

echo "--- Syncing packages on $BRANCH ---"

for dir in "$PARENT_DIR"/*/; do
    PKG=$(basename "$dir")
    REMOTE_URL="git@github.com:$ORG/$PKG.git"

    echo "🔄 Updating $PKG from $REMOTE_URL..."
    git subtree pull --prefix "$PARENT_DIR/$PKG" "$REMOTE_URL" "$BRANCH" --squash -m "chore(release): sync $PKG from $BRANCH"
done

echo "--- Sync complete. Your packages are up to date. ---"