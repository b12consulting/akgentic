#!/bin/bash

ORG="b12consulting"
PARENT_DIR="packages"
BRANCH="version-1.3.1"
DEFAULT_PACKAGES=(core llm tool agent catalog team infra)

usage() {
    cat <<EOF
Usage: $(basename "$0") [-h|--help] [PACKAGE ...]

Sync akgentic-* packages from branch '$BRANCH' using git subtree pull.

Arguments:
  PACKAGE             Short package name (without the 'akgentic-' prefix).
                      May be repeated. If omitted, the default list is used.

Default packages:
  ${DEFAULT_PACKAGES[*]}

Options:
  -h, --help          Show this help message and exit.

Examples:
  $(basename "$0")                    # sync the default package list
  $(basename "$0") core llm           # sync only akgentic-core and akgentic-llm
EOF
}

for arg in "$@"; do
    case "$arg" in
        -h|--help)
            usage
            exit 0
            ;;
    esac
done

if [ "$#" -gt 0 ]; then
    PACKAGES=("$@")
else
    PACKAGES=("${DEFAULT_PACKAGES[@]}")
fi

if [ -n "$(git status --porcelain)" ]; then
    echo "❌ Working tree has uncommitted changes. Commit or stash them before syncing." >&2
    git status --short >&2
    exit 1
fi

echo "--- Syncing packages on $BRANCH ---"

for name in "${PACKAGES[@]}"; do
    PKG="akgentic-$name"
    if [ ! -d "$PARENT_DIR/$PKG" ]; then
        echo "⚠️  Skipping $PKG: directory $PARENT_DIR/$PKG does not exist." >&2
        continue
    fi
    REMOTE_URL="git@github.com:$ORG/$PKG.git"

    echo "🔄 Updating $PKG from $REMOTE_URL..."
    git subtree pull --prefix "$PARENT_DIR/$PKG" "$REMOTE_URL" "$BRANCH" --squash -m "chore(release): sync $PKG from $BRANCH"
done

echo "--- Sync complete. Your packages are up to date. ---"