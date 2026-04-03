#!/usr/bin/env bash
set -euo pipefail

GITHUB_ORG="b12consulting"
WORKFLOW="ci.yml"

usage() {
    echo "Usage: $0 <package-name> [--ref <branch>]"
    echo ""
    echo "Trigger CI workflow for a specific akgentic package."
    echo ""
    echo "Arguments:"
    echo "  package-name   Package name (e.g. akgentic-llm, akgentic-core)"
    echo "  --ref          Branch/tag to run against (default: master)"
    echo ""
    echo "Examples:"
    echo "  $0 akgentic-llm"
    echo "  $0 akgentic-agent --ref feat/42-new-feature"
    exit 1
}

if [ $# -lt 1 ]; then
    usage
fi

PACKAGE="$1"
shift
REF="master"

while [ $# -gt 0 ]; do
    case "$1" in
        --ref)
            REF="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            usage
            ;;
    esac
done

# Validate package exists
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
if [ ! -d "$ROOT_DIR/packages/$PACKAGE" ]; then
    echo "Error: package '$PACKAGE' not found in packages/"
    echo "Available packages:"
    ls "$ROOT_DIR/packages/"
    exit 1
fi

REPO="$GITHUB_ORG/$PACKAGE"

echo "Triggering CI for $REPO on ref '$REF'..."
gh workflow run "$WORKFLOW" -R "$REPO" --ref "$REF"
echo "Done. Check status with: gh run list -R $REPO -w $WORKFLOW --limit 1"
